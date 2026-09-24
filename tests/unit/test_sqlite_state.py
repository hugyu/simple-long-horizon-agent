from __future__ import annotations

import gc
import os
import signal
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from simple_long_horizon_agent import (
    Agent,
    ContextCompressionEvent,
    ContextPolicy,
    FileEventJournal,
    FileRunStore,
    LeaseLostError,
    RecoverableRunExecutor,
    State,
    assistant_message,
)
from simple_long_horizon_agent.compression import maybe_compress_context
from simple_long_horizon_agent.context_view import CompressionDecision
from simple_long_horizon_agent.messages import text_of
from simple_long_horizon_agent.protocols import TurnStartEvent
from simple_long_horizon_agent.sqlite_state import SqliteCheckpointStore, StateLimits
from simple_long_horizon_agent.state import StateResourceLimitError
from simple_long_horizon_agent.tools import make_recall_tool, tool_result_text


class SqliteStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = SqliteCheckpointStore(self.root)

    def test_recall_after_compression_and_restart_without_latest_checkpoint(
        self,
    ) -> None:
        state = self.store.create("recall", "task")
        state.send("task", "user", "worker", "task")
        state.send("message", "user", "worker", "secret is 9931")
        self.store.save("recall", state)

        def compress(active, name):
            return CompressionDecision(
                compress_indices=(1,),
                replacement=assistant_message("old detail omitted", kind="summary"),
            )

        maybe_compress_context(
            Agent("worker", lambda visible: assistant_message("done")),
            state,
            ContextPolicy(strategy=compress),
        )
        expected_events = list(state.events)
        # No save after compression: committed event/projection tail is durable.
        restored = self.store.load("recall")
        self.assertEqual(list(restored.events), expected_events)
        self.assertEqual(restored.snapshot.active_context_indices, [0, 2])
        self.assertNotIn(
            "secret",
            "".join(text_of(m.content) for m in restored.active_context_messages()),
        )
        recalled = make_recall_tool(restored).execute(
            "r", {"indices": [1]}, lambda: False, None
        )
        self.assertIn("secret is 9931", tool_result_text(recalled))
        old_view = restored.messages
        restored.send("message", "user", "worker", "new")
        self.assertEqual(len(old_view), 3)
        self.assertEqual(len(restored.messages), 4)
        self.assertEqual(
            text_of(old_view[-1].content), text_of(state.messages[2].content)
        )
        self.assertFalse(isinstance(restored.events[:], list))

    def test_failed_append_rolls_back_event_message_and_projection(self) -> None:
        store = SqliteCheckpointStore(
            self.root, limits=StateLimits(max_active_messages=2)
        )
        state = store.create("quota", "task")
        state.send("task", "user", "worker", "task")
        state.send("message", "user", "worker", "first")
        with self.assertRaisesRegex(StateResourceLimitError, "active messages"):
            state.send("message", "user", "worker", "rejected")
        restored = store.load("quota")
        self.assertEqual(len(restored.events), 2)
        self.assertEqual(len(restored.messages), 2)
        self.assertEqual(restored.snapshot.active_context_indices, [0, 1])
        restored.record_event(TurnStartEvent(agent="worker"))
        self.assertEqual(restored.events[-1].index, 2)
        with self.assertRaises(ValueError):
            restored.record_event(
                ContextCompressionEvent(
                    agent="worker",
                    summary_message_index=1,
                    compressed_message_indices=[],
                    active_context_indices=[999],
                    before_tokens=1,
                    after_tokens=1,
                )
            )
        self.assertEqual(store.load("quota").snapshot.active_context_indices, [0, 1])
        self.assertEqual(len(store.load("quota").events), 3)

    def test_event_history_and_metadata_budgets(self) -> None:
        limits = StateLimits(
            max_event_bytes=600, max_history_bytes=900, max_metadata_bytes=200
        )
        store = SqliteCheckpointStore(self.root, limits=limits)
        state = store.create("limits", "task")
        with self.assertRaisesRegex(StateResourceLimitError, "event bytes"):
            state.send("message", "user", "w", "x" * 1000)
        self.assertEqual(len(state.events), 0)
        for _ in range(20):
            try:
                state.record_event(TurnStartEvent(agent="w"))
            except StateResourceLimitError as exc:
                self.assertIn("history bytes", str(exc))
                break
        else:
            self.fail("history limit was not enforced")
        self.assertEqual(len(state.events), len(store.load("limits").events))
        state.data["large"] = "x" * 300
        with self.assertRaisesRegex(StateResourceLimitError, "metadata bytes"):
            store.save("limits", state)
        self.assertNotIn("large", store.load("limits").data)

    def test_stale_writer_cannot_reuse_event_index_or_overwrite_metadata(self) -> None:
        first = self.store.create("shared", "task")
        stale = self.store.load("shared")
        first.send("message", "user", "w", "committed")
        with self.assertRaisesRegex(ValueError, "expected event index"):
            stale.send("message", "user", "w", "stale")
        with self.assertRaisesRegex(ValueError, "stale state"):
            self.store.save("shared", stale)
        self.assertEqual(
            text_of(self.store.load("shared").messages[0].content), "committed"
        )

    def test_import_existing_in_memory_state_and_preserve_metadata(self) -> None:
        original = State("task")
        original.data["attempts"] = 2
        original.send("task", "user", "w", "task")
        original.send("message", "user", "w", "fact")
        self.store.save("imported", original)
        restored = self.store.load("imported")
        self.assertEqual(list(restored.events), original.events)
        self.assertEqual(restored.data, original.data)
        with self.assertRaises(FileExistsError):
            self.store.save("imported", original)

    def test_long_history_and_restore_have_bounded_python_memory(self) -> None:
        state = self.store.create("long", "task")
        state.send("task", "user", "w", "task")
        tracemalloc.start()
        try:
            for i in range(800):
                state.send("message", "user", "w", f"fact-{i}:" + "x" * 16000)
                state.record_event(
                    ContextCompressionEvent(
                        agent="w",
                        summary_message_index=0,
                        compressed_message_indices=[len(state.messages) - 1],
                        active_context_indices=[0],
                        before_tokens=4000,
                        after_tokens=1,
                    )
                )
            gc.collect()
            retained, peak = tracemalloc.get_traced_memory()
            self.assertLess(retained, 1024 * 1024)
            self.assertLess(peak, 3 * 1024 * 1024)
        finally:
            tracemalloc.stop()
        self.assertGreater(self.store.path_for("long").stat().st_size, 12 * 1024 * 1024)
        from simple_long_horizon_agent.sqlite_state import _event_from_record

        with patch(
            "simple_long_horizon_agent.sqlite_state._event_from_record",
            wraps=_event_from_record,
        ) as decode:
            restored = self.store.load("long")
            self.assertEqual(len(restored.messages), 801)
            self.assertEqual(restored.snapshot.active_context_indices, [0])
            self.assertLessEqual(decode.call_count, 1)
        self.assertIn("fact-0:", text_of(restored.messages[1].content))
        self.assertIn("fact-799:", text_of(restored.messages[800].content))

    @unittest.skipUnless(os.name == "posix", "SIGKILL requires POSIX")
    def test_sigkill_rolls_back_partial_transaction_and_preserves_committed_tail(
        self,
    ) -> None:
        self.store.create("crash", "task")
        code = """
import os, signal, sqlite3, sys
from simple_long_horizon_agent.sqlite_state import SqliteCheckpointStore
store = SqliteCheckpointStore(sys.argv[1])
state = store.load('crash')
state.send('message', 'user', 'w', 'committed before kill')
db = sqlite3.connect(store.path_for('crash'))
db.execute('BEGIN IMMEDIATE')
db.execute("INSERT INTO events VALUES (1, 'incomplete')")
db.execute('UPDATE state SET event_count=2 WHERE id=1')
os.kill(os.getpid(), signal.SIGKILL)
"""
        child = subprocess.run(
            [sys.executable, "-c", code, str(self.root)],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(child.returncode, -signal.SIGKILL, child.stderr.decode())
        restored = self.store.load("crash")
        self.assertEqual(len(restored.events), 1)
        self.assertEqual(text_of(restored.messages[0].content), "committed before kill")
        restored.send("message", "user", "w", "after restart")
        self.assertEqual(restored.events[-1].index, 1)

    def test_executor_uses_sqlite_and_fences_old_state_writes(self) -> None:
        now = [0.0]
        run_store = FileRunStore(self.root / "runs", clock=lambda: now[0])
        executor = RecoverableRunExecutor(
            run_store=run_store, checkpoint_store=self.store
        )
        handle = executor.create("run", State("task"), worker_id="old")
        agent = Agent(
            "w", lambda visible: assistant_message("done", sender="w", kind="final")
        )
        old_state, old_events = executor.execute(handle, agent)
        now[0] = 100.0
        _, events = executor.execute(replace(handle, worker_id="new"), agent)
        with self.assertRaises(LeaseLostError):
            list(old_events)
        before = len(self.store.load("run").events)
        with self.assertRaises(LeaseLostError):
            old_state.send("message", "user", "w", "late write")
        self.assertEqual(len(self.store.load("run").events), before)
        list(events)
        self.assertEqual(run_store.get("run").status, "finished")

    def test_executor_stops_on_resource_budget(self) -> None:
        store = SqliteCheckpointStore(
            self.root, limits=StateLimits(max_event_bytes=1500)
        )
        run_store = FileRunStore(self.root / "runs")
        executor = RecoverableRunExecutor(run_store=run_store, checkpoint_store=store)
        handle = executor.create("stop", State("task"), worker_id="worker")
        agent = Agent(
            "w", lambda visible: assistant_message("x" * 3000, sender="w", kind="final")
        )
        _, events = executor.execute(handle, agent)
        with self.assertRaises(StateResourceLimitError):
            list(events)
        self.assertEqual(run_store.get("stop").status, "budget_exhausted")
        self.assertFalse(
            any("x" * 3000 in text_of(m.content) for m in store.load("stop").messages)
        )

    def test_agent_loop_compresses_and_recalls_from_disk(self) -> None:
        from simple_long_horizon_agent import ToolCallBlock, ToolCompactStrategy, run
        from simple_long_horizon_agent.protocols import ModelRequestEvent
        from simple_long_horizon_agent.tools import AgentTool, text_result

        state = self.store.create("loop", "task")
        state.send("task", "user", "w", "read reports")
        turns = 0

        def generate(visible):
            nonlocal turns
            turns += 1
            if turns > 12:
                return assistant_message("done", sender="w", kind="final")
            return assistant_message(
                [ToolCallBlock(f"read-{turns}", "report", {})],
                sender="w",
                kind="step",
            )

        tool = AgentTool(
            name="report",
            description="Read report",
            parameters={"type": "object", "properties": {}},
            execute=lambda call_id, args, abort, update: text_result(
                "fact:" + "x" * 16000
            ),
        )
        agent = Agent(
            "w",
            generate,
            tools=(tool, make_recall_tool(state)),
            context_policy=ContextPolicy(
                strategy=ToolCompactStrategy(threshold_tokens=1000)
            ),
        )
        for _ in run(agent, state, max_turns=20):
            pass
        restored = self.store.load("loop")
        self.assertEqual(restored.events[-1].reason, "done")
        self.assertLess(len(restored.active_context_messages()), len(restored.messages))
        request = next(
            e for e in reversed(restored.events) if isinstance(e, ModelRequestEvent)
        )
        self.assertTrue(
            all(hasattr(message, "role") for message in request.llm_payload)
        )
        recalled = make_recall_tool(restored).execute(
            "r", {"indices": [2]}, lambda: False, None
        )
        self.assertIn("fact:", tool_result_text(recalled))

    def test_database_file_limit_rolls_back_cleanly(self) -> None:
        store = SqliteCheckpointStore(
            self.root, limits=StateLimits(max_database_bytes=65536)
        )
        state = store.create("disk", "task")
        for _ in range(100):
            try:
                state.record_event(TurnStartEvent(agent="w" * 3000))
            except StateResourceLimitError:
                break
        else:
            self.fail("SQLite page budget was not enforced")
        self.assertLessEqual(store.path_for("disk").stat().st_size, 65536)
        self.assertEqual(len(store.load("disk").events), len(state.events))

    def test_restore_resource_limit_releases_lease(self) -> None:
        store = SqliteCheckpointStore(
            self.root, limits=StateLimits(max_active_bytes=10)
        )
        run_store = FileRunStore(self.root / "runs")
        executor = RecoverableRunExecutor(run_store=run_store, checkpoint_store=store)
        handle = executor.create("restore-budget", State("task"), worker_id="worker")
        agent = Agent("w", lambda visible: assistant_message("done"))
        with self.assertRaises(StateResourceLimitError):
            executor.execute(handle, agent)
        self.assertEqual(run_store.get("restore-budget").status, "budget_exhausted")

    def test_parallel_recall_uses_independent_connections(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        state = self.store.create("parallel", "task")
        state.send("message", "user", "w", "shared fact")
        recall = make_recall_tool(state)

        def read(index):
            return tool_result_text(
                recall.execute(str(index), {"indices": [0]}, lambda: False, None)
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            outputs = list(pool.map(read, range(16)))
        self.assertTrue(all("shared fact" in text for text in outputs))

    def test_separate_journal_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "omit event_journal"):
            RecoverableRunExecutor(
                run_store=FileRunStore(self.root / "runs"),
                checkpoint_store=self.store,
                event_journal=FileEventJournal(self.root / "journal"),
            )

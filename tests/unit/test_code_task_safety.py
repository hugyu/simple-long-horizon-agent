"""Regression checks for recoverable coding, not only happy-path tool use."""

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from simple_long_horizon_agent import (
    Agent,
    State,
    FileRunStore,
    SqliteCheckpointStore,
    RecoverableRunExecutor,
    RecoverableRuntimeService,
    ContextPolicy,
    ToolCompactStrategy,
    ToolCallBlock,
    assistant_message,
)
from simple_long_horizon_agent.agents.code_task import (
    repository_guidance,
    make_task_status_tool,
    delivery_review,
)
from simple_long_horizon_agent.completion import CompletionResult
from simple_long_horizon_agent.context_view import estimate_context_tokens
from simple_long_horizon_agent.core import _execute_one
from simple_long_horizon_agent.run_control import FileOperationLedger
from simple_long_horizon_agent.tools import AgentTool, text_result
from simple_long_horizon_agent.tools.bash import make_bash_tool, run_bash
from scripts.run_long_task import initialize, verifier


class CodeTaskSafetyTest(unittest.TestCase):
    def test_failed_shell_command_can_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = FileOperationLedger(root / "ops")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=SqliteCheckpointStore(root / "state"),
                operation_ledger=ledger,
                completion_check=lambda state: CompletionResult(False),
            )
            handle = executor.create("task", State("repair"), worker_id="worker")
            outputs = iter(
                [
                    assistant_message(
                        [ToolCallBlock("test", "bash", {"command": "exit 1"})],
                        sender="worker",
                        kind="step",
                    ),
                    assistant_message("continue", sender="worker", kind="final"),
                    assistant_message("retry", sender="worker", kind="final"),
                ]
            )
            agent = Agent(
                "worker",
                lambda visible: next(outputs),
                tools=(make_bash_tool(cwd=root),),
            )
            for _ in range(2):
                _, events = executor.execute(handle, agent)
                list(events)
            self.assertEqual(ledger.list_pending("task"), [])
            self.assertEqual(executor.run_store.get("task").status, "runnable")

    def test_factory_budget_applies_to_service_and_custom_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=SqliteCheckpointStore(root / "state"),
                max_attempts=1,
            )
            calls = []

            def fail(record):
                calls.append(1)
                raise RuntimeError("factory unavailable")

            service = RecoverableRuntimeService(
                executor, worker_id="service", agent_for=fail
            )
            service.submit("failure", "task")
            for _ in range(4):
                service.recover_once()
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                executor.run_store.get("failure").status, "budget_exhausted"
            )
            service.agent_for = lambda record: Agent(
                "custom",
                lambda visible: assistant_message(
                    "done", sender="custom", kind="final"
                ),
            )
            service.submit("ok", "task")
            service.recover_once()
            self.assertEqual(executor.run_store.get("ok").status, "finished")

    def test_timeout_signals_and_joins_writer(self):
        finished = threading.Event()
        observed = []

        def execute(call_id, args, abort, update):
            deadline = time.monotonic() + 1
            while not abort() and time.monotonic() < deadline:
                time.sleep(0.002)
            observed.append("cancelled" if abort() else "deadline")
            finished.set()
            return text_result("stopped")

        tool = AgentTool(
            name="writer",
            description="writer",
            parameters={},
            execute=execute,
            timeout_seconds=0.02,
        )
        result = _execute_one(
            ToolCallBlock("one", "writer", {}), {"writer": tool}, lambda: False, None
        )
        self.assertTrue(result.is_error)
        self.assertTrue(finished.is_set())
        self.assertEqual(observed, ["cancelled"])

    @unittest.skipUnless(sys.platform != "win32", "POSIX process groups")
    def test_bash_timeout_kills_child_before_late_write(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_bash(
                "(sleep 0.3; echo late > late.txt) & wait",
                cwd=directory,
                timeout_seconds=0.05,
            )
            self.assertTrue(result.timed_out)
            time.sleep(0.4)
            self.assertFalse((Path(directory) / "late.txt").exists())

    def test_compaction_replaces_prior_summaries(self):
        calls = 0

        def generate(visible):
            nonlocal calls
            calls += 1
            return assistant_message(
                [ToolCallBlock(str(calls), "read", {})], sender="worker", kind="step"
            )

        agent = Agent(
            "worker",
            generate,
            tools=(
                AgentTool(
                    name="read",
                    description="read",
                    parameters={},
                    execute=lambda *args: text_result("evidence " * 220),
                ),
            ),
            context_policy=ContextPolicy(strategy=ToolCompactStrategy(1000)),
        )
        state, events = agent.run("work", max_turns=60)
        list(events)
        active = state.active_context_messages()
        self.assertEqual(sum(message.kind == "summary" for message in active), 1)
        self.assertLess(estimate_context_tokens(active), 2000)
        self.assertGreater(len(state.messages), len(active))
        self.assertIn("evidence", str(state.messages[2]))

    def test_guidance_scope_and_delivery_protect_existing_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            (workspace / "AGENTS.md").write_text("Run unit tests.")
            (workspace / "nested").mkdir()
            (workspace / "nested/AGENTS.md").write_text("Use local conventions.")
            (workspace / "test_existing.py").write_text("assert True\n")
            guidance = repository_guidance(workspace)
            self.assertIn("Scope: nested", guidance)
            self.assertIn("Run unit tests.", guidance)
            initialize(root / "run", workspace, "task", "true")
            (workspace / "test_existing.py").write_text("# test removed\n")
            review = delivery_review(workspace, root / "run/before")
            self.assertEqual(review["protected_changes"], ["test_existing.py"])
            import json

            verdict = verifier(
                root / "run",
                json.loads((root / "run/manifest.json").read_text()),
                State("task"),
            )
            self.assertFalse(verdict.done)
            self.assertIn("test_existing.py", verdict.reason)

    def test_task_notes_persist_and_require_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SqliteCheckpointStore(directory)
            state = store.create("task", "task")
            state.send("task", "user", "worker", "task")
            tool = make_task_status_tool(state, save=lambda: store.save("task", state))
            invalid = tool.execute(
                "one",
                {"requirements": [{"requirement": "fix", "status": "done"}]},
                lambda: False,
                None,
            )
            self.assertTrue(invalid.is_error)
            result = tool.execute(
                "two",
                {
                    "requirements": [
                        {"requirement": "fix", "status": "in_progress", "evidence": [0]}
                    ],
                    "next_action": "run tests",
                },
                lambda: False,
                None,
            )
            self.assertFalse(result.is_error)
            self.assertEqual(
                store.load("task").data["task_progress"]["next_action"], "run tests"
            )

    def test_rolling_compaction_retains_recent_distinct_evidence(self):
        from simple_long_horizon_agent.messages import text_of

        calls = 0

        def generate(visible):
            nonlocal calls
            calls += 1
            return assistant_message(
                [ToolCallBlock(str(calls), "read", {"n": calls})],
                sender="worker",
                kind="step",
            )

        def read(call_id, args, abort, update):
            return text_result(f"fact-{args['n']}: " + "detail " * 250)

        agent = Agent(
            "worker",
            generate,
            tools=(
                AgentTool(name="read", description="read", parameters={}, execute=read),
            ),
            context_policy=ContextPolicy(strategy=ToolCompactStrategy(1000)),
        )
        state, events = agent.run("task", max_turns=12)
        list(events)
        summary = next(
            message
            for message in state.active_context_messages()
            if message.kind == "summary"
        )
        text = text_of(summary.content)
        self.assertIn("fact-10:", text)
        self.assertIn("fact-9:", text)

    def test_repeated_verifier_failure_without_file_progress_blocks(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            root = Path(directory) / "run"
            initialize(root, workspace, "task", "exit 1")
            config = json.loads((root / "manifest.json").read_text())
            state = State("task")
            self.assertFalse(verifier(root, config, state).blocked)
            self.assertFalse(verifier(root, config, state).blocked)
            self.assertTrue(verifier(root, config, state).blocked)
            (workspace / "fix.py").write_text("fixed = True\n")
            self.assertFalse(verifier(root, config, state).blocked)

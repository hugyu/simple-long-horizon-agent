"""Process-level acceptance for durable code-task execution."""

from pathlib import Path
import tempfile
import unittest

from examples.recoverable_code_task.demo import run_demo
from simple_long_horizon_agent import FileEventJournal, State
from simple_long_horizon_agent.messages import message_tool_calls, tool_results_of
from simple_long_horizon_agent.protocols import TurnStartEvent
from simple_long_horizon_agent.run_control import FileRunStore, RunRecord


class RecoveryAcceptanceTest(unittest.TestCase):
    def test_sigkill_after_write_recovers_without_repeating_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "demo"
            result = run_demo(root, hard_crash=True)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["edit_executions"], 1)
            state = State(
                "repair", events=FileEventJournal(root / "journal").read("repair")
            )
            calls = [
                call.id
                for message in state.messages
                for call in message_tool_calls(message)
            ]
            results = [
                result.tool_call_id
                for message in state.messages
                for result in tool_results_of(message.content)
            ]
            self.assertCountEqual(calls, results)
            self.assertTrue((root / "repair.patch").read_text())

    def test_torn_final_record_is_discarded_but_committed_corruption_is_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = FileEventJournal(root)
            state = State("task")
            first = state.record_event(TurnStartEvent(agent="w"))
            journal.append("run", first)
            with (root / "run.jsonl").open("ab") as handle:
                handle.write(b'{"index":1,"partial":"\xe4')
            self.assertEqual(journal.read("run"), [first])
            second = state.record_event(TurnStartEvent(agent="w"))
            journal.append("run", second)
            self.assertEqual(journal.read("run"), [first, second])
            with (root / "run.jsonl").open("ab") as handle:
                handle.write(b"{broken}\n")
            with self.assertRaisesRegex(ValueError, "Invalid event journal"):
                journal.read("run")

    def test_stale_worker_cannot_enter_artifact_write_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            now = [0.0]
            store = FileRunStore(directory, clock=lambda: now[0])
            store.create(RunRecord("run", status="runnable"))
            old = store.acquire_lease("run", "old", lease_seconds=1)
            now[0] = 2.0
            current = store.acquire_lease("run", "new", lease_seconds=10)
            artifact = Path(directory) / "artifact"
            from simple_long_horizon_agent.run_control import RunControlError

            with self.assertRaises(RunControlError):
                with store.guard(old):
                    artifact.write_text("stale")
            self.assertFalse(artifact.exists())
            with store.guard(current):
                artifact.write_text("current")
            self.assertEqual(artifact.read_text(), "current")

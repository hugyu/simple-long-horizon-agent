"""Regressions for information loss, oversized requests and shell recovery."""

import tempfile
import unittest
from pathlib import Path

from simple_long_horizon_agent import (
    Agent,
    ContextPolicy,
    State,
    run,
    assistant_message,
)
from simple_long_horizon_agent.messages import (
    ToolCallBlock,
    ToolResultBlock,
    TextBlock,
    tool_results_message,
)
from simple_long_horizon_agent.tools import tool_result_text
from simple_long_horizon_agent.tools.recall import make_recall_tool
from simple_long_horizon_agent.compression import ToolCompactStrategy
from simple_long_horizon_agent.state import StateResourceLimitError
from simple_long_horizon_agent.reconciliation import BashOperationReconciler
from simple_long_horizon_agent.run_control import OperationRecord
from simple_long_horizon_agent.tools.bash import make_bash_tool


class LongTaskBoundariesTest(unittest.TestCase):
    def history(self):
        state = State("task")
        state.send("task", "user", "worker", "Find the tail fact")
        state.record(
            assistant_message(
                [ToolCallBlock("r", "read", {"path": "old"})],
                sender="worker",
                kind="step",
            )
        )
        state.record(
            tool_results_message(
                [
                    ToolResultBlock(
                        tool_call_id="r",
                        tool_name="read",
                        content=(TextBlock("x" * 12000 + "TAIL_FACT"),),
                    )
                ],
                target="worker",
            )
        )
        return state

    def test_recall_paging_recovers_tail_after_emergency_compaction(self):
        state = self.history()
        seen = []
        agent = Agent(
            "worker",
            lambda messages: (
                seen.append(messages)
                or assistant_message("done", sender="worker", kind="final")
            ),
            context_policy=ContextPolicy(
                strategy=ToolCompactStrategy(1000), max_input_tokens=3000
            ),
        )
        list(run(agent, state))
        self.assertEqual(len(seen), 1)
        self.assertIn("TAIL_FACT", str(seen[0]))
        self.assertLess(len(str(seen[0])), 3000)
        tool = make_recall_tool(state)
        first = tool_result_text(
            tool.execute("c", {"indices": [2]}, lambda: False, None)
        )
        self.assertIn("next_offset=4000", first)
        tail = tool_result_text(
            tool.execute("c", {"indices": [2], "offset": 12000}, lambda: False, None)
        )
        self.assertIn("TAIL_FACT", tail)

    def test_fixed_input_overflow_never_calls_model(self):
        state = State("task")
        agent = Agent(
            "worker",
            lambda _: self.fail("oversized request dispatched"),
            system_prompt="x" * 5000,
            context_policy=ContextPolicy(max_input_tokens=1000),
        )
        with self.assertRaises(StateResourceLimitError):
            list(run(agent, state))

    def test_non_tool_messages_are_externalized(self):
        state = State("task")
        state.send("message", "user", "worker", "x" * 10000)
        agent = Agent(
            "worker",
            lambda _: assistant_message("done", sender="worker", kind="final"),
            context_policy=ContextPolicy(max_input_tokens=2000),
        )
        list(run(agent, state))
        self.assertIn("x" * 10000, str(state.messages[0]))
        self.assertNotIn("x" * 10000, str(state.active_context_messages()))

    def test_only_exact_caller_approved_commands_are_repeated(self):
        with tempfile.TemporaryDirectory() as directory:
            command = "printf recovered > result.txt"
            tool = make_bash_tool(cwd=directory, recoverable_commands=(command,))
            metadata = tool.side_effect_metadata({"command": command})
            operation = OperationRecord(
                "op", "run", "call", "bash", "key", "digest", metadata=metadata
            )
            denied = BashOperationReconciler(workspace=directory, commands=())
            self.assertEqual(denied.reconcile(operation).status, "blocked")
            self.assertFalse((Path(directory) / "result.txt").exists())
            allowed = BashOperationReconciler(workspace=directory, commands=(command,))
            self.assertEqual(allowed.reconcile(operation).status, "confirmed")
            self.assertEqual((Path(directory) / "result.txt").read_text(), "recovered")
            self.assertIsNone(
                tool.side_effect_metadata({"command": command + "; false"})
            )

    def test_failed_test_is_a_completed_recovery_command(self):
        operation = OperationRecord(
            "op",
            "run",
            "call",
            "bash",
            "key",
            "digest",
            metadata={"retry_command": "false"},
        )
        with tempfile.TemporaryDirectory() as directory:
            reconciler = BashOperationReconciler(
                workspace=directory, commands=("false",)
            )
            result = reconciler.reconcile(operation)
            self.assertEqual(result.status, "confirmed")
            self.assertEqual(result.exit_code, 1)

    def test_provider_context_error_compacts_once_then_stops(self):
        class Overflow(Exception):
            code = "context_length_exceeded"

        state = self.history()
        calls = []

        def generate(messages):
            calls.append(str(messages))
            raise Overflow("window exceeded")

        agent = Agent(
            "worker", generate, context_policy=ContextPolicy(max_input_tokens=50000)
        )
        with self.assertRaises(StateResourceLimitError):
            list(run(agent, state))
        self.assertEqual(len(calls), 2)
        self.assertLess(len(calls[1]), len(calls[0]))

    def test_executor_resumes_pending_approved_shell_operation(self):
        from simple_long_horizon_agent import (
            FileRunStore,
            SqliteCheckpointStore,
            RecoverableRunExecutor,
            RecoverableRun,
        )
        from simple_long_horizon_agent.run_control import FileOperationLedger

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = "printf recovered > result.txt"
            ledger = FileOperationLedger(root / "operations")
            record = ledger.create_intent(
                OperationRecord(
                    "op",
                    "recover",
                    "c",
                    "bash",
                    "key",
                    "digest",
                    metadata={"retry_command": command},
                )
            )
            record = ledger.transition(record, "intent_recorded")
            ledger.transition(record, "started")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=SqliteCheckpointStore(root / "state"),
                operation_ledger=ledger,
                reconciler_for=lambda _: BashOperationReconciler(
                    workspace=root, commands=(command,)
                ),
            )
            executor.create("recover", State("continue"), worker_id="worker")

            def generate(_):
                self.assertEqual((root / "result.txt").read_text(), "recovered")
                return assistant_message("done", sender="worker", kind="final")

            _, events = executor.execute(
                RecoverableRun("recover", "recover", "worker"),
                Agent("worker", generate),
            )
            list(events)
            self.assertEqual(ledger.get("op").status, "confirmed")

    def test_edit_before_write_is_replayed_but_conflict_is_blocked(self):
        from simple_long_horizon_agent.tools.edit import _edit_operation_metadata
        from simple_long_horizon_agent.reconciliation import EditOperationReconciler

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "file.txt"
            path.write_text("before")
            metadata = _edit_operation_metadata(
                root,
                {"path": "file.txt", "old_string": "before", "new_string": "after"},
            )
            operation = OperationRecord(
                "op", "run", "call", "edit", "key", "digest", metadata=metadata
            )
            reconciler = EditOperationReconciler(workspace=root)
            self.assertEqual(reconciler.reconcile(operation).status, "confirmed")
            self.assertEqual(path.read_text(), "after")
            self.assertEqual(reconciler.reconcile(operation).status, "confirmed")
            path.write_text("external change")
            self.assertEqual(reconciler.reconcile(operation).status, "blocked")
            self.assertEqual(path.read_text(), "external change")

    def test_create_before_write_is_replayed(self):
        from simple_long_horizon_agent.tools.edit import _edit_operation_metadata
        from simple_long_horizon_agent.reconciliation import EditOperationReconciler

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = _edit_operation_metadata(
                root, {"path": "new.txt", "old_string": "", "new_string": "created"}
            )
            operation = OperationRecord(
                "op", "run", "call", "edit", "key", "digest", metadata=metadata
            )
            self.assertEqual(
                EditOperationReconciler(workspace=root).reconcile(operation).status,
                "confirmed",
            )
            self.assertEqual((root / "new.txt").read_text(), "created")

    def test_recovery_renews_lease_during_slow_reconciliation(self):
        import time
        from simple_long_horizon_agent import (
            FileRunStore,
            SqliteCheckpointStore,
            RecoverableRunExecutor,
            RecoverableRun,
        )
        from simple_long_horizon_agent.run_control import FileOperationLedger
        from simple_long_horizon_agent.reconciliation import ReconcileResult

        class SlowReconciler:
            def reconcile(self, operation):
                time.sleep(0.3)
                return ReconcileResult("confirmed", "finished")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = FileOperationLedger(root / "operations")
            record = ledger.create_intent(
                OperationRecord("op", "recover", "c", "bash", "key", "digest")
            )
            record = ledger.transition(record, "intent_recorded")
            ledger.transition(record, "started")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=SqliteCheckpointStore(root / "state"),
                operation_ledger=ledger,
                reconciler_for=lambda _: SlowReconciler(),
                lease_seconds=0.1,
                lease_renew_interval_seconds=0.02,
            )
            executor.create("recover", State("continue"), worker_id="worker")
            _, events = executor.execute(
                RecoverableRun("recover", "recover", "worker"),
                Agent(
                    "worker",
                    lambda _: assistant_message("done", sender="worker", kind="final"),
                ),
            )
            list(events)
            self.assertEqual(ledger.get("op").status, "confirmed")

    def test_failed_command_output_reaches_model_after_recovery(self):
        from simple_long_horizon_agent.run_control import (
            FileOperationLedger,
            operation_args_digest,
        )
        from simple_long_horizon_agent.reconciliation import (
            reconcile_pending,
            restore_tool_results,
        )

        state = State("fix test")
        state.data["run_id"] = "recover"
        state.record(
            assistant_message(
                [ToolCallBlock("c", "bash", {"command": "printf FAILURE; exit 1"})],
                sender="worker",
                kind="step",
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            ledger = FileOperationLedger(Path(directory) / "ledger")
            op_id = "op-" + operation_args_digest({"idempotency_key": "recover:c"})[:32]
            record = ledger.create_intent(
                OperationRecord(
                    op_id,
                    "recover",
                    "c",
                    "bash",
                    "recover:c",
                    "digest",
                    metadata={"retry_command": "printf FAILURE; exit 1"},
                )
            )
            record = ledger.transition(record, "intent_recorded")
            ledger.transition(record, "started")
            reconcile_pending(
                ledger,
                "recover",
                lambda _: BashOperationReconciler(
                    workspace=directory, commands=("printf FAILURE; exit 1",)
                ),
            )
            restore_tool_results(state, ledger, "worker")
            result = state.messages[-1].content[0]
            self.assertTrue(result.is_error)
            self.assertIn("FAILURE", str(result.content))
            self.assertIn("exit code 1", str(result.content))
            self.assertEqual(ledger.get(op_id).status, "confirmed")

    def test_compaction_preserves_recent_evidence_and_tool_pair(self):
        state = self.history()
        state.send(
            "message", "user", "worker", "Recent decision: preserve API compatibility"
        )
        received = []
        agent = Agent(
            "worker",
            lambda messages: (
                received.append(messages)
                or assistant_message("done", sender="worker", kind="final")
            ),
            context_policy=ContextPolicy(max_input_bytes=3000),
        )
        list(run(agent, state))
        self.assertIn("Recent decision: preserve API compatibility", str(received[0]))
        self.assertIn("TAIL_FACT", str(received[0]))
        self.assertIn("tool_call", str(received[0]))
        self.assertIn("tool_result", str(received[0]))

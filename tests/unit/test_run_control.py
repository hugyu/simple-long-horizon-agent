from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_long_horizon_agent.run_control import (
    FileOperationLedger,
    FileRunStore,
    LeaseConflict,
    OperationConflict,
    OperationRecord,
    RunRecord,
    VersionConflict,
    operation_args_digest,
)


class RunStoreTest(unittest.TestCase):
    def test_lease_fencing_and_old_worker_rejection(self) -> None:
        now = [100.0]
        with tempfile.TemporaryDirectory() as tmp:
            store = FileRunStore(tmp, clock=lambda: now[0])
            store.create(RunRecord("run-1", status="runnable"))
            worker_a = store.acquire_lease("run-1", "worker-a", lease_seconds=10)
            with self.assertRaises(LeaseConflict):
                store.acquire_lease("run-1", "worker-b", lease_seconds=10)

            now[0] = 111.0
            worker_b = store.acquire_lease("run-1", "worker-b", lease_seconds=10)
            self.assertGreater(worker_b.fencing_token, worker_a.fencing_token)
            with self.assertRaises(VersionConflict):
                store.transition(worker_a, "running")
            running = store.transition(worker_b, "running")
            self.assertEqual(running.status, "running")

    def test_release_returns_run_to_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileRunStore(tmp)
            store.create(RunRecord("run-1", status="runnable"))
            leased = store.acquire_lease("run-1", "worker-a", lease_seconds=10)
            released = store.release_lease(leased)
            self.assertEqual(released.status, "runnable")
            self.assertIsNone(released.lease_owner)
            self.assertIsNone(released.lease_expires_at)

    def test_invalid_run_id_cannot_escape_store_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileRunStore(Path(tmp))
            with self.assertRaises(ValueError):
                store.create(RunRecord("../outside"))


class OperationLedgerTest(unittest.TestCase):
    def _record(
        self, *, key: str = "run-1:call-1", operation_id: str = "op-1"
    ) -> OperationRecord:
        return OperationRecord(
            operation_id=operation_id,
            run_id="run-1",
            tool_call_id="call-1",
            tool_name="edit",
            idempotency_key=key,
            args_digest=operation_args_digest({"path": "a.py", "new": "x"}),
        )

    def test_idempotent_intent_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FileOperationLedger(tmp)
            created = ledger.create_intent(self._record())
            intent = ledger.transition(created, "intent_recorded")
            started = ledger.transition(intent, "started")
            unknown = ledger.transition(started, "unknown")
            reconciled = ledger.transition(
                unknown, "reconciled", result={"found": True}
            )
            duplicate = ledger.create_intent(self._record(operation_id="op-2"))

            self.assertEqual(duplicate.operation_id, created.operation_id)
            self.assertEqual(reconciled.status, "reconciled")
            self.assertEqual(reconciled.result, {"found": True})

    def test_reusing_key_for_different_operation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FileOperationLedger(tmp)
            ledger.create_intent(self._record())
            different = OperationRecord(
                operation_id="op-2",
                run_id="run-1",
                tool_call_id="call-2",
                tool_name="edit",
                idempotency_key="run-1:call-1",
                args_digest=operation_args_digest({"path": "b.py"}),
            )
            with self.assertRaises(OperationConflict):
                ledger.create_intent(different)

    def test_stale_transition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = FileOperationLedger(tmp)
            created = ledger.create_intent(self._record())
            intent = ledger.transition(created, "intent_recorded")
            with self.assertRaises(VersionConflict):
                ledger.transition(created, "intent_recorded")
            self.assertEqual(ledger.get(intent.operation_id).status, "intent_recorded")


if __name__ == "__main__":
    unittest.main()

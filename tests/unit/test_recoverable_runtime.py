from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from simple_long_horizon_agent import (
    Agent,
    FileCheckpointStore,
    FileRunStore,
    RecoverableRun,
    RecoverableRunExecutor,
    RecoveryScanner,
    RecoveryScheduler,
    FileWorkspaceManager,
    FileEvidenceStore,
    LeaseLostError,
    State,
    assistant_message,
    FileEventJournal,
    merge_checkpoint_with_journal,
)
from simple_long_horizon_agent.reconciliation import EditOperationReconciler
from simple_long_horizon_agent.run_control import (
    FileOperationLedger,
    OperationRecord,
    operation_args_digest,
)
from simple_long_horizon_agent.protocols import TurnStartEvent
from simple_long_horizon_agent.run_control import RunRecord


class RecoverableRuntimeTest(unittest.TestCase):
    def test_edit_reconciler_confirms_existing_post_image(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / "note.txt"
            path.write_text("after", encoding="utf-8")
            import hashlib

            record = __import__(
                "simple_long_horizon_agent", fromlist=["OperationRecord"]
            ).OperationRecord(
                operation_id="op-1",
                run_id="run-1",
                tool_call_id="edit-1",
                tool_name="edit",
                idempotency_key="run-1:edit-1",
                args_digest="digest",
                status="started",
                metadata={
                    "path": str(path),
                    "new_sha256": hashlib.sha256(b"after").hexdigest(),
                },
            )
            result = EditOperationReconciler(workspace=root).reconcile(record)
            self.assertEqual(result.status, "confirmed")

    def test_executor_reconciles_pending_edit_before_resuming(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            path = root / "note.txt"
            path.write_text("after", encoding="utf-8")
            ledger = FileOperationLedger(root / "ledger")
            record = ledger.create_intent(
                OperationRecord(
                    operation_id="op-edit",
                    run_id="recover",
                    tool_call_id="edit-1",
                    tool_name="edit",
                    idempotency_key="recover:edit-1",
                    args_digest=operation_args_digest({"path": "note.txt"}),
                    status="created",
                    metadata={
                        "path": str(path),
                        "new_sha256": __import__("hashlib")
                        .sha256(b"after")
                        .hexdigest(),
                    },
                )
            )
            ledger.transition(record, "intent_recorded")
            ledger.transition(ledger.get("op-edit"), "started")
            run_store = FileRunStore(root / "runs")
            executor = RecoverableRunExecutor(
                run_store=run_store,
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                operation_ledger=ledger,
                reconciler_for=lambda operation: EditOperationReconciler(
                    workspace=root
                ),
            )
            executor.create("recover", State("continue"), worker_id="worker")
            _, events = executor.execute(
                RecoverableRun("recover", "recover", "worker"),
                Agent(
                    "writer",
                    lambda visible: assistant_message(
                        "done", sender="writer", target="user", kind="final"
                    ),
                ),
            )
            list(events)
            self.assertEqual(ledger.get("op-edit").status, "confirmed")
            self.assertEqual(run_store.get("recover").status, "complete")

    def test_long_run_renews_lease_before_it_expires(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            run_store = FileRunStore(root / "runs")
            executor = RecoverableRunExecutor(
                run_store=run_store,
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                lease_seconds=0.15,
                lease_renew_interval_seconds=0.03,
            )
            executor.create("heartbeat", State("wait"), worker_id="worker-a")
            ready = threading.Event()

            def generate(visible):
                ready.set()
                time.sleep(0.25)
                return assistant_message(
                    "done", sender="writer", target="user", kind="final"
                )

            _, events = executor.execute(
                RecoverableRun("heartbeat", "heartbeat", "worker-a"),
                Agent("writer", generate),
            )
            list(events)
            self.assertTrue(ready.is_set())
            self.assertEqual(run_store.get("heartbeat").status, "complete")

    def test_lost_lease_stops_old_worker_without_releasing_new_lease(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            now = [100.0]
            run_store = FileRunStore(root / "runs", clock=lambda: now[0])
            executor = RecoverableRunExecutor(
                run_store=run_store,
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                lease_seconds=10,
                lease_renew_interval_seconds=0.02,
            )
            executor.create("fenced", State("task"), worker_id="worker-a")
            entered = threading.Event()
            release = threading.Event()

            def generate(visible):
                entered.set()
                release.wait(1)
                return assistant_message(
                    "done", sender="writer", target="user", kind="final"
                )

            _, events = executor.execute(
                RecoverableRun("fenced", "fenced", "worker-a"),
                Agent("writer", generate),
            )
            worker_error: list[BaseException] = []

            def consume() -> None:
                try:
                    list(events)
                except BaseException as exc:
                    worker_error.append(exc)

            thread = threading.Thread(target=consume)
            thread.start()
            self.assertTrue(entered.wait(1))
            now[0] = 111.0
            new_lease = run_store.acquire_lease("fenced", "worker-b", lease_seconds=10)
            self.assertEqual(new_lease.lease_owner, "worker-b")
            release.set()
            thread.join(2)
            self.assertTrue(worker_error)
            self.assertIsInstance(worker_error[0], LeaseLostError)
            self.assertEqual(run_store.get("fenced").lease_owner, "worker-b")

    def test_checkpoint_journal_merge_replays_only_tail(self) -> None:
        checkpoint = State("task")
        checkpoint.send("task", "user", "writer", "task")
        prefix = list(checkpoint.events)
        tail = checkpoint.record_event(TurnStartEvent(agent="writer"))
        merged = merge_checkpoint_with_journal(checkpoint, [*prefix, tail])
        self.assertEqual(merged.events, [*prefix, tail])
        self.assertEqual(merged.messages, checkpoint.messages)

    def test_checkpoint_journal_merge_rejects_prefix_conflict(self) -> None:
        checkpoint = State("task")
        checkpoint.send("task", "user", "writer", "task")
        conflicting = TurnStartEvent(agent="other", index=0, uuid="different")
        with self.assertRaisesRegex(ValueError, "conflicts"):
            merge_checkpoint_with_journal(checkpoint, [conflicting])

    def test_event_journal_is_append_only_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            journal = FileEventJournal(Path(raw_root))
            event = TurnStartEvent(agent="writer", index=0, uuid="event-1")
            journal.append("run-1", event)
            journal.append("run-1", event)
            self.assertEqual(journal.read("run-1"), [event])
            with self.assertRaises(ValueError):
                journal.append("run-1", TurnStartEvent(agent="writer", index=2))

    def test_scanner_finds_runnable_and_expired_runs(self) -> None:
        now = [100.0]
        with tempfile.TemporaryDirectory() as raw_root:
            store = FileRunStore(Path(raw_root), clock=lambda: now[0])
            store.create(RunRecord("runnable", status="runnable"))
            store.create(
                RunRecord(
                    "expired",
                    status="running",
                    lease_owner="old",
                    lease_expires_at=99.0,
                    fencing_token=1,
                )
            )
            store.create(
                RunRecord(
                    "active",
                    status="running",
                    lease_owner="old",
                    lease_expires_at=101.0,
                    fencing_token=1,
                )
            )
            found = {
                record.run_id
                for record in RecoveryScanner(store, clock=lambda: now[0]).runnable()
            }
            self.assertEqual(found, {"runnable", "expired"})

    def test_scheduler_attempts_candidates_and_keeps_running_after_one_error(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            store = FileRunStore(Path(raw_root))
            store.create(RunRecord("first", status="runnable"))
            store.create(RunRecord("second", status="runnable"))
            attempted: list[str] = []
            errors: list[tuple[str | None, str]] = []

            def recover(record: RunRecord) -> None:
                attempted.append(record.run_id)
                if record.run_id == "first":
                    raise RuntimeError("transient")

            scheduler = RecoveryScheduler(
                RecoveryScanner(store),
                worker_id="worker-1",
                recover=recover,
                on_error=lambda record, error: errors.append(
                    (record.run_id if record else None, str(error))
                ),
            )
            self.assertEqual(scheduler.run_once(), ["first", "second"])
            self.assertEqual(attempted, ["first", "second"])
            self.assertEqual(errors, [("first", "transient")])

    def test_scheduler_stop_ends_background_loop(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            store = FileRunStore(Path(raw_root))
            store.create(RunRecord("loop", status="runnable"))
            started = threading.Event()
            stopped = threading.Event()

            def recover(record: RunRecord) -> None:
                started.set()

            scheduler = RecoveryScheduler(
                RecoveryScanner(store),
                worker_id="worker-1",
                recover=recover,
                poll_interval_seconds=0.01,
            )
            scheduler.start()
            self.assertTrue(started.wait(1))
            self.assertTrue(scheduler.stop(join_timeout=1))
            stopped.set()
            self.assertFalse(scheduler.running)
            self.assertTrue(stopped.is_set())

    def test_scheduler_stop_does_not_start_remaining_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            store = FileRunStore(Path(raw_root))
            store.create(RunRecord("first", status="runnable"))
            store.create(RunRecord("second", status="runnable"))
            started = threading.Event()
            release = threading.Event()
            attempted: list[str] = []

            def recover(record: RunRecord) -> None:
                attempted.append(record.run_id)
                started.set()
                release.wait(1)

            scheduler = RecoveryScheduler(
                RecoveryScanner(store),
                worker_id="worker-1",
                recover=recover,
                poll_interval_seconds=1,
            )
            scheduler.start()
            self.assertTrue(started.wait(1))
            self.assertFalse(scheduler.stop(join_timeout=0.01))
            release.set()
            self.assertTrue(scheduler.stop(join_timeout=1))
            self.assertEqual(attempted, ["first"])

    def test_executor_writes_independent_event_journal(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                event_journal=FileEventJournal(root / "events"),
                checkpoint_every_events=2,
            )
            executor.create("run-journal", State("finish"), worker_id="worker")
            agent = Agent(
                "writer",
                lambda visible: assistant_message(
                    "done", sender="writer", target="user", kind="final"
                ),
            )
            _, events = executor.execute(
                RecoverableRun("run-journal", "run-journal", "worker"), agent
            )
            list(events)
            journal_events = FileEventJournal(root / "events").read("run-journal")
            self.assertTrue(journal_events)
            self.assertEqual(
                [event.index for event in journal_events],
                list(range(len(journal_events))),
            )

    def test_executor_persists_evidence_on_completion(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            evidence_store = FileEvidenceStore(root / "evidence")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                evidence_store=evidence_store,
            )
            executor.create("evidence-run", State("finish"), worker_id="worker")
            _, events = executor.execute(
                RecoverableRun("evidence-run", "evidence-run", "worker"),
                Agent(
                    "writer",
                    lambda visible: assistant_message(
                        "done", sender="writer", target="user", kind="final"
                    ),
                ),
            )
            list(events)
            pack = evidence_store.load("evidence-run")
            self.assertEqual(pack.run_id, "evidence-run")
            self.assertEqual(pack.stop_reason, "done")

    def test_run_is_checkpointed_and_completed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                checkpoint_every_events=1,
            )
            state = State("finish task")
            executor.create("run-1", state, worker_id="worker-a")
            agent = Agent(
                "writer",
                lambda visible: assistant_message(
                    "done", sender="writer", target="user", kind="final"
                ),
            )
            resumed, events = executor.execute(
                RecoverableRun("run-1", "run-1", "worker-a"), agent
            )
            list(events)

            self.assertTrue(resumed.events)
            self.assertEqual(executor.run_store.get("run-1").status, "complete")
            self.assertEqual(
                executor.checkpoint_store.load("run-1").events, resumed.events
            )

    def test_executor_persists_and_resolves_workspace_reference(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = FileWorkspaceManager(root / "workspaces")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                workspace_manager=manager,
            )
            state = State("finish")
            executor.create("workspace-run", state, worker_id="worker")
            self.assertEqual(
                executor.run_store.get("workspace-run").workspace_ref,
                "workspace-run",
            )
            resumed, events = executor.execute(
                RecoverableRun("workspace-run", "workspace-run", "worker"),
                Agent(
                    "writer",
                    lambda visible: assistant_message(
                        "done", sender="writer", target="user", kind="final"
                    ),
                ),
            )
            list(events)
            self.assertEqual(
                resumed.data["workspace_path"],
                str(manager.resolve("workspace-run")),
            )

    def test_missing_workspace_blocks_run_before_model_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            manager = FileWorkspaceManager(root / "workspaces")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                workspace_manager=manager,
            )
            executor.create("missing-workspace", State("finish"), worker_id="worker")
            manager.release("missing-workspace", remove=True)
            with self.assertRaisesRegex(Exception, "workspace cannot be recovered"):
                executor.execute(
                    RecoverableRun("missing-workspace", "missing-workspace", "worker"),
                    Agent(
                        "writer",
                        lambda visible: (_ for _ in ()).throw(AssertionError()),
                    ),
                )
            record = executor.run_store.get("missing-workspace")
            self.assertEqual(record.status, "blocked")
            self.assertIsNone(record.lease_owner)

    def test_exception_checkpoints_and_releases_for_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            run_store = FileRunStore(root / "runs")
            executor = RecoverableRunExecutor(
                run_store=run_store,
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                lease_seconds=30,
            )
            executor.create("run-2", State("continue"), worker_id="worker-a")
            agent = Agent(
                "writer",
                lambda visible: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            _, events = executor.execute(
                RecoverableRun("run-2", "run-2", "worker-a"), agent
            )
            with self.assertRaises(RuntimeError):
                list(events)
            self.assertEqual(run_store.get("run-2").status, "runnable")

            takeover = RecoverableRun("run-2", "run-2", "worker-b")
            safe_agent = Agent(
                "writer",
                lambda visible: assistant_message(
                    "continued", sender="writer", target="user", kind="final"
                ),
            )
            _, resumed_events = executor.execute(takeover, safe_agent)
            list(resumed_events)
            self.assertEqual(run_store.get("run-2").status, "complete")


if __name__ == "__main__":
    unittest.main()

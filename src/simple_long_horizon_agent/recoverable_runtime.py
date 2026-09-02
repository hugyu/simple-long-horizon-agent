"""Small coordinator for durable, restartable Agent runs.

The coordinator owns control-plane concerns while ``core.run`` remains a
simple generator. It persists checkpoints at event boundaries, fences workers
through ``RunStore``, and releases a resumable run when a worker is interrupted.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import time
import threading
from pathlib import Path
from typing import Callable, cast

from .checkpoint import CheckpointStore
from .core import Agent, run
from .event_journal import EventJournal, merge_checkpoint_with_journal
from .evidence import EvidenceStore, evidence_pack_from_state
from .reconciliation import OperationReconciler, reconcile_pending
from .protocols import AgentEndEvent, Event
from .run_control import (
    OperationLedger,
    OperationRecord,
    RunControlError,
    RunRecord,
    RunStatus,
    RunStore,
)
from .state import State
from .tools import AbortFlag
from .workspace import WorkspaceManager, WorkspaceRef


StateFactory = Callable[[], State]
RecoveryHandler = Callable[[RunRecord], None]
RecoveryErrorHandler = Callable[[RunRecord | None, BaseException], None]


class LeaseLostError(RunControlError):
    """The worker can no longer safely advance a Run."""


class _LeaseHeartbeat:
    def __init__(
        self,
        run_store: RunStore,
        lease: RunRecord,
        *,
        interval_seconds: float,
    ) -> None:
        self._run_store = run_store
        self._lease = lease
        self._interval_seconds = interval_seconds
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop, name="recoverable-run-lease", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval_seconds * 2))

    def lease(self) -> RunRecord:
        with self._lock:
            return self._lease

    def update_progress(self, **kwargs: int | None) -> RunRecord:
        with self._lock:
            self._assert_active()
            self._lease = self._run_store.update_progress(self._lease, **kwargs)
            return self._lease

    def transition(self, status: RunStatus) -> RunRecord:
        with self._lock:
            self._assert_active()
            self._lease = self._run_store.transition(self._lease, status)
            return self._lease

    def release(self, *, status: RunStatus) -> RunRecord:
        with self._lock:
            self._assert_active()
            self._lease = self._run_store.release_lease(self._lease, status=status)
            return self._lease

    def _assert_active(self) -> None:
        if self.lost:
            raise LeaseLostError("Run lease was lost; worker must stop")

    def _loop(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                with self._lock:
                    self._assert_active()
                    self._lease = self._run_store.renew_lease(
                        self._lease, lease_seconds=self._interval_seconds * 3
                    )
            except BaseException:
                self._lost.set()
                return


@dataclass(frozen=True)
class RecoverableRun:
    """Handles needed to execute or recover one logical Run."""

    run_id: str
    checkpoint_id: str
    worker_id: str


class RecoverableRunExecutor:
    """Coordinate one worker's lease, state checkpointing, and Agent loop."""

    def __init__(
        self,
        *,
        run_store: RunStore,
        checkpoint_store: CheckpointStore,
        operation_ledger: OperationLedger | None = None,
        event_journal: EventJournal | None = None,
        evidence_store: EvidenceStore | None = None,
        workspace_manager: WorkspaceManager | None = None,
        reconciler_for: Callable[[OperationRecord], OperationReconciler | None]
        | None = None,
        lease_seconds: float = 30.0,
        lease_renew_interval_seconds: float | None = None,
        checkpoint_every_events: int = 10,
    ) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        if checkpoint_every_events <= 0:
            raise ValueError("checkpoint_every_events must be greater than zero")
        self.run_store = run_store
        self.checkpoint_store = checkpoint_store
        self.operation_ledger = operation_ledger
        self.event_journal = event_journal
        self.evidence_store = evidence_store
        self.workspace_manager = workspace_manager
        self.reconciler_for = reconciler_for
        self.lease_seconds = lease_seconds
        self.lease_renew_interval_seconds = (
            lease_renew_interval_seconds
            if lease_renew_interval_seconds is not None
            else max(0.1, lease_seconds / 3)
        )
        if self.lease_renew_interval_seconds <= 0:
            raise ValueError("lease_renew_interval_seconds must be greater than zero")
        self.checkpoint_every_events = checkpoint_every_events

    def create(
        self,
        run_id: str,
        state: State,
        *,
        worker_id: str,
        workspace_source: str | Path | None = None,
    ) -> RecoverableRun:
        """Persist the initial state and make a Run available to workers."""

        state.data["run_id"] = run_id
        workspace_ref = state.data.get("workspace_ref")
        if isinstance(workspace_ref, WorkspaceRef):
            workspace_ref = workspace_ref.workspace_id
        if workspace_ref is not None and not isinstance(workspace_ref, str):
            raise ValueError("state.data['workspace_ref'] must be a string")
        if self.workspace_manager is not None and workspace_ref is None:
            workspace_ref = self.workspace_manager.create(
                run_id, source=workspace_source
            ).workspace_id
            state.data["workspace_ref"] = workspace_ref
        if self.operation_ledger is not None:
            state.data["operation_ledger_root"] = str(
                getattr(self.operation_ledger, "root", "")
            )
        self.checkpoint_store.save(run_id, state)
        if self.event_journal is not None:
            for event in state.events:
                self.event_journal.append(run_id, event)
        self.run_store.create(
            RunRecord(run_id=run_id, status="runnable", workspace_ref=workspace_ref)
        )
        return RecoverableRun(run_id=run_id, checkpoint_id=run_id, worker_id=worker_id)

    def execute(
        self,
        handle: RecoverableRun,
        agent: Agent,
        *,
        task: str | None = None,
        max_turns: int = 10,
        abort: AbortFlag = lambda: False,
    ) -> tuple[State, Iterator[Event]]:
        """Acquire a lease and return an event iterator for a fresh or resumed Run."""

        lease = self.run_store.acquire_lease(
            handle.run_id, handle.worker_id, lease_seconds=self.lease_seconds
        )
        state = self.checkpoint_store.load(handle.checkpoint_id)
        if self.workspace_manager is not None and lease.workspace_ref is not None:
            try:
                state.data["workspace_ref"] = lease.workspace_ref
                state.data["workspace_path"] = str(
                    self.workspace_manager.resolve(lease.workspace_ref)
                )
            except (FileNotFoundError, ValueError) as exc:
                lease = self.run_store.transition(lease, "blocked")
                self.run_store.release_lease(lease, status="blocked")
                self._save_evidence(state)
                raise RunControlError(
                    f"Run {handle.run_id!r} workspace cannot be recovered: {exc}"
                ) from exc
        if self.event_journal is not None:
            state = merge_checkpoint_with_journal(
                state, self.event_journal.read(handle.run_id)
            )
        if self.operation_ledger is not None:
            state.data["operation_ledger"] = self.operation_ledger
        if task is not None and not state.messages:
            state = agent._default_init_state(task)
        state.data["run_id"] = handle.run_id
        state.data["fencing_token"] = lease.fencing_token
        if self.operation_ledger is not None and self.operation_ledger.list_pending(
            handle.run_id
        ):
            lease = self.run_store.transition(lease, "reconciling")
            outcomes = reconcile_pending(
                self.operation_ledger,
                handle.run_id,
                self.reconciler_for or (lambda operation: None),
            )
            if any(outcome.status != "confirmed" for _, outcome in outcomes):
                lease = self.run_store.transition(lease, "blocked")
                self.run_store.release_lease(lease, status="blocked")
                self._save_evidence(state)
                raise RunControlError(
                    f"Run {handle.run_id!r} has an operation that could not be reconciled"
                )
        lease = self.run_store.transition(lease, "running")
        return state, self._events(
            handle, agent, state, max_turns=max_turns, abort=abort
        )

    def _save_evidence(self, state: State) -> None:
        if self.evidence_store is not None:
            self.evidence_store.save(
                str(state.data.get("run_id") or ""), evidence_pack_from_state(state)
            )

    def _events(
        self,
        handle: RecoverableRun,
        agent: Agent,
        state: State,
        *,
        max_turns: int,
        abort: AbortFlag,
    ) -> Iterator[Event]:
        lease = self.run_store.get(handle.run_id)
        heartbeat = _LeaseHeartbeat(
            self.run_store,
            lease,
            interval_seconds=self.lease_renew_interval_seconds,
        )
        heartbeat.start()
        last_checkpoint = len(state.events)
        try:

            def combined_abort() -> bool:
                return abort() or heartbeat.lost

            for event in run(agent, state, max_turns=max_turns, abort=combined_abort):
                if heartbeat.lost:
                    raise LeaseLostError(
                        "Run lease was lost while the Agent was executing"
                    )
                if self.event_journal is not None:
                    self.event_journal.append(handle.run_id, event)
                yield event
                if len(
                    state.events
                ) - last_checkpoint >= self.checkpoint_every_events or isinstance(
                    event, AgentEndEvent
                ):
                    self.checkpoint_store.save(handle.checkpoint_id, state)
                    last_checkpoint = len(state.events)
                    lease = heartbeat.update_progress(
                        latest_event_index=event.index,
                        checkpoint_event_index=event.index,
                    )
                if isinstance(event, AgentEndEvent):
                    status = cast(
                        RunStatus,
                        {
                            "done": "complete",
                            "abort": "aborted",
                            "max_turns": "runnable",
                            "tool_terminate": "complete",
                        }[event.reason],
                    )
                    lease = heartbeat.transition(status)
                    heartbeat.release(status=status)
                    self._save_evidence(state)
                    return
        except LeaseLostError:
            raise
        except BaseException:
            if heartbeat.lost:
                raise LeaseLostError(
                    "Run lease was lost while handling a worker failure"
                )
            self.checkpoint_store.save(handle.checkpoint_id, state)
            try:
                heartbeat.update_progress(
                    latest_event_index=state.events[-1].index if state.events else -1,
                    checkpoint_event_index=state.events[-1].index
                    if state.events
                    else -1,
                )
                heartbeat.release(status="runnable")
                self._save_evidence(state)
            except RunControlError as exc:
                raise LeaseLostError(
                    "Run lease was lost while handling a worker failure"
                ) from exc
            raise
        finally:
            heartbeat.stop()


class RecoveryScanner:
    """Find Runs that a new worker may attempt to claim after restart."""

    def __init__(
        self, run_store: RunStore, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.run_store = run_store
        self._clock = clock

    def runnable(self) -> list[RunRecord]:
        return [record for record in self.run_store.list() if self._is_runnable(record)]

    def _is_runnable(self, record: RunRecord) -> bool:
        if record.status in {"runnable", "reconciling", "waiting_external"}:
            return True
        if record.status in {"leased", "running"}:
            return (
                record.lease_expires_at is not None
                and record.lease_expires_at <= self._clock()
            )
        return False


class RecoveryScheduler:
    """Continuously hand recoverable Runs to a caller-owned worker callback.

    The scheduler only discovers candidates.  The callback remains responsible
    for constructing a ``RecoverableRun`` and invoking the executor, which keeps
    lease acquisition and fencing as the single concurrency boundary.
    """

    def __init__(
        self,
        scanner: RecoveryScanner,
        *,
        worker_id: str,
        recover: RecoveryHandler,
        poll_interval_seconds: float = 5.0,
        sleep_fn: Callable[[float], None] | None = None,
        on_error: RecoveryErrorHandler | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be greater than zero")
        self.scanner = scanner
        self.worker_id = worker_id
        self.recover = recover
        self.poll_interval_seconds = poll_interval_seconds
        self.sleep_fn = sleep_fn
        self.on_error = on_error
        self.last_errors: list[tuple[str, BaseException]] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_once(self) -> list[str]:
        """Scan and attempt each candidate once, returning attempted Run IDs."""

        attempted: list[str] = []
        self.last_errors = []
        for record in self.scanner.runnable():
            if self._stop.is_set():
                break
            attempted.append(record.run_id)
            try:
                self.recover(record)
            except Exception as exc:
                self.last_errors.append((record.run_id, exc))
                if self.on_error is not None:
                    self.on_error(record, exc)
        return attempted

    def run_forever(self, *, stop_event: threading.Event | None = None) -> None:
        """Run scan cycles until ``stop()`` or the optional event is set."""

        external_stop = stop_event
        while not self._stop.is_set() and not (
            external_stop is not None and external_stop.is_set()
        ):
            try:
                self.run_once()
            except Exception as exc:
                if self.on_error is not None:
                    self.on_error(None, exc)
                else:
                    raise
            if self._stop.is_set() or (
                external_stop is not None and external_stop.is_set()
            ):
                break
            if self.sleep_fn is None:
                self._stop.wait(self.poll_interval_seconds)
            else:
                self.sleep_fn(self.poll_interval_seconds)

    def start(self) -> None:
        """Start a daemon scan thread; repeated starts are harmless."""

        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            name=f"recovery-scheduler-{self.worker_id}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout: float | None = None) -> bool:
        """Request shutdown and report whether the scheduler fully stopped.

        A running recovery callback is allowed to finish; a finite timeout can
        therefore return ``False`` while that callback is still in progress.
        """

        self._stop.set()
        if self._thread is not None:
            self._thread.join(join_timeout)
        return not self.running


__all__ = [
    "LeaseLostError",
    "RecoverableRun",
    "RecoverableRunExecutor",
    "RecoveryScanner",
    "RecoveryScheduler",
]

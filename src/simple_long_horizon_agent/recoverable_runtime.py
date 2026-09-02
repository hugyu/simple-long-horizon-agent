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
from typing import Callable, cast

from .checkpoint import CheckpointStore
from .core import Agent, run
from .event_journal import EventJournal, merge_checkpoint_with_journal
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


StateFactory = Callable[[], State]


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

    def create(self, run_id: str, state: State, *, worker_id: str) -> RecoverableRun:
        """Persist the initial state and make a Run available to workers."""

        state.data["run_id"] = run_id
        if self.operation_ledger is not None:
            state.data["operation_ledger_root"] = str(
                getattr(self.operation_ledger, "root", "")
            )
        self.checkpoint_store.save(run_id, state)
        if self.event_journal is not None:
            for event in state.events:
                self.event_journal.append(run_id, event)
        self.run_store.create(RunRecord(run_id=run_id, status="runnable"))
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
                raise RunControlError(
                    f"Run {handle.run_id!r} has an operation that could not be reconciled"
                )
        lease = self.run_store.transition(lease, "running")
        return state, self._events(
            handle, agent, state, max_turns=max_turns, abort=abort
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


__all__ = [
    "LeaseLostError",
    "RecoverableRun",
    "RecoverableRunExecutor",
    "RecoveryScanner",
]

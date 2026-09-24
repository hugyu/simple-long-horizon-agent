"""Small coordinator for durable, restartable Agent runs.

The coordinator owns control-plane concerns while ``core.run`` remains a
simple generator. It persists checkpoints at event boundaries, fences workers
through ``RunStore``, and releases a resumable run when a worker is interrupted.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import time
import math
import threading
from pathlib import Path
from typing import Callable, cast

from .checkpoint import CheckpointStore
from .completion import CompletionCheck, CompletionResult
from .messages import text_of
from .core import Agent, run
from .event_journal import EventJournal, merge_checkpoint_with_journal
from .evidence import EvidenceStore, evidence_pack_from_state
from .reconciliation import (
    BashOperationReconciler,
    OperationReconciler,
    reconcile_pending,
    restore_tool_results,
)
from .protocols import (
    AgentEndEvent,
    Event,
    GoalStatusEvent,
    ModelRequestEvent,
    ModelResponseEvent,
)
from .run_control import (
    GuardedOperationLedger,
    OperationLedger,
    OperationRecord,
    RunControlError,
    RunRecord,
    RunStatus,
    RunStore,
)
from .state import State, StateResourceLimitError
from .sqlite_state import SqliteCheckpointStore
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
        lease_seconds: float,
    ) -> None:
        self._lease_seconds = lease_seconds
        self._run_store = run_store
        self._lease = lease
        self._interval_seconds = interval_seconds
        self._lock = threading.RLock()
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

    @contextmanager
    def guard(self) -> Iterator[None]:
        with self._lock:
            self._assert_active()
            with ExitStack() as stack:
                try:
                    stack.enter_context(self._run_store.guard(self._lease))
                except RunControlError as exc:
                    self._lost.set()
                    raise LeaseLostError("Run lease no longer permits writes") from exc
                # A tool's ledger conflict is not evidence that the Run lease
                # was lost; only failure to acquire the guard fences this worker.
                yield

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
                        self._lease, lease_seconds=self._lease_seconds
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
        completion_check: CompletionCheck | None = None,
        max_attempts: int = 3,
        max_model_calls: int | None = None,
        max_tokens: int | None = None,
        wall_clock_seconds: float | None = None,
    ) -> None:
        if (
            isinstance(checkpoint_store, SqliteCheckpointStore)
            and event_journal is not None
        ):
            raise ValueError(
                "SQLite state already owns its event journal; omit event_journal"
            )
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        if checkpoint_every_events <= 0:
            raise ValueError("checkpoint_every_events must be greater than zero")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
        ):
            raise ValueError(
                f"max_attempts must be a positive integer, got {max_attempts!r}"
            )
        for name, value in (
            ("max_model_calls", max_model_calls),
            ("max_tokens", max_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if wall_clock_seconds is not None and (
            not math.isfinite(wall_clock_seconds) or wall_clock_seconds <= 0
        ):
            raise ValueError(
                f"wall_clock_seconds must be positive and finite, got {wall_clock_seconds!r}"
            )
        self.max_model_calls = max_model_calls
        self.max_tokens = max_tokens
        self.wall_clock_seconds = wall_clock_seconds
        self.completion_check = completion_check
        self.max_attempts = max_attempts
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
        state.data["completion_required"] = self.completion_check is not None
        state.data["max_attempts"] = self.max_attempts
        state.data["attempts_started"] = 0
        state.data["run_budget"] = {
            "max_model_calls": self.max_model_calls,
            "max_tokens": self.max_tokens,
            "deadline": time.time() + self.wall_clock_seconds
            if self.wall_clock_seconds is not None
            else None,
        }
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
        agent_for_state: Callable[[State], Agent] | None = None,
        max_turns: int = 10,
        abort: AbortFlag = lambda: False,
    ) -> tuple[State, Iterator[Event]]:
        """Acquire a lease and return an event iterator for a fresh or resumed Run."""

        lease = self.run_store.acquire_lease(
            handle.run_id, handle.worker_id, lease_seconds=self.lease_seconds
        )
        heartbeat = _LeaseHeartbeat(
            self.run_store,
            lease,
            interval_seconds=self.lease_renew_interval_seconds,
            lease_seconds=self.lease_seconds,
        )
        heartbeat.start()
        try:
            if self.operation_ledger is not None and self.operation_ledger.list_pending(
                handle.run_id
            ):
                heartbeat.transition("reconciling")
            state = self._restore(
                handle, agent, heartbeat.lease(), task, heartbeat=heartbeat
            )
            heartbeat.transition("running")
        except StateResourceLimitError:
            heartbeat.release(status="budget_exhausted")
            raise
        finally:
            heartbeat.stop()
        lease = heartbeat.lease()
        return state, self._events(
            handle,
            agent,
            state,
            lease=lease,
            max_turns=max_turns,
            abort=abort,
            agent_for_state=agent_for_state,
        )

    def _restore(
        self,
        handle: RecoverableRun,
        agent: Agent,
        lease: RunRecord,
        task: str | None,
        *,
        heartbeat: _LeaseHeartbeat,
    ) -> State:
        state = self.checkpoint_store.load(handle.checkpoint_id)
        state.write_guard = heartbeat.guard
        if self.workspace_manager is not None and lease.workspace_ref is not None:
            try:
                state.data["workspace_ref"] = lease.workspace_ref
                state.data["workspace_path"] = str(
                    self.workspace_manager.resolve(lease.workspace_ref)
                )
            except (FileNotFoundError, ValueError) as exc:
                self._save_evidence(state)
                heartbeat.release(status="blocked")
                raise RunControlError(
                    f"Run workspace cannot be recovered: {exc}"
                ) from exc
        if self.event_journal is not None:
            state = merge_checkpoint_with_journal(
                state, self.event_journal.read(handle.run_id)
            )
        state.data["run_id"] = handle.run_id
        state.data["fencing_token"] = lease.fencing_token
        if self.operation_ledger is not None:
            deadline = state.data.get("run_budget", {}).get("deadline")

            def recovery_adapter(operation):
                if deadline is not None and time.time() >= deadline:
                    raise StateResourceLimitError("deadline reached during recovery")
                with heartbeat.guard():
                    adapter = (
                        self.reconciler_for(operation) if self.reconciler_for else None
                    )
                if isinstance(adapter, BashOperationReconciler):
                    if deadline is not None:
                        adapter.timeout_seconds = min(
                            adapter.timeout_seconds, max(0.01, deadline - time.time())
                        )
                return adapter

            outcomes = reconcile_pending(
                GuardedOperationLedger(self.operation_ledger, heartbeat.guard),
                handle.run_id,
                recovery_adapter,
            )
            if deadline is not None and time.time() >= deadline:
                raise StateResourceLimitError("deadline reached during recovery")
            if any(outcome.status != "confirmed" for _, outcome in outcomes):
                self._save_evidence(state)
                heartbeat.release(status="blocked")
                raise RunControlError(
                    f"Run {handle.run_id!r} has an operation that could not be reconciled"
                )
        restore_tool_results(
            state, self.operation_ledger, str(state.data.get("agent_name", agent.name))
        )
        if not state.messages:
            if task is not None:
                state.task = task
            state.send("task", "user", agent.name, state.task)
        return state

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
        lease: RunRecord,
        agent_for_state: Callable[[State], Agent] | None,
        max_turns: int,
        abort: AbortFlag,
    ) -> Iterator[Event]:
        heartbeat = _LeaseHeartbeat(
            self.run_store,
            lease,
            interval_seconds=self.lease_renew_interval_seconds,
            lease_seconds=self.lease_seconds,
        )
        state.write_guard = heartbeat.guard
        if self.operation_ledger is not None:
            state.data["operation_ledger"] = GuardedOperationLedger(
                self.operation_ledger, heartbeat.guard
            )
        persisted = (
            len(self.event_journal.read(handle.run_id)) if self.event_journal else 0
        )
        last_checkpoint = 0
        released = False
        budget = state.data.get("run_budget", {})
        accounted = 0
        model_calls = 0
        tokens = 0

        def budget_reason(*, before_request: bool = False) -> str:
            nonlocal accounted, model_calls, tokens
            for observed in state.events[accounted:]:
                if isinstance(observed, ModelRequestEvent):
                    model_calls += 1
                elif (
                    isinstance(observed, ModelResponseEvent)
                    and observed.usage is not None
                ):
                    tokens += (
                        observed.usage.input_tokens
                        + observed.usage.output_tokens
                        + observed.usage.cache_read_tokens
                        + observed.usage.cache_write_tokens
                    )
            accounted = len(state.events)
            if budget.get("deadline") is not None and time.time() >= budget["deadline"]:
                return "durable wall-clock deadline reached"
            if budget.get("max_tokens") is not None and tokens >= budget["max_tokens"]:
                return "durable reported token budget reached"
            # A request event reserves its slot before generate() is called.
            if budget.get("max_model_calls") is not None and model_calls > budget[
                "max_model_calls"
            ] - (0 if before_request else 1):
                return "durable model-call budget reached"
            return ""

        def exhaust(reason: str) -> None:
            state.data["budget_reason"] = reason
            finish("budget_exhausted")

        def persist(*, checkpoint: bool = False) -> None:
            nonlocal persisted, last_checkpoint
            with heartbeat.guard():
                if self.event_journal is not None:
                    for event in state.events[persisted:]:
                        self.event_journal.append(handle.run_id, event)
                        persisted = event.index + 1
                if checkpoint:
                    self.checkpoint_store.save(handle.checkpoint_id, state)
                    last_checkpoint = len(state.events)
                heartbeat.update_progress(
                    latest_event_index=state.events[-1].index if state.events else -1,
                    checkpoint_event_index=(
                        state.events[-1].index if state.events else -1
                    )
                    if checkpoint
                    else None,
                )

        def finish(status: RunStatus) -> None:
            nonlocal released
            with heartbeat.guard():
                persist(checkpoint=True)
                self._save_evidence(state)
                heartbeat.release(status=status)
                released = True

        try:
            heartbeat.start()
            persist(checkpoint=True)
            last = state.events[-1] if state.events else None
            # A crash may happen after the verdict is durable but before release.
            if isinstance(last, GoalStatusEvent):
                if last.status != "active":
                    finish(cast(RunStatus, last.status))
                    return
                state.send(
                    "message",
                    "user",
                    agent.name,
                    f"External verification failed. Continue the original task: {last.reason}",
                )
                persist(checkpoint=True)
            if state.data.get("completion_required") and self.completion_check is None:
                status = self._completion_status(
                    state, agent, AgentEndEvent(reason="done")
                )
                finish(status)
                return

            reason = budget_reason(before_request=isinstance(last, AgentEndEvent))
            if reason:
                exhaust(reason)
                return
            if not isinstance(last, AgentEndEvent):
                attempts = int(
                    state.data.get(
                        "attempts_started",
                        sum(isinstance(event, AgentEndEvent) for event in state.events),
                    )
                )
                if attempts >= int(state.data.get("max_attempts", self.max_attempts)):
                    exhaust("durable attempt budget reached")
                    return
                state.data["attempts_started"] = attempts + 1
                persist(checkpoint=True)
                if agent_for_state is not None:
                    prepared = agent_for_state(state)
                    previous_name = state.data.get("agent_name")
                    if previous_name is not None and prepared.name != previous_name:
                        raise ValueError(
                            "agent_for_state must preserve the persisted agent name"
                        )
                    agent = prepared
                    state.data["agent_name"] = agent.name
                    persist(checkpoint=True)

            def combined_abort() -> bool:
                # Call slots are checked at ModelRequestEvent, before dispatch.
                deadline = budget.get("deadline")
                return (
                    abort()
                    or heartbeat.lost
                    or (deadline is not None and time.time() >= deadline)
                )

            # Re-run only the verifier if the previous worker durably ended a
            # model attempt but died before verifying it. Verifiers must be safe to repeat.
            events = (
                iter([last])
                if isinstance(last, AgentEndEvent)
                else run(agent, state, max_turns=max_turns, abort=combined_abort)
            )
            for event in events:
                persist(
                    checkpoint=isinstance(
                        event, (ModelRequestEvent, ModelResponseEvent)
                    )
                    or len(state.events) - last_checkpoint
                    >= self.checkpoint_every_events
                )
                if isinstance(event, (ModelRequestEvent, ModelResponseEvent)):
                    reason = budget_reason(before_request=True)
                    if reason:
                        exhaust(reason)
                        return
                yield event
                if isinstance(event, AgentEndEvent):
                    reason = budget_reason(before_request=True)
                    if reason:
                        exhaust(reason)
                        return
                    boundary = len(state.events)
                    status = self._completion_status(state, agent, event)
                    finish(status)
                    yield from state.events[boundary:]
                    return
        except StateResourceLimitError:
            # The rejected event is absent; preserve the last committed state
            # and stop scheduling retries that would hit the same resource limit.
            try:
                finish("budget_exhausted")
            except StateResourceLimitError:
                # SQLite events are already durable even if metadata cannot fit.
                heartbeat.release(status="budget_exhausted")
                released = True
            raise
        except LeaseLostError:
            raise
        except BaseException as exc:
            # Flush every recorded event before the snapshot so an exception
            # cannot leave a checkpoint ahead of its event journal.
            if not released:
                state.data["last_error"] = f"{type(exc).__name__}: {exc}"[:2000]
                exhausted = int(state.data.get("attempts_started", 0)) >= int(
                    state.data.get("max_attempts", self.max_attempts)
                )
                reason = (
                    "durable attempt budget reached" if exhausted else budget_reason()
                )
                if reason:
                    state.data["budget_reason"] = reason
                finish("budget_exhausted" if reason else "runnable")
            raise
        finally:
            heartbeat.stop()

    def _completion_status(
        self, state: State, agent: Agent, end: AgentEndEvent
    ) -> RunStatus:
        if end.reason == "abort":
            return "aborted"
        if self.completion_check is None:
            if state.data.get("completion_required"):
                state.record_event(
                    GoalStatusEvent(
                        objective=state.task
                        if isinstance(state.task, str)
                        else text_of(state.task),
                        status="blocked",
                        turns_used=0,
                        reason="Required completion verifier is missing after recovery",
                    )
                )
                return "blocked"
            if end.reason != "max_turns":
                return "finished"
            attempts = int(
                state.data.get(
                    "attempts_started",
                    sum(isinstance(event, AgentEndEvent) for event in state.events),
                )
            )
            if attempts >= int(state.data.get("max_attempts", self.max_attempts)):
                return "budget_exhausted"
            state.send(
                "message", "user", agent.name, "Continue working on the original task."
            )
            return "runnable"

        attempts = int(
            state.data.get(
                "attempts_started",
                sum(isinstance(event, AgentEndEvent) for event in state.events),
            )
        )
        limit = int(state.data.get("max_attempts", self.max_attempts))
        try:
            verdict = self.completion_check(state)
        except Exception as exc:
            verdict = CompletionResult(
                False,
                blocked=True,
                reason=f"Verifier failed: {type(exc).__name__}: {exc}",
            )
        deadline = state.data.get("run_budget", {}).get("deadline")
        expired = deadline is not None and time.time() >= deadline
        if expired:
            state.data["budget_reason"] = (
                "durable wall-clock deadline reached during verification"
            )
        status = (
            "budget_exhausted"
            if expired
            else "complete"
            if verdict.done
            else "blocked"
            if verdict.blocked
            else "budget_exhausted"
            if attempts >= limit
            else "active"
        )
        state.record_event(
            GoalStatusEvent(
                objective=state.task
                if isinstance(state.task, str)
                else text_of(state.task),
                status=status,
                turns_used=attempts,
                reason=verdict.reason,
            )
        )
        if status == "active":
            state.send(
                "message",
                "user",
                agent.name,
                "External verification has not passed. Continue working on the "
                f"original task. Verification feedback: {verdict.reason}",
            )
            return "runnable"
        return cast(RunStatus, status)


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

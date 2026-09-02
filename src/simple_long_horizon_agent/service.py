"""Service lifecycle facade for recoverable Agent Runs.

The service owns the process-level wiring around ``RecoveryScheduler``. It
does not keep Agent state in memory: submitted Runs are durable records, and a
caller-provided factory rebuilds an Agent for each recovery attempt.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import TypeVar

from .core import Agent
from .protocols import Event
from .recoverable_runtime import (
    RecoverableRun,
    RecoverableRunExecutor,
    RecoveryScanner,
    RecoveryScheduler,
)
from .run_control import RunRecord
from .state import State


AgentFactory = Callable[[RunRecord], Agent]
ServiceT = TypeVar("ServiceT", bound="RecoverableRuntimeService")


class RecoverableRuntimeService:
    """Small service entry point for durable submission and recovery.

    ``submit`` only writes a runnable Run. ``start`` enables background
    recovery; callers that own their process loop can use ``recover_once``
    instead. The Agent factory only rebuilds execution inputs; the executor's
    lease acquisition remains the authority for advancing a Run.
    """

    def __init__(
        self,
        executor: RecoverableRunExecutor,
        *,
        worker_id: str,
        agent_for: AgentFactory,
        poll_interval_seconds: float = 5.0,
        on_error: Callable[[RunRecord | None, BaseException], None] | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        self.executor = executor
        self.worker_id = worker_id
        self.agent_for = agent_for
        self.scheduler = RecoveryScheduler(
            RecoveryScanner(executor.run_store),
            worker_id=worker_id,
            recover=self._recover,
            poll_interval_seconds=poll_interval_seconds,
            on_error=on_error,
        )

    @property
    def running(self) -> bool:
        return self.scheduler.running

    def submit(
        self,
        run_id: str,
        task: str,
        *,
        workspace_source: str | None = None,
    ) -> RecoverableRun:
        """Durably create a runnable Run for later service execution."""

        return self.executor.create(
            run_id,
            State(task),
            worker_id=self.worker_id,
            workspace_source=workspace_source,
        )

    def recover_once(self) -> list[str]:
        """Scan and attempt each recoverable Run once."""

        return self.scheduler.run_once()

    def start(self) -> None:
        """Start background recovery after the service has been initialized."""

        self.scheduler.start()

    def stop(self, *, join_timeout: float | None = None) -> bool:
        """Stop new recovery attempts and report whether the scheduler drained."""

        return self.scheduler.stop(join_timeout=join_timeout)

    def _recover(self, record: RunRecord) -> None:
        handle = RecoverableRun(record.run_id, record.run_id, self.worker_id)
        agent = self.agent_for(record)
        _, events = self.executor.execute(handle, agent)
        self._drain(events)

    @staticmethod
    def _drain(events: Iterator[Event]) -> None:
        for _ in events:
            pass

    def __enter__(self: ServiceT) -> ServiceT:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop(join_timeout=None)


__all__ = ["AgentFactory", "RecoverableRuntimeService"]

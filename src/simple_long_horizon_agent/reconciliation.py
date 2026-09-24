"""Explicit reconciliation for side-effecting operations after interruption."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Protocol, Any, cast

from .run_control import OperationLedger, OperationRecord, operation_args_digest
from .messages import (
    TextBlock,
    ToolResultBlock,
    message_tool_calls,
    tool_results_of,
    tool_results_message,
)
from .state import State


ReconcileStatus = str


@dataclass(frozen=True)
class ReconcileResult:
    status: ReconcileStatus
    reason: str
    output: str | None = None
    exit_code: int | None = None


class OperationReconciler(Protocol):
    def reconcile(self, operation: OperationRecord) -> ReconcileResult: ...


class EditOperationReconciler:
    """Verify an edit by comparing the resulting file hash."""

    def __init__(self, *, workspace: str | Path) -> None:
        self.workspace = Path(workspace).resolve()

    def reconcile(self, operation: OperationRecord) -> ReconcileResult:
        metadata = operation.metadata or {}
        raw_path = metadata.get("path")
        expected_hash = metadata.get("new_sha256")
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
            return ReconcileResult("blocked", "edit operation lacks file hash metadata")
        path = Path(raw_path)
        if not path.is_absolute():
            path = self.workspace / path
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            actual_hash = None
        except OSError as exc:
            return ReconcileResult("blocked", f"cannot inspect edited file: {exc}")
        if actual_hash == expected_hash:
            return ReconcileResult(
                "confirmed", "file matches the recorded post-edit hash"
            )
        if "old_sha256" in metadata and actual_hash == metadata["old_sha256"]:
            from .tools.edit import edit_file

            args = metadata.get("arguments")
            if isinstance(args, dict):
                args = cast(dict[str, Any], args)
                edit_file(
                    str(path),
                    args["old_string"],
                    args["new_string"],
                    root=self.workspace,
                    replace_all=args.get("replace_all", False),
                )
                if (
                    path.exists()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
                ):
                    return ReconcileResult(
                        "confirmed",
                        "Unapplied edit replayed from matching pre-edit hash",
                    )
        return ReconcileResult(
            "blocked",
            "file matches neither a recoverable pre-edit state nor the post-edit hash",
        )


class BashOperationReconciler:
    """Retry only exact commands explicitly authorized by the caller.

    Approval means repeat execution is safe even if an interrupted child survived.
    Unknown commands remain blocked. A normal exit, including a failed test,
    confirms execution; the exit code and output remain model-visible.
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        commands: tuple[str, ...],
        timeout_seconds: float = 30,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.commands = commands
        self.timeout_seconds = timeout_seconds

    def reconcile(self, operation: OperationRecord) -> ReconcileResult:
        from .tools.bash import run_bash

        command = (operation.metadata or {}).get("retry_command")
        if not isinstance(command, str) or command not in self.commands:
            return ReconcileResult(
                "blocked", "command is not caller-approved for retry"
            )
        result = run_bash(
            command, cwd=self.workspace, timeout_seconds=self.timeout_seconds
        )
        if result.exit_code < 0:
            return ReconcileResult(
                "blocked",
                "approved recovery command did not succeed: "
                + result.raw_stderr[-2000:],
            )
        return ReconcileResult(
            "confirmed",
            "Caller-approved command finished during recovery.",
            output=result.raw_stdout[-8000:] + result.raw_stderr[-8000:],
            exit_code=result.exit_code,
        )


def reconcile_pending(
    ledger: OperationLedger,
    run_id: str,
    reconciler_for: Callable[[OperationRecord], OperationReconciler | None],
) -> list[tuple[OperationRecord, ReconcileResult]]:
    """Reconcile all pending operations for a Run using caller-owned adapters."""

    outcomes: list[tuple[OperationRecord, ReconcileResult]] = []
    for operation in ledger.list_pending(run_id):
        if operation.status == "blocked":
            outcomes.append(
                (
                    operation,
                    ReconcileResult("blocked", "operation was previously blocked"),
                )
            )
            continue
        reconciler = reconciler_for(operation)
        outcome = (
            reconciler.reconcile(operation)
            if reconciler is not None
            else ReconcileResult("blocked", "no reconciler is registered for this tool")
        )
        target = "confirmed" if outcome.status == "confirmed" else "blocked"
        updated = ledger.transition(
            operation,
            target,
            result={
                "reason": outcome.reason,
                "output": outcome.output,
                "exit_code": outcome.exit_code,
            },
        )
        outcomes.append((updated, outcome))
    return outcomes


def restore_tool_results(
    state: State, ledger: OperationLedger | None, agent: str
) -> None:
    """Close interrupted tool exchanges without replaying their side effects.

    An operation may be confirmed before the model-visible result was journaled.
    In that window we can report the durable outcome, but cannot invent lost output.
    """
    # Compression keeps call/result pairs together, so only active messages
    # can contain an interrupted exchange. Avoid indexing all historical IDs.
    active = state.active_context_messages()
    answered = {
        result.tool_call_id
        for message in active
        for result in tool_results_of(message.content)
    }
    missing = [
        call
        for message in active
        for call in message_tool_calls(message)
        if call.id not in answered
    ]
    if not missing:
        return
    results = []
    for call in missing:
        operation_id = (
            "op-"
            + operation_args_digest(
                {"idempotency_key": f"{state.data['run_id']}:{call.id}"}
            )[:32]
        )
        operation = None
        if ledger is not None:
            try:
                operation = ledger.get(operation_id)
            except FileNotFoundError:
                pass
        confirmed = operation is not None and operation.status in {
            "confirmed",
            "reconciled",
        }
        reason = (
            "Recovery confirmed this operation. "
            + str((operation.result or {}).get("reason", ""))
            + " "
            "Original tool output was not durably recorded. Inspect current state if needed."
            if confirmed
            else "Execution was interrupted before a durable result was recorded. "
            "No successful result is available; inspect current state before retrying."
        )
        recovered = operation.result if operation is not None else None
        exit_code = (recovered or {}).get("exit_code")
        if exit_code is not None:
            reason = f"Recovery re-executed command; exit code {exit_code}\n{(recovered or {}).get('output', '')}"
        results.append(
            ToolResultBlock(
                tool_call_id=call.id,
                tool_name=call.name,
                content=(TextBlock(reason),),
                is_error=not confirmed or (exit_code is not None and exit_code != 0),
            )
        )
    state.record(tool_results_message(results, target=agent))


__all__ = [
    "EditOperationReconciler",
    "OperationReconciler",
    "ReconcileResult",
    "reconcile_pending",
]

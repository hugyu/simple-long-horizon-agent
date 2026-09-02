"""Explicit reconciliation for side-effecting operations after interruption."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from .run_control import OperationLedger, OperationRecord


ReconcileStatus = str


@dataclass(frozen=True)
class ReconcileResult:
    status: ReconcileStatus
    reason: str


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
        except OSError as exc:
            return ReconcileResult("blocked", f"cannot inspect edited file: {exc}")
        if actual_hash == expected_hash:
            return ReconcileResult(
                "confirmed", "file matches the recorded post-edit hash"
            )
        return ReconcileResult(
            "blocked", "file does not match the recorded post-edit hash"
        )


def reconcile_pending(
    ledger: OperationLedger,
    run_id: str,
    reconciler_for: Callable[[OperationRecord], OperationReconciler | None],
) -> list[tuple[OperationRecord, ReconcileResult]]:
    """Reconcile all pending operations for a Run using caller-owned adapters."""

    outcomes: list[tuple[OperationRecord, ReconcileResult]] = []
    for operation in ledger.list_pending(run_id):
        reconciler = reconciler_for(operation)
        outcome = (
            reconciler.reconcile(operation)
            if reconciler is not None
            else ReconcileResult("blocked", "no reconciler is registered for this tool")
        )
        target = "confirmed" if outcome.status == "confirmed" else "blocked"
        updated = ledger.transition(
            operation, target, result={"reason": outcome.reason}
        )
        outcomes.append((updated, outcome))
    return outcomes


__all__ = [
    "EditOperationReconciler",
    "OperationReconciler",
    "ReconcileResult",
    "reconcile_pending",
]

"""Small durable control-plane protocols for recoverable Agent runs.

This module intentionally implements the Phase 2 contract on the local
filesystem.  A database-backed implementation can replace these stores later;
the Runtime-facing invariants stay the same: conditional versions, lease
fencing, and idempotent tool operation identities.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, is_dataclass, replace
from pathlib import Path
from typing import Any, cast, Literal, Protocol

from filelock import FileLock


RunStatus = Literal[
    "created",
    "runnable",
    "leased",
    "running",
    "waiting_external",
    "reconciling",
    "complete",
    "blocked",
    "aborted",
    "failed",
]

OperationStatus = Literal[
    "created",
    "intent_recorded",
    "started",
    "confirmed",
    "unknown",
    "reconciled",
]


class RunControlError(RuntimeError):
    """Base error for rejected control-plane updates."""


class VersionConflict(RunControlError):
    """The caller is updating an out-of-date record."""


class LeaseConflict(RunControlError):
    """The caller does not hold the current valid lease."""


class OperationConflict(RunControlError):
    """An idempotency key was reused for a different operation."""


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    status: RunStatus = "created"
    version: int = 0
    latest_event_index: int = -1
    checkpoint_event_index: int | None = None
    config_fingerprint: str = ""
    tool_registry_version: str = ""
    budget: dict[str, int | float | None] | None = None
    lease_owner: str | None = None
    lease_expires_at: float | None = None
    fencing_token: int = 0


class RunStore(Protocol):
    def create(self, record: RunRecord) -> RunRecord: ...

    def get(self, run_id: str) -> RunRecord: ...

    def acquire_lease(
        self, run_id: str, worker_id: str, *, lease_seconds: float
    ) -> RunRecord: ...

    def renew_lease(self, record: RunRecord, *, lease_seconds: float) -> RunRecord: ...

    def transition(self, record: RunRecord, status: RunStatus) -> RunRecord: ...

    def release_lease(self, record: RunRecord) -> RunRecord: ...


class FileRunStore:
    """JSON RunRecord store with process-safe lock and conditional updates."""

    def __init__(
        self, root: str | Path, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.root = Path(root)
        self._clock = clock

    def _path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError(f"run_id must be a simple name, got {run_id!r}")
        return self.root / f"{run_id}.json"

    def _lock(self, run_id: str) -> FileLock:
        return FileLock(str(self.root / f".{run_id}.lock"))

    def create(self, record: RunRecord) -> RunRecord:
        path = self._path(record.run_id)
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock(record.run_id):
            if path.exists():
                raise RunControlError(f"Run already exists: {record.run_id}")
            _atomic_write(path, record)
        return record

    def get(self, run_id: str) -> RunRecord:
        path = self._path(run_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"Run not found: {run_id}") from None
        return _run_from_payload(payload)

    def acquire_lease(
        self, run_id: str, worker_id: str, *, lease_seconds: float
    ) -> RunRecord:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        path = self._path(run_id)
        with self._lock(run_id):
            current = self.get(run_id)
            now = self._clock()
            active = (
                current.lease_expires_at is not None and current.lease_expires_at > now
            )
            if active:
                raise LeaseConflict(
                    f"Run {run_id!r} is leased by {current.lease_owner!r}"
                )
            if current.status not in {
                "created",
                "runnable",
                "leased",
                "running",
                "waiting_external",
                "reconciling",
            }:
                raise RunControlError(
                    f"Run {run_id!r} is not leasable in status {current.status!r}"
                )
            updated = replace(
                current,
                status="leased",
                version=current.version + 1,
                lease_owner=worker_id,
                lease_expires_at=now + lease_seconds,
                fencing_token=current.fencing_token + 1,
            )
            _atomic_write(path, updated)
            return updated

    def renew_lease(self, record: RunRecord, *, lease_seconds: float) -> RunRecord:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be greater than zero")
        path = self._path(record.run_id)
        with self._lock(record.run_id):
            current = self.get(record.run_id)
            self._assert_lease(current, record)
            updated = replace(
                current,
                version=current.version + 1,
                lease_expires_at=self._clock() + lease_seconds,
            )
            _atomic_write(path, updated)
            return updated

    def transition(self, record: RunRecord, status: RunStatus) -> RunRecord:
        path = self._path(record.run_id)
        with self._lock(record.run_id):
            current = self.get(record.run_id)
            self._assert_lease(current, record)
            updated = replace(current, status=status, version=current.version + 1)
            _atomic_write(path, updated)
            return updated

    def release_lease(self, record: RunRecord) -> RunRecord:
        path = self._path(record.run_id)
        with self._lock(record.run_id):
            current = self.get(record.run_id)
            self._assert_lease(current, record)
            updated = replace(
                current,
                status="runnable",
                version=current.version + 1,
                lease_owner=None,
                lease_expires_at=None,
            )
            _atomic_write(path, updated)
            return updated

    def _assert_lease(self, current: RunRecord, expected: RunRecord) -> None:
        if current.version != expected.version:
            raise VersionConflict(
                f"Run {expected.run_id!r} version changed: expected {expected.version}, got {current.version}"
            )
        if (
            current.lease_owner != expected.lease_owner
            or current.fencing_token != expected.fencing_token
            or current.lease_owner is None
            or current.lease_expires_at is None
            or current.lease_expires_at <= self._clock()
        ):
            raise LeaseConflict(f"Lease is no longer valid for run {expected.run_id!r}")


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    run_id: str
    tool_call_id: str
    tool_name: str
    idempotency_key: str
    args_digest: str
    status: OperationStatus = "created"
    version: int = 0
    result: Mapping[str, object] | None = None


class OperationLedger(Protocol):
    def create_intent(self, record: OperationRecord) -> OperationRecord: ...

    def get(self, operation_id: str) -> OperationRecord: ...

    def transition(
        self,
        record: OperationRecord,
        status: OperationStatus,
        *,
        result: Mapping[str, object] | None = None,
    ) -> OperationRecord: ...


class FileOperationLedger:
    """Filesystem operation ledger with idempotency-key deduplication."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, operation_id: str) -> Path:
        if not operation_id or Path(operation_id).name != operation_id:
            raise ValueError(
                f"operation_id must be a simple name, got {operation_id!r}"
            )
        return self.root / f"{operation_id}.json"

    def _index_path(self) -> Path:
        return self.root / "idempotency.json"

    def create_intent(self, record: OperationRecord) -> OperationRecord:
        if not record.idempotency_key:
            raise ValueError("idempotency_key must not be empty")
        self.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.root / ".ledger.lock"))
        with lock:
            index = self._read_index()
            existing_id = index.get(record.idempotency_key)
            if existing_id is not None:
                existing = self.get(existing_id)
                if (
                    existing.run_id != record.run_id
                    or existing.tool_name != record.tool_name
                    or existing.args_digest != record.args_digest
                ):
                    raise OperationConflict(
                        f"Idempotency key {record.idempotency_key!r} was reused for a different operation"
                    )
                return existing
            if self._path(record.operation_id).exists():
                raise OperationConflict(
                    f"Operation already exists: {record.operation_id}"
                )
            _atomic_write(self._path(record.operation_id), record)
            index[record.idempotency_key] = record.operation_id
            _atomic_write(self._index_path(), index)
            return record

    def get(self, operation_id: str) -> OperationRecord:
        try:
            payload = json.loads(self._path(operation_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"Operation not found: {operation_id}") from None
        return _operation_from_payload(payload)

    def transition(
        self,
        record: OperationRecord,
        status: OperationStatus,
        *,
        result: Mapping[str, object] | None = None,
    ) -> OperationRecord:
        path = self._path(record.operation_id)
        lock = FileLock(str(self.root / ".ledger.lock"))
        with lock:
            current = self.get(record.operation_id)
            if current.version != record.version:
                raise VersionConflict(
                    f"Operation {record.operation_id!r} version changed: expected {record.version}, got {current.version}"
                )
            if status not in _ALLOWED_OPERATION_TRANSITIONS.get(current.status, ()):
                raise OperationConflict(
                    f"Invalid operation transition {current.status!r} -> {status!r}"
                )
            updated = replace(
                current,
                status=status,
                version=current.version + 1,
                result=result if result is not None else current.result,
            )
            _atomic_write(path, updated)
            return updated

    def _read_index(self) -> dict[str, str]:
        try:
            value = json.loads(self._index_path().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(value, dict):
            raise ValueError("Operation ledger idempotency index must be an object")
        return {str(key): str(item) for key, item in value.items()}


_ALLOWED_OPERATION_TRANSITIONS: dict[OperationStatus, tuple[OperationStatus, ...]] = {
    "created": ("intent_recorded",),
    "intent_recorded": ("started", "unknown"),
    "started": ("confirmed", "unknown"),
    "confirmed": ("reconciled",),
    "unknown": ("reconciled", "confirmed"),
    "reconciled": (),
}


def operation_args_digest(arguments: Mapping[str, object]) -> str:
    """Hash tool arguments without persisting the raw values in the ledger."""

    encoded = json.dumps(
        _json_safe(arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_from_payload(payload: object) -> RunRecord:
    if not isinstance(payload, Mapping):
        raise ValueError("Run record must be a JSON object")
    values = cast(Mapping[str, Any], payload)
    status = str(values.get("status") or "created")
    if status not in _RUN_STATUSES:
        raise ValueError(f"Unsupported run status: {status!r}")
    return RunRecord(
        run_id=str(values.get("run_id") or ""),
        status=cast(RunStatus, status),
        version=int(values.get("version", 0)),
        latest_event_index=int(values.get("latest_event_index", -1)),
        checkpoint_event_index=(
            int(values["checkpoint_event_index"])
            if values.get("checkpoint_event_index") is not None
            else None
        ),
        config_fingerprint=str(values.get("config_fingerprint") or ""),
        tool_registry_version=str(values.get("tool_registry_version") or ""),
        budget=dict(values["budget"])
        if isinstance(values.get("budget"), Mapping)
        else None,
        lease_owner=str(values["lease_owner"])
        if values.get("lease_owner") is not None
        else None,
        lease_expires_at=float(values["lease_expires_at"])
        if values.get("lease_expires_at") is not None
        else None,
        fencing_token=int(values.get("fencing_token", 0)),
    )


def _operation_from_payload(payload: object) -> OperationRecord:
    if not isinstance(payload, Mapping):
        raise ValueError("Operation record must be a JSON object")
    values = cast(Mapping[str, Any], payload)
    status = str(values.get("status") or "created")
    if status not in _ALLOWED_OPERATION_TRANSITIONS:
        raise ValueError(f"Unsupported operation status: {status!r}")
    result = values.get("result")
    return OperationRecord(
        operation_id=str(values.get("operation_id") or ""),
        run_id=str(values.get("run_id") or ""),
        tool_call_id=str(values.get("tool_call_id") or ""),
        tool_name=str(values.get("tool_name") or ""),
        idempotency_key=str(values.get("idempotency_key") or ""),
        args_digest=str(values.get("args_digest") or ""),
        status=cast(OperationStatus, status),
        version=int(values.get("version", 0)),
        result=dict(result) if isinstance(result, dict) else None,
    )


def _json_safe(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if is_dataclass(value):
        return _json_safe(asdict(cast(Any, value)))
    return repr(value)


_RUN_STATUSES = {
    "created",
    "runnable",
    "leased",
    "running",
    "waiting_external",
    "reconciling",
    "complete",
    "blocked",
    "aborted",
    "failed",
}


def _atomic_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    )
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".part", dir=path.parent
    )
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

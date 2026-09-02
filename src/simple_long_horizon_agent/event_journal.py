"""Append-only event journal for recoverable Agent runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from .checkpoint import _event_from_record, _event_to_record
from .protocols import Event


class EventJournal(Protocol):
    def append(self, run_id: str, event: Event) -> None: ...

    def read(self, run_id: str) -> list[Event]: ...


class FileEventJournal:
    """One fsynced JSONL stream per Run with monotonic event indexes."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError(f"run_id must be a simple name, got {run_id!r}")
        return self.root / f"{run_id}.jsonl"

    def append(self, run_id: str, event: Event) -> None:
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read(run_id)
        expected = existing[-1].index + 1 if existing else 0
        if event.index < expected:
            if event.index < len(existing) and existing[event.index] == event:
                return
            raise ValueError(
                f"Event journal for {run_id!r} already contains a different event at index {event.index}"
            )
        if event.index != expected:
            raise ValueError(
                f"Event journal for {run_id!r} expected index {expected}, got {event.index}"
            )
        encoded = json.dumps(
            _event_to_record(event), ensure_ascii=False, sort_keys=True
        )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass

    def read(self, run_id: str) -> list[Event]:
        path = self._path(run_id)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events: list[Event] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                payload: Any = json.loads(line)
                event = _event_from_record(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Invalid event journal record at {path}:{line_number}"
                ) from exc
            if event.index != len(events):
                raise ValueError(
                    f"Event journal at {path}:{line_number} has non-contiguous index {event.index}"
                )
            events.append(event)
        return events


__all__ = ["EventJournal", "FileEventJournal"]

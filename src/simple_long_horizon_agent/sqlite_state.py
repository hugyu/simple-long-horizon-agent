"""Bounded, disk-backed State with transactional event and message projections.

One SQLite file owns the complete history, active view, and metadata checkpoint.
Readers hold fixed-length lazy views; no decoded history is cached in Python.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar, cast, overload

from .checkpoint import (
    _content_input_from_record,
    _content_input_to_record,
    _event_from_record,
    _event_to_record,
    _json_safe,
)
from .messages import ContentInput, Message
from .protocols import ContextCompressionEvent, Event, MessageEvent
from .state import State, StateResourceLimitError


@dataclass(frozen=True)
class StateLimits:
    """Serialized byte budgets, not a promise about total process RSS."""

    max_event_bytes: int = 8 * 1024 * 1024
    max_active_bytes: int = 8 * 1024 * 1024
    max_active_messages: int = 2048
    max_history_bytes: int = 256 * 1024 * 1024
    max_metadata_bytes: int = 64 * 1024
    max_database_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if self.max_database_bytes < 65536:
            raise ValueError("max_database_bytes must be at least 65536")


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _check(name: str, size: int, limit: int) -> None:
    if size > limit:
        raise StateResourceLimitError(f"{name}: {size} exceeds limit {limit}")


T = TypeVar("T")


class _HistoryView(Sequence[T], Generic[T]):
    """A stable prefix or slice; indexing decodes only the requested record."""

    def __init__(self, history: _SqliteHistory, messages: bool, indices: range) -> None:
        self.history = history
        self.is_messages = messages
        self.indices = indices

    def __len__(self) -> int:
        return len(self.indices)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[T]: ...

    def __getitem__(self, index: int | slice) -> T | Sequence[T]:
        selected = self.indices[index]
        if isinstance(selected, range):
            return _HistoryView(self.history, self.is_messages, selected)
        return cast(T, self.history.read(selected, messages=self.is_messages))

    def __iter__(self) -> Iterator[T]:
        for index in range(len(self)):
            yield self[index]


class _SqliteHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self.connect() as db:
            row = db.execute(
                "SELECT limits, event_count, message_count FROM state WHERE id=1"
            ).fetchone()
        if row is None:
            raise ValueError(f"Uninitialized state database: {path}")
        self.limits = StateLimits(**json.loads(row[0]))
        self.event_count, self.message_count = row[1:]

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        # mode=rw avoids silently creating a fresh database if history is lost.
        db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=30)
        try:
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA synchronous=FULL")
            if hasattr(self, "limits"):
                page_size = db.execute("PRAGMA page_size").fetchone()[0]
                pages = self.limits.max_database_bytes // page_size
                db.execute(f"PRAGMA max_page_count={pages}")
            yield db
        except sqlite3.OperationalError as exc:
            # SQLITE_FULL is 13; named error constants need Python 3.11+.
            if (
                getattr(exc, "sqlite_errorcode", None) == 13
                or str(exc) == "database or disk is full"
            ):
                raise StateResourceLimitError("SQLite storage is full") from exc
            raise
        finally:
            db.close()

    @property
    def events(self) -> Sequence[Event]:
        return _HistoryView(self, False, range(self.event_count))

    @property
    def messages(self) -> Sequence[Message]:
        return _HistoryView(self, True, range(self.message_count))

    @property
    def active_context_indices(self) -> list[int]:
        with self.connect() as db:
            return [
                r[0]
                for r in db.execute(
                    "SELECT message_index FROM active ORDER BY position"
                )
            ]

    def read(self, index: int, *, messages: bool) -> Event | Message:
        query = (
            "SELECT e.payload FROM messages m JOIN events e ON e.id=m.event_index WHERE m.id=?"
            if messages
            else "SELECT payload FROM events WHERE id=?"
        )
        with self.connect() as db:
            row = db.execute(query, (index,)).fetchone()
        if row is None:
            raise ValueError(f"Missing history record {index} in {self.path}")
        event = _event_from_record(json.loads(row[0]))
        if messages:
            if not isinstance(event, MessageEvent):
                raise ValueError(f"Message {index} points to a non-message event")
            return event.message
        return event

    def append(self, event: Event) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._append(db, event)
            db.commit()
        self.event_count += 1
        if isinstance(event, MessageEvent):
            self.message_count += 1

    def _append(self, db: sqlite3.Connection, event: Event) -> None:
        count, message_count, history_bytes = db.execute(
            "SELECT event_count, message_count, history_bytes FROM state WHERE id=1"
        ).fetchone()
        if event.index != count:
            raise ValueError(
                f"History expected event index {count}, got {event.index}; reload state"
            )
        encoded = _encode(_event_to_record(event))
        size = len(encoded.encode("utf-8"))
        _check("event bytes", size, self.limits.max_event_bytes)
        _check("history bytes", history_bytes + size, self.limits.max_history_bytes)
        db.execute("INSERT INTO events VALUES (?, ?)", (count, encoded))
        if isinstance(event, MessageEvent):
            message_size = len(_encode(_json_safe(event.message)).encode("utf-8"))
            db.execute(
                "INSERT INTO messages VALUES (?, ?, ?)",
                (message_count, count, message_size),
            )
            position = db.execute(
                "SELECT COALESCE(MAX(position), -1)+1 FROM active"
            ).fetchone()[0]
            db.execute("INSERT INTO active VALUES (?, ?)", (position, message_count))
            message_count += 1
        elif isinstance(event, ContextCompressionEvent):
            indices = event.active_context_indices
            if len(indices) != len(set(indices)) or any(
                i < 0 or i >= message_count for i in indices
            ):
                raise ValueError(f"Invalid active message indices: {indices!r}")
            db.execute("DELETE FROM active")
            db.executemany("INSERT INTO active VALUES (?, ?)", enumerate(indices))
        active_count, active_bytes = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(m.bytes), 0) FROM active a JOIN messages m ON m.id=a.message_index"
        ).fetchone()
        _check("active messages", active_count, self.limits.max_active_messages)
        _check("active bytes", active_bytes, self.limits.max_active_bytes)
        db.execute(
            "UPDATE state SET event_count=?, message_count=?, history_bytes=? WHERE id=1",
            (count + 1, message_count, history_bytes + size),
        )


class SqliteCheckpointStore:
    """Opt-in CheckpointStore that loads lazy State histories, not full prefixes.

    Do not pair this store with a separate EventJournal: SQLite commits each
    event and projection already. save() checkpoints task/data; load() sees all
    committed events, including those newer than the last metadata checkpoint.
    """

    def __init__(self, root: str | Path, *, limits: StateLimits | None = None) -> None:
        self.root = Path(root).expanduser().resolve()
        self.limits = limits or StateLimits()

    def path_for(self, checkpoint_id: str) -> Path:
        if (
            not checkpoint_id
            or Path(checkpoint_id).name != checkpoint_id
            or checkpoint_id in {".", ".."}
        ):
            raise ValueError(
                f"checkpoint_id must be a simple file name, got {checkpoint_id!r}"
            )
        return self.root / f"{checkpoint_id}.sqlite3"

    def create(self, checkpoint_id: str, task: ContentInput) -> State:
        self.save(checkpoint_id, State(task))
        return self.load(checkpoint_id)

    def save(self, checkpoint_id: str, state: State) -> Path:
        path = self.path_for(checkpoint_id)
        metadata = _encode(
            {
                "task": _content_input_to_record(state.task),
                "data": _json_safe(state.data),
            }
        )
        if state.history is not None:
            if (
                not isinstance(state.history, _SqliteHistory)
                or state.history.path != path
            ):
                raise ValueError(
                    "Cannot checkpoint a different history into this database"
                )
            history = state.history
            _check(
                "metadata bytes",
                len(metadata.encode("utf-8")),
                history.limits.max_metadata_bytes,
            )
            with state.write_guard(), history.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT event_count FROM state WHERE id=1"
                ).fetchone()[0]
                if current != len(state.events):
                    raise ValueError("Cannot checkpoint stale state; reload state")
                db.execute(
                    "UPDATE state SET metadata=?, checkpoint_events=? WHERE id=1",
                    (metadata, current),
                )
                db.commit()
            return path
        _check(
            "metadata bytes",
            len(metadata.encode("utf-8")),
            self.limits.max_metadata_bytes,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidentally overwriting a live run.
        with path.open("xb"):
            pass
        db = sqlite3.connect(path)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA synchronous=FULL")
            page_size = db.execute("PRAGMA page_size").fetchone()[0]
            db.execute(
                f"PRAGMA max_page_count={self.limits.max_database_bytes // page_size}"
            )
            db.executescript("""
                CREATE TABLE state (
                    id INTEGER PRIMARY KEY CHECK(id=1), schema_version INTEGER,
                    limits TEXT, metadata TEXT, event_count INTEGER,
                    message_count INTEGER, history_bytes INTEGER, checkpoint_events INTEGER
                );
                CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE messages (id INTEGER PRIMARY KEY, event_index INTEGER UNIQUE, bytes INTEGER);
                CREATE TABLE active (position INTEGER PRIMARY KEY, message_index INTEGER UNIQUE);
            """)
            db.execute(
                "INSERT INTO state VALUES (1, 0, ?, ?, 0, 0, 0, 0)",
                (_encode(asdict(self.limits)), metadata),
            )
            db.commit()
            history = _SqliteHistory(path)
            db.execute("BEGIN IMMEDIATE")
            for event in state.events:
                history._append(db, event)
            db.execute(
                "UPDATE state SET checkpoint_events=event_count, schema_version=1 WHERE id=1"
            )
            db.commit()
        except BaseException:
            db.close()
            path.unlink(missing_ok=True)
            raise
        finally:
            db.close()
        return path

    def load(self, checkpoint_id: str) -> State:
        path = self.path_for(checkpoint_id)
        if not path.is_file():
            raise FileNotFoundError(f"State database not found: {path}")
        history = _SqliteHistory(path)
        with history.connect() as db:
            version, metadata = db.execute(
                "SELECT schema_version, metadata FROM state WHERE id=1"
            ).fetchone()
        if version != 1:
            raise ValueError(f"Unsupported state schema: {version}")
        payload = json.loads(metadata)
        state = State(task=_content_input_from_record(payload["task"]), history=history)
        state.data.update(payload["data"])
        if state.events:
            state._monotonic_origin = time.monotonic() - state.events[-1].elapsed
        return state

"""Small filesystem workspace manager for recoverable code-task Runs."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class WorkspaceRef:
    """Stable, serializable identity for one Run's working directory."""

    workspace_id: str


class WorkspaceManager(Protocol):
    def create(
        self, run_id: str, *, source: str | Path | None = None
    ) -> WorkspaceRef: ...

    def resolve(self, ref: WorkspaceRef | str) -> Path: ...

    def release(self, ref: WorkspaceRef | str, *, remove: bool = False) -> None: ...

    def gc(self, *, older_than_seconds: float) -> list[WorkspaceRef]: ...


class FileWorkspaceManager:
    """Keep one stable directory per Run under a caller-owned root.

    The workspace id is deliberately a simple name and never accepts a path.
    ``release`` only removes a directory when ``remove=True``; a normal Run
    completion can therefore retain artifacts for inspection and recovery.
    """

    def __init__(
        self, root: str | Path, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.root = Path(root).resolve()
        self._clock = clock

    def create(self, run_id: str, *, source: str | Path | None = None) -> WorkspaceRef:
        self._validate_id(run_id)
        path = self.root / run_id
        if path.exists():
            raise FileExistsError(f"Workspace already exists: {run_id}")
        self.root.mkdir(parents=True, exist_ok=True)
        if source is None:
            path.mkdir()
        else:
            source_path = Path(source).resolve()
            if not source_path.is_dir():
                raise ValueError(f"Workspace source must be a directory: {source!r}")
            shutil.copytree(source_path, path)
        return WorkspaceRef(run_id)

    def resolve(self, ref: WorkspaceRef | str) -> Path:
        workspace_id = self._id_from_ref(ref)
        path = (self.root / workspace_id).resolve()
        if path.parent != self.root:
            raise ValueError(
                f"Workspace reference escapes manager root: {workspace_id!r}"
            )
        if not path.is_dir():
            raise FileNotFoundError(f"Workspace not found: {workspace_id}")
        return path

    def release(self, ref: WorkspaceRef | str, *, remove: bool = False) -> None:
        path = self.resolve(ref)
        if remove:
            shutil.rmtree(path)

    def gc(self, *, older_than_seconds: float) -> list[WorkspaceRef]:
        if older_than_seconds < 0:
            raise ValueError("older_than_seconds must be non-negative")
        if not self.root.exists():
            return []
        cutoff = self._clock() - older_than_seconds
        removed: list[WorkspaceRef] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir() or path.name.startswith("."):
                continue
            if path.stat().st_mtime <= cutoff:
                shutil.rmtree(path)
                removed.append(WorkspaceRef(path.name))
        return removed

    @staticmethod
    def _validate_id(workspace_id: str) -> None:
        if not workspace_id or Path(workspace_id).name != workspace_id:
            raise ValueError(
                f"workspace_id must be a simple name, got {workspace_id!r}"
            )

    @classmethod
    def _id_from_ref(cls, ref: WorkspaceRef | str) -> str:
        workspace_id = ref.workspace_id if isinstance(ref, WorkspaceRef) else ref
        cls._validate_id(workspace_id)
        return workspace_id


__all__ = ["FileWorkspaceManager", "WorkspaceManager", "WorkspaceRef"]

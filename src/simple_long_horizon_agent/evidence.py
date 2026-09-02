"""Pure evidence summaries derived from a recoverable Agent State."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from typing import Any

from .protocols import AgentEndEvent, SkillInvokedEvent, ToolExecutionEndEvent


@dataclass(frozen=True)
class EvidencePack:
    """JSON-shaped summary for humans, evaluators, and recovery tooling."""

    run_id: str
    workspace_ref: str | None
    skills: tuple[dict[str, str], ...]
    tool_calls: int
    tool_errors: int
    verification: Any
    stop_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workspace_ref": self.workspace_ref,
            "skills": [dict(item) for item in self.skills],
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "verification": self.verification,
            "stop_reason": self.stop_reason,
        }


class EvidenceStore(Protocol):
    """Persistence boundary for the latest evidence summary of a Run."""

    def save(self, run_id: str, pack: EvidencePack) -> Path: ...

    def load(self, run_id: str) -> EvidencePack: ...


class FileEvidenceStore:
    """Atomically persist one JSON evidence pack per Run."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id:
            raise ValueError(f"run_id must be a simple name, got {run_id!r}")
        return self.root / f"{run_id}.json"

    def save(self, run_id: str, pack: EvidencePack) -> Path:
        if pack.run_id and pack.run_id != run_id:
            raise ValueError("evidence pack run_id does not match the requested run")
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(_json_safe(pack.as_dict()), ensure_ascii=False, indent=2) + "\n"
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
        return path

    def load(self, run_id: str) -> EvidencePack:
        path = self._path(run_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"Evidence pack not found: {run_id}") from None
        if not isinstance(payload, dict):
            raise ValueError("evidence pack must contain a JSON object")
        skills = payload.get("skills", [])
        if not isinstance(skills, list):
            raise ValueError("evidence pack skills must be a list")
        return EvidencePack(
            run_id=str(payload.get("run_id") or run_id),
            workspace_ref=(
                str(payload["workspace_ref"])
                if payload.get("workspace_ref") is not None
                else None
            ),
            skills=tuple(
                {str(key): str(value) for key, value in item.items()}
                for item in skills
                if isinstance(item, dict)
            ),
            tool_calls=int(payload.get("tool_calls", 0) or 0),
            tool_errors=int(payload.get("tool_errors", 0) or 0),
            verification=payload.get("verification"),
            stop_reason=(
                str(payload["stop_reason"])
                if payload.get("stop_reason") is not None
                else None
            ),
        )


def evidence_pack_from_state(state: Any) -> EvidencePack:
    """Build an evidence summary without reading external files or services."""

    skills = tuple(
        {
            "name": event.skill_name,
            "version": event.version,
            "input_sha256": event.input_sha256,
            "trigger": event.trigger,
            "source": event.source,
        }
        for event in state.events
        if isinstance(event, SkillInvokedEvent)
    )
    tool_end_events = [
        event for event in state.events if isinstance(event, ToolExecutionEndEvent)
    ]
    end_events = [event for event in state.events if isinstance(event, AgentEndEvent)]
    return EvidencePack(
        run_id=str(state.data.get("run_id") or ""),
        workspace_ref=(
            str(state.data["workspace_ref"])
            if state.data.get("workspace_ref") is not None
            else None
        ),
        skills=skills,
        tool_calls=len(tool_end_events),
        tool_errors=sum(1 for event in tool_end_events if event.is_error),
        verification=state.data.get("verification"),
        stop_reason=end_events[-1].reason if end_events else None,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


__all__ = [
    "EvidencePack",
    "EvidenceStore",
    "FileEvidenceStore",
    "evidence_pack_from_state",
]

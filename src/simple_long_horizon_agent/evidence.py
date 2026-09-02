"""Pure evidence summaries derived from a recoverable Agent State."""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = ["EvidencePack", "evidence_pack_from_state"]

"""Versioned persistence for an in-progress Agent ``State``.

Checkpoints are a recovery aid, not a replacement for the event log.  The
payload contains the complete typed event prefix plus the run-level scratch
data, so loading it rebuilds ``StateSnapshot`` and preserves the same evidence
that an in-process ``resume`` call would see.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from .llm.types import llm_message
from .messages import (
    AssistantMessage,
    ContentBlock,
    ContentInput,
    ImageBlock,
    Message,
    MessageKind,
    MessageSidecar,
    Role,
    TextBlock,
    ThinkingBlock,
    TokenUsage,
    ToolCallBlock,
    ToolResultBlock,
    make_message,
)
from .protocols import (
    AgentStartEvent,
    AgentEndEvent,
    ContextCompressionEvent,
    Event,
    GoalStatusEvent,
    HookFiredEvent,
    SkillInvokedEvent,
    MessageEvent,
    ModelRequestEvent,
    ModelResponseEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    ToolExecutionUpdateEvent,
    TurnEndEvent,
    TurnStartEvent,
)
from .state import State
from .tools import ToolResult


CHECKPOINT_SCHEMA = "simple-long-horizon-agent.checkpoint.v1"


class CheckpointStore(Protocol):
    """Persistence boundary for versioned State checkpoints."""

    def save(self, checkpoint_id: str, state: State) -> Path: ...

    def load(self, checkpoint_id: str) -> State: ...


class FileCheckpointStore:
    """Store checkpoints as atomically replaced JSON files."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, checkpoint_id: str) -> Path:
        if not checkpoint_id or Path(checkpoint_id).name != checkpoint_id:
            raise ValueError(
                f"checkpoint_id must be a simple file name, got {checkpoint_id!r}"
            )
        return self.root / f"{checkpoint_id}.json"

    def save(self, checkpoint_id: str, state: State) -> Path:
        path = self.path_for(checkpoint_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = state_to_checkpoint(state)
        encoded = (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
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

    def load(self, checkpoint_id: str) -> State:
        path = self.path_for(checkpoint_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise FileNotFoundError(f"Checkpoint not found: {path}") from None
        if not isinstance(payload, Mapping):
            raise ValueError("checkpoint must contain a JSON object")
        return state_from_checkpoint(payload)


def state_to_checkpoint(state: State) -> dict[str, Any]:
    """Encode a State with a schema marker and content hash."""

    if state.history is not None:
        raise ValueError("Use the owning durable store to checkpoint disk-backed State")
    body = {
        "task": _content_input_to_record(state.task),
        "data": _json_safe(state.data),
        "events": [_event_to_record(event) for event in state.events],
    }
    digest = _digest(body)
    return {
        "schema": CHECKPOINT_SCHEMA,
        "covered_event_index": state.events[-1].index if state.events else -1,
        "covered_event_uuid": state.events[-1].uuid if state.events else "",
        "content_sha256": digest,
        "body": body,
    }


def state_from_checkpoint(payload: Mapping[str, Any]) -> State:
    """Validate and decode a checkpoint into a fresh State."""

    schema = str(payload.get("schema") or "")
    if schema != CHECKPOINT_SCHEMA:
        raise ValueError(f"Unsupported checkpoint schema: {schema!r}")
    body = payload.get("body")
    if not isinstance(body, Mapping):
        raise ValueError("checkpoint body must be an object")
    expected = str(payload.get("content_sha256") or "")
    if expected != _digest(body):
        raise ValueError("Checkpoint content hash mismatch")

    raw_events = body.get("events", [])
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        raise ValueError("checkpoint body 'events' must be a list")
    events = [_event_from_record(record) for record in raw_events]
    _validate_event_prefix(events, payload)
    data = body.get("data")
    state = State(task=_content_input_from_record(body.get("task", "")), events=events)
    if events:
        # ``State`` uses a monotonic origin for new events. Shift that origin
        # so a resumed run continues the persisted relative timeline instead
        # of starting elapsed time at zero again.
        state._monotonic_origin = time.monotonic() - events[-1].elapsed
    if isinstance(data, Mapping):
        state.data.update(dict(data))
    return state


def _event_to_record(event: Event) -> dict[str, Any]:
    return cast(dict[str, Any], _json_safe(event))


def _event_from_record(record: Any) -> Event:
    if not isinstance(record, Mapping):
        raise ValueError("checkpoint event records must be objects")
    kind = str(record.get("kind") or "")
    base = _base(record)
    if kind == "message":
        return MessageEvent(message=_message_from_record(record.get("message")), **base)
    if kind == "agent_start":
        return AgentStartEvent(
            agent=str(record.get("agent") or ""),
            system_prompt=str(record.get("system_prompt") or ""),
            **base,
        )
    if kind == "agent_end":
        return AgentEndEvent(
            reason=cast(Any, str(record.get("reason") or "max_turns")), **base
        )
    if kind == "turn_start":
        return TurnStartEvent(agent=str(record.get("agent") or ""), **base)
    if kind == "turn_end":
        return TurnEndEvent(
            agent=str(record.get("agent") or ""),
            terminated=bool(record.get("terminated", False)),
            **base,
        )
    if kind == "model_request":
        return ModelRequestEvent(
            agent=str(record.get("agent") or ""),
            visible_count=int(record.get("visible_count", 0) or 0),
            llm_message_count=int(record.get("llm_message_count", 0) or 0),
            context_view=_mapping(record.get("context_view")),
            tools=list(record.get("tools", []))
            if isinstance(record.get("tools"), list)
            else [],
            llm_payload=[
                llm_message(
                    item["role"],
                    _content_input_from_record(item.get("content", "")),
                    extra=item["extra"],
                )
                if isinstance(item, Mapping) and "role" in item and "extra" in item
                else item
                for item in record.get("llm_payload", [])
            ]
            if isinstance(record.get("llm_payload"), list)
            else [],
            system_prompt=str(record.get("system_prompt") or ""),
            api=str(record.get("api") or ""),
            **base,
        )
    if kind == "model_response":
        usage = record.get("usage")
        return ModelResponseEvent(
            agent=str(record.get("agent") or ""),
            output_kind=cast(MessageKind, str(record.get("output_kind") or "message")),
            target=str(record.get("target") or ""),
            tool_call_count=int(record.get("tool_call_count", 0) or 0),
            usage=_usage_from_record(usage) if isinstance(usage, Mapping) else None,
            model=str(record.get("model") or ""),
            api=str(record.get("api") or ""),
            **base,
        )
    if kind == "context_compression":
        return ContextCompressionEvent(
            agent=str(record.get("agent") or ""),
            summary_message_index=int(record.get("summary_message_index", 0) or 0),
            compressed_message_indices=_int_list(
                record.get("compressed_message_indices")
            ),
            active_context_indices=_int_list(record.get("active_context_indices")),
            before_tokens=int(record.get("before_tokens", 0) or 0),
            after_tokens=int(record.get("after_tokens", 0) or 0),
            strategy=str(record.get("strategy") or ""),
            start_elapsed=_optional_float(record.get("start_elapsed")),
            **base,
        )
    if kind == "tool_execution_start":
        return ToolExecutionStartEvent(
            tool_call_id=str(record.get("tool_call_id") or ""),
            tool_name=str(record.get("tool_name") or ""),
            **base,
        )
    if kind == "tool_execution_update":
        return ToolExecutionUpdateEvent(
            tool_call_id=str(record.get("tool_call_id") or ""),
            tool_name=str(record.get("tool_name") or ""),
            partial=_tool_result_from_record(record.get("partial")),
            **base,
        )
    if kind == "tool_execution_end":
        return ToolExecutionEndEvent(
            tool_call_id=str(record.get("tool_call_id") or ""),
            tool_name=str(record.get("tool_name") or ""),
            is_error=bool(record.get("is_error", False)),
            terminate=bool(record.get("terminate", False)),
            **base,
        )
    if kind == "hook_fired":
        return HookFiredEvent(
            point=str(record.get("point") or ""),
            agent=str(record.get("agent") or ""),
            target=str(record.get("target") or ""),
            block_reason=str(record.get("block_reason") or ""),
            emitted=int(record.get("emitted", 0) or 0),
            **base,
        )
    if kind == "goal_status":
        return GoalStatusEvent(
            objective=str(record.get("objective") or ""),
            status=cast(Any, str(record.get("status") or "active")),
            turns_used=int(record.get("turns_used", 0) or 0),
            tokens_used=int(record.get("tokens_used", 0) or 0),
            reason=str(record.get("reason") or ""),
            **base,
        )
    if kind == "skill_invoked":
        return SkillInvokedEvent(
            skill_name=str(record.get("skill_name") or ""),
            version=str(record.get("version") or ""),
            input_sha256=str(record.get("input_sha256") or ""),
            source=str(record.get("source") or ""),
            trigger=str(record.get("trigger") or "mention"),
            **base,
        )
    raise ValueError(f"Unsupported checkpoint event kind: {kind!r}")


def _base(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "index": int(record["index"]) if record.get("index") is not None else -1,
        "elapsed": float(record["elapsed"])
        if record.get("elapsed") is not None
        else 0.0,
        "uuid": str(record.get("uuid") or ""),
    }


def _message_to_record(message: Message) -> dict[str, Any]:
    record = cast(dict[str, Any], _json_safe(message))
    if record.get("usage") is None:
        record.pop("usage", None)
    return record


def _message_from_record(record: Any) -> Message:
    if not isinstance(record, Mapping):
        raise ValueError("checkpoint message records must be objects")
    role = str(record.get("role") or "")
    if role not in {"user", "system", "assistant"}:
        raise ValueError(f"Unsupported checkpoint message role: {role!r}")
    content = tuple(_block_from_record(item) for item in record.get("content", []))
    message = make_message(
        cast(Role, role),
        content,
        sender=str(record.get("sender") or role),
        target=str(record.get("target") or "all"),
        kind=cast(MessageKind, str(record.get("kind") or "message")),
        sidecar=cast(MessageSidecar, _mapping(record.get("sidecar"))),
    )
    if role != "assistant":
        return message
    return cast(
        Message,
        AssistantMessage(
            content=message.content,
            sender=message.sender,
            target=message.target,
            kind=message.kind,
            sidecar=message.sidecar,
            usage=_usage_from_record(record["usage"])
            if isinstance(record.get("usage"), Mapping)
            else None,
            model=str(record.get("model") or ""),
        ),
    )


def _block_from_record(record: Any) -> ContentBlock:
    if not isinstance(record, Mapping):
        raise ValueError("checkpoint content block records must be objects")
    kind = str(record.get("kind") or "")
    if kind == "text":
        return TextBlock(str(record.get("text") or ""))
    if kind == "image":
        return ImageBlock(
            data=str(record.get("data") or ""),
            mime_type=str(record.get("mime_type") or "image/png"),
        )
    if kind == "thinking":
        return ThinkingBlock(
            text=str(record.get("text") or ""),
            signature=_optional_string(record.get("signature")),
            redacted=bool(record.get("redacted", False)),
            source_field=_optional_string(record.get("source_field")),
        )
    if kind == "tool_call":
        return ToolCallBlock(
            id=str(record.get("id") or ""),
            name=str(record.get("name") or ""),
            arguments=_mapping(record.get("arguments")),
        )
    if kind == "tool_result":
        return ToolResultBlock(
            tool_call_id=str(record.get("tool_call_id") or ""),
            tool_name=str(record.get("tool_name") or ""),
            content=tuple(
                cast(TextBlock | ImageBlock, _block_from_record(item))
                for item in record.get("content", [])
            ),
            is_error=bool(record.get("is_error", False)),
        )
    raise ValueError(f"Unsupported checkpoint content block kind: {kind!r}")


def _tool_result_from_record(record: Any) -> ToolResult:
    if not isinstance(record, Mapping):
        raise ValueError("checkpoint tool result must be an object")
    content = tuple(
        cast(TextBlock | ImageBlock, _block_from_record(item))
        for item in record.get("content", [])
    )
    return ToolResult(
        content=content,
        details=record.get("details"),
        is_error=bool(record.get("is_error", False)),
        terminate=bool(record.get("terminate", False)),
        execution_complete=record.get("execution_complete")
        if isinstance(record.get("execution_complete"), bool)
        else None,
    )


def _content_input_to_record(content: ContentInput) -> str | list[dict[str, Any]]:
    return (
        content
        if isinstance(content, str)
        else cast(list[dict[str, Any]], _json_safe(content))
    )


def _content_input_from_record(value: Any) -> ContentInput:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return tuple(_block_from_record(item) for item in value)
    return str(value or "")


def _usage_from_record(record: Mapping[str, Any]) -> TokenUsage:
    return TokenUsage(
        input_tokens=int(record.get("input_tokens", 0) or 0),
        output_tokens=int(record.get("output_tokens", 0) or 0),
        cache_read_tokens=int(record.get("cache_read_tokens", 0) or 0),
        cache_write_tokens=int(record.get("cache_write_tokens", 0) or 0),
    )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_list(value: Any) -> list[int]:
    return [int(item) for item in value] if isinstance(value, list) else []


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _digest(body: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _json_safe(body), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_event_prefix(events: Sequence[Event], payload: Mapping[str, Any]) -> None:
    for expected, event in enumerate(events):
        if event.index != expected:
            raise ValueError(
                f"Checkpoint event index is not contiguous at {expected}: {event.index}"
            )
    raw_covered = payload.get("covered_event_index")
    covered = int(raw_covered) if raw_covered is not None else -1
    if covered != (events[-1].index if events else -1):
        raise ValueError("Checkpoint covered_event_index does not match events")
    covered_uuid = str(payload.get("covered_event_uuid") or "")
    if covered_uuid != (events[-1].uuid if events else ""):
        raise ValueError("Checkpoint covered_event_uuid does not match events")

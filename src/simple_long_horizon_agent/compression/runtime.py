"""Compression runtime — the framework that applies a strategy's decision.

Strategy authors do not need to read this module. It takes a
`CompressionDecision` and does everything else: filtering, validating tool
pairs, sizing the active context, recording the replacement, and emitting the
`ContextCompressionEvent` that re-points the active view.

A decision has two shapes, resolved by `_resolve_targets`:

- the default **N->1 fold** — drop `compress_indices` (tool pairs aligned so a
  call/result is never split) and splice `replacement` where the first dropped
  item was;
- a **1->1 rewrite** (`rewrite=True`) — swap a single target in place,
  structure preserved, no alignment.

Both reduce to the same move and record the same event.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Iterator

from ..context_view import (
    CompressionDecision,
    ContextPolicy,
    estimate_context_tokens,
)
from ..messages import (
    AssistantMessage,
    Message,
    TextBlock,
    message_tool_calls,
    tool_results_of,
)
from ..protocols import ContextCompressionEvent, Event, MessageEvent

if TYPE_CHECKING:
    from ..core import Agent
    from ..state import State


def format_index_ranges(indices: Sequence[int]) -> str:
    """Render indices as compact sorted ranges: (2, 3, 4, 7) -> '2-4, 7'."""
    ordered = sorted(set(indices))
    if not ordered:
        return ""
    ranges: list[str] = []
    start = prev = ordered[0]
    for index in ordered[1:]:
        if index == prev + 1:
            prev = index
            continue
        ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
        start = prev = index
    ranges.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(ranges)


def _active_context_tokens(active: list[tuple[int, Message]]) -> int:
    """Size the active context, distrusting baselines older than the last compression.

    A usage baseline (`AssistantMessage.usage.context_tokens`) is the full
    window size *at the moment that assistant was generated*. Once a compression
    folds older messages away, any assistant recorded before it carries a
    baseline that still counts content no longer present — trusting it reports
    a freshly compressed context as barely smaller than before.

    We read the answer off the append-only index order itself, with no marker
    on the messages. Because the transcript is append-only, a message's index
    rises with its position; the *only* thing that places a higher index before
    a lower one in the active view is a compaction splicing its replacement back
    at an earlier slot — true for both an N->1 fold (the summary) and a 1->1
    rewrite (the in-place replacement). So an index that has any smaller index
    after it is a compaction output, and a usage baseline is trustworthy only if
    it outranks every such boundary. Until the next model turn appends a fresh
    assistant (a new highest index, in order), fall back to the per-message sum
    (which still uses each assistant's exact `output_tokens`).
    """
    indices = [index for index, _ in active]
    # Newest compaction boundary == the largest index that sits before a
    # smaller one. Walk right-to-left tracking the min index seen *after* the
    # current position; an index above that min was spliced in (out of order).
    last_compaction = -1
    suffix_min: int | None = None
    for index in reversed(indices):
        if suffix_min is not None and index > suffix_min:
            last_compaction = max(last_compaction, index)
        suffix_min = index if suffix_min is None else min(suffix_min, index)
    last_usage_assistant = max(
        (
            index
            for index, message in active
            if isinstance(message, AssistantMessage)
            and message.usage is not None
            and message.usage.context_tokens > 0
        ),
        default=-1,
    )
    fresh_baseline = last_usage_assistant > last_compaction
    return estimate_context_tokens(
        [message for _, message in active],
        allow_usage_baseline=fresh_baseline,
    )


def maybe_compress_context(
    agent: "Agent",
    state: "State",
    policy: ContextPolicy,
) -> list[Event]:
    """Run `policy.strategy` (if any) and apply its decision."""
    if policy.strategy is None:
        return []
    active = [
        item for item in state.active_context_items() if policy.is_visible(item[1])
    ]
    trace_base_elapsed = state.elapsed_seconds()
    multi_decision = getattr(policy.strategy, "decisions", None)
    if callable(multi_decision):
        decisions = list(multi_decision(active, agent.name) or [])
    else:
        decision = policy.strategy(active, agent.name)
        decisions = [] if decision is None else [decision]
    if not decisions:
        return []

    events: list[Event] = []
    trace_offset = 0.0
    for decision in decisions:
        events.extend(
            _apply_decision(
                agent,
                state,
                active,
                decision,
                trace_base_elapsed + trace_offset,
            )
        )
        trace_offset += max(
            (event.elapsed for event in decision.trace_events), default=0.0
        )
        active = [
            item for item in state.active_context_items() if policy.is_visible(item[1])
        ]
    return events


def _apply_decision(
    agent: "Agent",
    state: "State",
    active: list[tuple[int, Message]],
    decision: CompressionDecision,
    trace_base_elapsed: float = 0.0,
) -> Iterator[Event]:
    """Apply one decision and record the resulting `ContextCompressionEvent`.

    Both decision shapes reduce to the same move — drop a set of indices and
    splice one replacement where the first dropped item was — so they share
    this body and the same event. They differ only in how the target set and
    the replacement are resolved; see `_resolve_targets`.
    """
    compress_set, replacement = _resolve_targets(active, decision)
    if not compress_set:
        return

    if not decision.rewrite:
        # Cite the final aligned set, not the strategy's proposed indices.
        citation = (
            f"[Compressed from transcript messages {format_index_ranges(sorted(compress_set))}; "
            "use recall with these 0-based indices to read originals.]\n"
        )
        replacement = replace(
            replacement, content=(TextBlock(citation), *replacement.content)
        )

    for event in decision.trace_events:
        yield state.record_event_at(
            event,
            elapsed=trace_base_elapsed + event.elapsed,
        )
    decision_end_elapsed = trace_base_elapsed + max(
        (event.elapsed for event in decision.trace_events), default=0.0
    )

    # New active view: every uncompressed item stays in its original order; the
    # replacement is spliced where the first compressed item was. For a 1->1
    # rewrite (a single index) that slot is the target's own position.
    first = next(
        (pos for pos, (index, _) in enumerate(active) if index in compress_set),
        len(active),
    )
    kept_before = [index for index, _ in active[:first]]
    kept_after = [
        index for index, _ in active[first + 1 :] if index not in compress_set
    ]
    kept_messages = [message for index, message in active if index not in compress_set]

    # `before` uses the append-only-aware sizing so a prior compaction's stale
    # usage baseline doesn't inflate the reported size.
    before_tokens = _active_context_tokens(active)
    # `after` is post-compaction: the replacement is the newest content and no
    # usage-bearing assistant outranks it, so the baseline is never valid here —
    # sum per-message (still exact per-assistant `output_tokens`).
    after_tokens = estimate_context_tokens(
        kept_messages + [replacement], allow_usage_baseline=False
    )

    summary_index = len(state.messages)
    yield state.record_event_at(
        MessageEvent(message=replacement), elapsed=decision_end_elapsed
    )
    yield state.record_event_at(
        ContextCompressionEvent(
            agent=agent.name,
            summary_message_index=summary_index,
            compressed_message_indices=sorted(compress_set),
            active_context_indices=kept_before + [summary_index] + kept_after,
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            strategy=decision.label,
            start_elapsed=trace_base_elapsed,
        ),
        elapsed=decision_end_elapsed,
    )


def _resolve_targets(
    active: list[tuple[int, Message]],
    decision: CompressionDecision,
) -> tuple[set[int], Message]:
    """Resolve a decision to the `(indices_to_drop, replacement)` to splice.

    Fold (the default): align tool pairs so a half-compressed call/result is
    never split apart.

    Rewrite (`rewrite=True`): a 1->1 in-place swap. Exactly one target, and
    NO alignment — the replacement keeps the target's tool linkage itself, so
    the call/result graph stays intact by construction rather than by
    un-compressing a split pair. The structure is validated; nothing is stamped
    on the replacement, because sizing infers the compaction boundary from the
    spliced message's out-of-order index (see `_active_context_tokens`).
    Returns an empty set (a no-op) when the target is not in the active view.
    """
    if not decision.rewrite:
        return (
            _align_tool_pairs(active, set(decision.compress_indices)),
            decision.replacement,
        )
    if len(decision.compress_indices) != 1:
        raise ValueError(
            "a rewrite decision must target exactly one message index, "
            f"got {decision.compress_indices!r}"
        )
    (target_index,) = decision.compress_indices
    target = next((m for index, m in active if index == target_index), None)
    if target is None:
        return set(), decision.replacement
    return {target_index}, _validated_rewrite(target, decision.replacement)


def _validated_rewrite(target: Message, replacement: Message) -> Message:
    """Reject a rewrite replacement that would change the message's structure.

    A rewrite may shrink or reword content, but it must not alter the shape the
    wire and the runtime depend on: the role must match, and the set of
    `tool_call_id`s the message exposes (as `ToolCallBlock`s on an assistant or
    `ToolResultBlock`s on a user message) must be identical — otherwise the
    swap would orphan a tool_call or its result.
    """
    if type(target) is not type(replacement):
        raise ValueError(
            f"a rewrite must preserve the message role: target is "
            f"{type(target).__name__}, replacement is {type(replacement).__name__}"
        )
    if _tool_call_ids(target) != _tool_call_ids(replacement):
        raise ValueError(
            "a rewrite must preserve the message's tool_call_id linkage; "
            f"target exposes {sorted(_tool_call_ids(target))}, replacement "
            f"exposes {sorted(_tool_call_ids(replacement))}"
        )
    return replacement


def _tool_call_ids(message: Message) -> set[str]:
    """Every tool_call_id this message links to, from either side of a pair."""
    ids = {call.id for call in message_tool_calls(message)}
    ids.update(block.tool_call_id for block in tool_results_of(message.content))
    return ids


def _align_tool_pairs(
    active: list[tuple[int, Message]],
    compress_set: set[int],
) -> set[int]:
    """Drop entries from `compress_set` whose tool partner is not compressed.

    A `tool_call` and its matching `tool_result` must travel together — many
    providers reject an orphan tool_result, and a tool_call with no result
    has no answer to reference. If a strategy compresses one side without
    the other, the framework un-compresses that side (conservative: keep
    more context rather than break the call/result graph).
    """
    by_index = {index: message for index, message in active}
    while True:
        orphans = {
            index
            for index in compress_set
            if any(
                partner not in compress_set
                for partner in _tool_partners(index, by_index)
            )
        }
        if not orphans:
            return compress_set
        compress_set = compress_set - orphans


def _tool_partners(index: int, by_index: dict[int, Message]) -> list[int]:
    """Indices of messages that pair with `index` via tool_call_id.

    Two cases, joined:
    - If `index` is an assistant with `tool_calls`, return every message
      that carries a matching `ToolResultBlock`.
    - If `index` carries `ToolResultBlock`s, return every assistant
      whose `tool_calls` they answer.
    """
    message = by_index[index]
    partners: list[int] = []
    if isinstance(message, AssistantMessage) and message.tool_calls:
        call_ids = {tool_call.id for tool_call in message.tool_calls}
        partners.extend(
            other_index
            for other_index, other_message in by_index.items()
            if other_index != index
            and any(
                block.tool_call_id in call_ids
                for block in tool_results_of(other_message.content)
            )
        )
    wanted = {block.tool_call_id for block in tool_results_of(message.content)}
    if wanted:
        partners.extend(
            other_index
            for other_index, other_message in by_index.items()
            if other_index != index
            and isinstance(other_message, AssistantMessage)
            and any(tool_call.id in wanted for tool_call in other_message.tool_calls)
        )
    return partners


def enforce_input_budget(
    agent: "Agent", state: "State", policy: ContextPolicy, *, force: bool = False
) -> list[Event]:
    """Bound estimated wire input, including system and tool schemas, before dispatch.

    A conservative UTF-8 byte estimate avoids assuming English token density.
    Originals remain in the transcript and can be paged with recall.
    """
    import json
    from dataclasses import asdict
    from ..llm.bridge import messages_to_llm_messages
    from ..messages import make_message
    from ..state import StateResourceLimitError

    limit = (
        policy.max_input_bytes
        if policy.max_input_bytes is not None
        else policy.max_input_tokens
    )
    if limit is None:
        return []

    def size() -> int:
        messages = [m for _, m in state.active_context_items() if policy.is_visible(m)]
        payload = [
            asdict(m) for m in messages_to_llm_messages(messages, with_header=False)
        ]
        tools = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in agent.tools
        ]
        return len(
            json.dumps(
                [agent.system_prompt, tools, payload], ensure_ascii=False, default=str
            ).encode("utf-8")
        )

    if size() <= limit and not force:
        return []
    events = []
    # Shrink the largest message first, preserving its role and tool linkage.
    # Keep both ends: failures and conclusions commonly occur at the end.
    from ..messages import text_of, ToolResultBlock, TextBlock

    while size() > limit or force:
        active = [
            item for item in state.active_context_items() if policy.is_visible(item[1])
        ]
        candidates = [
            (i, m)
            for i, m in active
            if m.kind not in {"task", "system", "context"}
            and not (m.sidecar or {}).get("budget_externalized")
        ]
        if not candidates:
            break
        index, message = max(candidates, key=lambda item: len(str(item[1].content)))

        def excerpt(text: str) -> str:
            return text if len(text) <= 512 else text[:256] + "\n…\n" + text[-256:]

        marker = f"[Full original: recall indices=[{index}], offset=0; follow next_offset.]\n"
        content = []
        for block in message.content:
            if isinstance(block, TextBlock):
                content.append(TextBlock(marker + excerpt(block.text)))
            elif isinstance(block, ToolResultBlock):
                content.append(
                    replace(
                        block,
                        content=(TextBlock(marker + excerpt(text_of(block.content))),),
                    )
                )
            else:
                content.append(block)
        replacement = replace(
            message,
            content=tuple(content),
            sidecar={**(message.sidecar or {}), "budget_externalized": True},
        )
        decision = CompressionDecision(
            (index,), replacement, rewrite=True, label="input-budget"
        )
        events.extend(_apply_decision(agent, state, active, decision))
        force = False
    # If many small messages exceed the allowance, fold only the older half.
    # Keep the latest exchanges and the caller-owned task progress available.
    while size() > limit:
        active = [
            item for item in state.active_context_items() if policy.is_visible(item[1])
        ]
        candidates = [
            (i, m) for i, m in active if m.kind not in {"task", "system", "context"}
        ]
        if len(candidates) <= 4:
            break
        indices = tuple(i for i, _ in candidates[: max(1, len(candidates) // 2)])
        progress = json.dumps(state.data.get("task_progress", {}), ensure_ascii=False)
        decision = CompressionDecision(
            indices,
            make_message(
                "user",
                "Older history externalized. Saved progress: "
                + progress
                + "\nUse paged recall for evidence.",
                kind="summary",
                sender="runtime",
                target=agent.name,
            ),
            label="input-budget",
        )
        before = size()
        events.extend(_apply_decision(agent, state, active, decision))
        if size() >= before:
            break
    if size() > limit:
        raise StateResourceLimitError(
            "Pinned instructions, tool schemas or recall citations exceed the input byte allowance; reduce fixed input or increase the configured budget"
        )
    return events

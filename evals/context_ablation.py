"""Paired context-compression experiment with fixed transcripts and exact scoring."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import statistics
import time

from evals.evidence import capture_source

from simple_long_horizon_agent import (
    Agent,
    ContextPolicy,
    State,
    ToolCompactStrategy,
    run,
)
from simple_long_horizon_agent.compression import summarize_compression
from simple_long_horizon_agent.llm.env import load_dotenv, provider_from_env
from simple_long_horizon_agent.llm_agent import make_llm_agent
from simple_long_horizon_agent.messages import (
    Message,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    assistant_message,
    text_of,
    tool_results_message,
)
from simple_long_horizon_agent.tools import make_recall_tool
from simple_long_horizon_agent.trace import run_trace_from_state, write_event_stream


PROMPT = "Return only the exact ACCESS-CODE from the archived reports. Use recall if the reports were compressed."
SYSTEM = "You are answering from a saved research transcript. Recover evidence if needed; never guess an access code."


def fixture(case: int) -> tuple[State, str]:
    expected = hashlib.sha256(f"context-ablation-v1:{case}".encode()).hexdigest()[:16]
    state = State(PROMPT)
    state.send("task", "user", "reader", PROMPT)
    for page in range(6):
        call_id = f"archive-{page}"
        state.record(
            assistant_message(
                [ToolCallBlock(call_id, "read", {"path": f"report-{page}.txt"})],
                sender="reader",
                kind="step",
            )
        )
        body = f"Report {page}\n" + "irrelevant historical measurement. " * 120
        if page == case % 5:
            body += f"\nACCESS-CODE: {expected}\n"
        state.record(
            tool_results_message(
                [
                    ToolResultBlock(
                        tool_call_id=call_id,
                        tool_name="read",
                        content=(TextBlock(body),),
                    )
                ],
                target="reader",
            )
        )
    return state, expected


def scripted(visible: list[Message]) -> Message:
    text = "\n".join(text_of(message.content) for message in visible)
    # Read nested tool-result content, as a real adapter would expose it.
    from simple_long_horizon_agent.messages import tool_results_of

    text += "\n".join(
        text_of(result.content)
        for message in visible
        for result in tool_results_of(message.content)
    )
    found = re.search(r"ACCESS-CODE: ([a-f0-9]{16})", text)
    if found:
        return assistant_message(found.group(1), sender="reader", kind="final")
    return assistant_message(
        [
            ToolCallBlock(
                "recall-archives",
                "recall",
                {
                    "indices": [2, 4, 6, 8, 10],
                },
            )
        ],
        sender="reader",
        kind="step",
    )


def experiment(output: Path, *, cases: int, mode: str) -> dict[str, object]:
    if cases < 1:
        raise ValueError("cases must be positive")
    if output.exists():
        raise FileExistsError(f"Use a new output directory: {output}")
    provider = None
    if mode == "openai":
        provider = replace(
            provider_from_env(read_reasoning=True, default_temperature=None),
            default_max_tokens=1024,
        )
    output.mkdir(parents=True)
    manifest = {
        "experiment": "context-ablation-v1",
        "mode": mode,
        "cases": list(range(cases)),
        "model": provider.model if provider else "scripted",
        "api": provider.api if provider else None,
        "reasoning": provider.default_reasoning if provider else None,
        **capture_source(output),
        "prompt": PROMPT,
        "system_prompt": SYSTEM,
        "max_turns": 4,
        "max_output_tokens": 1024,
        "scorer": "exact stripped final answer equals fixture code",
        "variants": {
            "baseline": None,
            "compact": {"threshold_tokens": 1000, "preview_chars": 80},
        },
        "order": "alternate baseline-first and compact-first by case",
        "limitation": "synthetic retrieval tasks; no claim about SWE-bench or statistical significance",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    for case in range(cases):
        variants = ("baseline", "compact") if case % 2 == 0 else ("compact", "baseline")
        for variant in variants:
            state, expected = fixture(case)
            # Both arms have identical recall capabilities and output bounds.
            recall = make_recall_tool(
                state, max_chars_per_message=6000, max_total_chars=30000
            )
            policy = (
                ContextPolicy(strategy=ToolCompactStrategy(1000, preview_chars=80))
                if variant == "compact"
                else ContextPolicy()
            )
            agent = (
                make_llm_agent(
                    name="reader",
                    provider=provider,
                    tools=(recall,),
                    system_prompt=SYSTEM,
                    context_policy=policy,
                )
                if provider
                else Agent(
                    "reader",
                    scripted,
                    tools=(recall,),
                    system_prompt=SYSTEM,
                    context_policy=policy,
                )
            )
            start = time.monotonic()
            error = None
            try:
                list(run(agent, state, max_turns=4))
            except Exception as exc:
                error = type(exc).__name__
            final = next(
                (
                    text_of(m.content).strip()
                    for m in reversed(state.messages)
                    if m.kind == "final"
                ),
                "",
            )
            trace = run_trace_from_state(
                state=state, trace_id=f"{case}-{variant}", producer="context-ablation"
            )
            write_event_stream(output / f"{case}-{variant}.jsonl", trace)
            cost = trace.run_cost()
            row = {
                "case": case,
                "variant": variant,
                "passed": error is None and final == expected,
                "failure": error
                or (None if final == expected else "wrong_or_missing_answer"),
                "seconds": time.monotonic() - start,
                "usage": cost.as_dict() if mode == "openai" else None,
                "estimated_cost_usd": cost.total_usd
                if mode == "openai" and cost.calls and not cost.unpriced_models
                else None,
                **summarize_compression(state.events).as_dict(),
            }
            rows.append(row)
            with (output / "results.jsonl").open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            print(f"case={case} variant={variant} passed={row['passed']}", flush=True)
    summary = {
        "mode": mode,
        "scope": "model-quality experiment"
        if mode == "openai"
        else "scripted wiring check only",
        "variants": {
            variant: {
                "tasks": cases,
                "passed": sum(
                    row["passed"] for row in rows if row["variant"] == variant
                ),
                "mean_peak_estimated_tokens": statistics.mean(
                    row["peak_active_tokens"]
                    for row in rows
                    if row["variant"] == variant
                ),
                "mean_seconds": statistics.mean(
                    row["seconds"] for row in rows if row["variant"] == variant
                ),
            }
            for variant in ("baseline", "compact")
        },
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=int, default=12)
    parser.add_argument(
        "--provider", choices=("scripted", "openai"), default="scripted"
    )
    parser.add_argument("--dotenv", type=Path, default=Path(".env"))
    args = parser.parse_args()
    load_dotenv(args.dotenv)
    print(
        json.dumps(
            experiment(args.output, cases=args.cases, mode=args.provider), indent=2
        )
    )


if __name__ == "__main__":
    main()

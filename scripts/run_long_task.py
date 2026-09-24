"""Run or resume a verified code task with durable state and bounded attempts."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from evals.evidence import capture_source
from simple_long_horizon_agent import (
    Agent,
    ContextPolicy,
    FileRunStore,
    RecoverableRun,
    RecoverableRunExecutor,
    SqliteCheckpointStore,
    State,
    ToolCompactStrategy,
)
from simple_long_horizon_agent.completion import CompletionResult
from simple_long_horizon_agent.llm.env import provider_from_env
from simple_long_horizon_agent.llm_agent import make_llm_agent
from simple_long_horizon_agent.reconciliation import (
    EditOperationReconciler,
    BashOperationReconciler,
)
from simple_long_horizon_agent.run_control import FileOperationLedger, LeaseConflict
from simple_long_horizon_agent.tools.bash import make_bash_tool, run_bash
from simple_long_horizon_agent.agents.code_task import (
    repository_guidance,
    make_task_status_tool,
    delivery_review,
)
from simple_long_horizon_agent.tools.edit import make_edit_tool
from simple_long_horizon_agent.tools.read import make_read_tool
from simple_long_horizon_agent.tools.recall import make_recall_tool
from simple_long_horizon_agent.trace import run_trace_from_state, write_event_stream


SYSTEM = """Complete the original task in this workspace. Read files, make edits,
and check the result. Use edit for file changes so interrupted writes can be
reconciled. Old tool results may be compacted: use recall with the message IDs
in the summary when you need the originals. A caller-owned verifier decides
completion; use its feedback to correct remaining failures. Do not change tests
to make a failing implementation appear correct. Start with repository guidance
and a requirement checklist using task_status. Record evidence and the next
action when progress changes; read task_status after compaction. If the same
failure repeats without new evidence, change the approach or explain the blocker.
Before finishing, inspect the diff and verify every requirement."""


def snapshot(workspace: Path, destination: Path) -> None:
    """Copy tracked and non-ignored task files, excluding Git internals."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    destination.mkdir(parents=True, exist_ok=True)
    for name in set(result.stdout.decode().split("\0")) - {""}:
        source = workspace / name
        if source.is_file() or source.is_symlink():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(source.readlink())
            else:
                shutil.copy2(source, target)


def initialize(
    root: Path,
    workspace: Path,
    task: str,
    verify_command: str,
    *,
    max_attempts: int = 10,
    max_model_calls: int = 100,
    max_tokens: int = 500000,
    wall_clock_seconds: float = 3600,
    compress_tokens: int = 24000,
    model: str = "configured",
    api: str = "",
    reasoning: str = "",
    allow_test_changes: bool = False,
    max_input_tokens: int = 64000,
    max_input_bytes: int | None = None,
    recoverable_commands: tuple[str, ...] = (),
) -> None:
    if root.exists():
        raise FileExistsError(f"Use --resume for existing run directory: {root}")
    if root == workspace or root.is_relative_to(workspace):
        raise ValueError("Run directory must be outside the task workspace")
    if not task.strip() or not verify_command.strip():
        raise ValueError("task and verify-command must be nonempty")
    if compress_tokens < 1:
        raise ValueError("compress-tokens must be positive")
    guidance = repository_guidance(workspace)
    root.mkdir(parents=True)
    (root / "repository-guidance.txt").write_text(guidance)
    (root / "baseline-status.txt").write_text(
        subprocess.check_output(["git", "status", "--short"], cwd=workspace, text=True)
    )
    snapshot(workspace, root / "before")
    config = dict(
        workspace=str(workspace),
        task=task,
        verify_command=verify_command,
        max_attempts=max_attempts,
        max_model_calls=max_model_calls,
        max_tokens=max_tokens,
        wall_clock_seconds=wall_clock_seconds,
        compress_tokens=compress_tokens,
        model=model,
        api=api,
        reasoning=reasoning,
        allow_test_changes=allow_test_changes,
        max_input_bytes=max_input_bytes
        if max_input_bytes is not None
        else max_input_tokens,
        recoverable_commands=list(recoverable_commands),
    )
    (root / "manifest.json").write_text(
        json.dumps({**capture_source(root), **config}, indent=2) + "\n"
    )


def verifier(root: Path, config: dict, state: State) -> CompletionResult:
    deadline = state.data.get("run_budget", {}).get("deadline")
    timeout = (
        min(120.0, max(0.01, deadline - time.time())) if deadline is not None else 120.0
    )
    review = delivery_review(Path(config["workspace"]), root / "before")
    (root / "delivery.json").write_text(json.dumps(review, indent=2) + "\n")
    result = run_bash(
        config["verify_command"], cwd=config["workspace"], timeout_seconds=timeout
    )
    record = dict(
        command=config["verify_command"],
        returncode=result.exit_code,
        stdout=result.raw_stdout,
        stderr=result.raw_stderr,
    )
    with (root / "verification.jsonl").open("a") as handle:
        handle.write(json.dumps(record) + "\n")
    feedback = (record["stdout"] + record["stderr"])[-12000:]
    protected = (
        review["protected_changes"] if not config.get("allow_test_changes") else []
    )
    if protected:
        feedback += "\nRestore modified caller-owned tests/instructions: " + ", ".join(
            protected
        )
    if not review["diff_check_passed"]:
        feedback += "\nFix diff whitespace errors: " + review["diff_check_output"]
    done = record["returncode"] == 0 and not protected and review["diff_check_passed"]
    signature = json.dumps(
        {
            "changes": review["changes"],
            "feedback": feedback,
            "returncode": record["returncode"],
        },
        sort_keys=True,
    )
    import hashlib

    signature = hashlib.sha256(signature.encode()).hexdigest()
    previous = state.data.get("verification_progress", {})
    streak = (
        previous.get("streak", 0) + 1 if previous.get("signature") == signature else 1
    )
    state.data["verification_progress"] = {
        "signature": signature,
        "streak": 0 if done else streak,
    }
    blocked = not done and streak >= 3
    if blocked:
        feedback += "\nNo progress: identical file changes and verification failure across three attempts."
    return CompletionResult(done, blocked=blocked, reason=feedback)


def drive(
    root: Path,
    *,
    agent_for_state: Callable[[State], Agent],
    once: bool = False,
    lease_seconds: float = 30.0,
) -> dict:
    """Rebuild the same executor on every invocation; persisted limits win."""
    config = json.loads((root / "manifest.json").read_text())
    workspace = Path(config["workspace"])
    executor = RecoverableRunExecutor(
        lease_seconds=lease_seconds,
        run_store=FileRunStore(root / "runs"),
        checkpoint_store=SqliteCheckpointStore(root / "state"),
        operation_ledger=FileOperationLedger(root / "operations"),
        reconciler_for=lambda operation: (
            EditOperationReconciler(workspace=workspace)
            if operation.tool_name == "edit"
            else BashOperationReconciler(
                workspace=workspace,
                commands=tuple(config.get("recoverable_commands", ())),
            )
            if operation.tool_name == "bash"
            else None
        ),
        completion_check=lambda state: verifier(root, config, state),
        max_attempts=config["max_attempts"],
        max_model_calls=config["max_model_calls"],
        max_tokens=config["max_tokens"],
        wall_clock_seconds=config["wall_clock_seconds"],
    )
    handle = RecoverableRun("task", "task", "long-task")
    if not (root / "runs" / "task.json").exists():
        executor.create("task", State(config["task"]), worker_id=handle.worker_id)
    while executor.run_store.get("task").status in {
        "runnable",
        "running",
        "leased",
        "reconciling",
    }:
        try:
            _, events = executor.execute(
                handle,
                Agent(
                    "worker",
                    lambda visible: (_ for _ in ()).throw(
                        AssertionError("unbound worker")
                    ),
                ),
                agent_for_state=agent_for_state,
                max_turns=50,
            )
            for _ in events:
                pass
        except LeaseConflict:
            # A killed process's lease must expire; never steal a live lease.
            raise
        except Exception as exc:
            with (root / "errors.jsonl").open("a") as output:
                output.write(
                    json.dumps({"error": f"{type(exc).__name__}: {exc}"}) + "\n"
                )
            if executor.run_store.get("task").status not in {
                "runnable",
                "budget_exhausted",
                "blocked",
            }:
                raise
        if once:
            break
    state = executor.checkpoint_store.load("task")
    trace = run_trace_from_state(state=state, trace_id="task", producer="long-task")
    write_event_stream(root / "trajectory.jsonl", trace)
    (root / "cost.json").write_text(
        json.dumps(asdict(trace.run_cost()), indent=2) + "\n"
    )
    after = root / "after"
    if after.exists():
        shutil.rmtree(after)
    snapshot(workspace, after)
    diff = subprocess.run(
        ["git", "diff", "--no-index", "--binary", "--", "before", "after"],
        cwd=root,
        capture_output=True,
    )
    if diff.returncode not in (0, 1):
        raise RuntimeError(diff.stderr.decode())
    (root / "repair.patch").write_bytes(
        diff.stdout.replace(b"a/before/", b"a/").replace(b"b/after/", b"b/")
    )
    (root / "task-progress.json").write_text(
        json.dumps(state.data.get("task_progress", {}), indent=2) + "\n"
    )
    result = {
        "status": executor.run_store.get("task").status,
        "attempts_started": state.data.get("attempts_started", 0),
        "budget_reason": state.data.get("budget_reason", ""),
        "events": len(state.events),
        "model": config["model"],
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--verify-command")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--allow-test-changes",
        action="store_true",
        help="Allow modifying existing tests/instructions; independent verification still applies",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one durable attempt, then export artifacts",
    )
    parser.add_argument("--max-attempts", type=int, default=10)
    parser.add_argument("--max-model-calls", type=int, default=100)
    parser.add_argument("--max-tokens", type=int, default=500000)
    parser.add_argument("--wall-clock-seconds", type=float, default=3600)
    parser.add_argument("--compress-tokens", type=int, default=24000)
    parser.add_argument(
        "--max-input-bytes",
        "--max-input-tokens",
        dest="max_input_bytes",
        type=int,
        default=64000,
        help="Conservative serialized input byte allowance; legacy --max-input-tokens is an alias",
    )
    parser.add_argument(
        "--recoverable-command",
        action="append",
        default=[],
        help="Exact shell command the caller authorizes to safely repeat after interruption, including if a previous child survives",
    )
    args = parser.parse_args()
    root = args.run_dir.resolve()
    provider = provider_from_env(
        reexport_auth=True, read_reasoning=True, default_temperature=None
    )
    if not args.resume:
        if args.workspace is None or not args.task or not args.verify_command:
            parser.error("new runs require --workspace, --task and --verify-command")
        initialize(
            root,
            args.workspace.resolve(),
            args.task,
            args.verify_command,
            max_attempts=args.max_attempts,
            max_model_calls=args.max_model_calls,
            max_tokens=args.max_tokens,
            wall_clock_seconds=args.wall_clock_seconds,
            compress_tokens=args.compress_tokens,
            model=provider.model,
            api=provider.api,
            reasoning=str(provider.default_reasoning),
            allow_test_changes=args.allow_test_changes,
            max_input_bytes=args.max_input_bytes,
            recoverable_commands=tuple(args.recoverable_command),
        )
    config = json.loads((root / "manifest.json").read_text())
    if (provider.model, provider.api, str(provider.default_reasoning)) != (
        config["model"],
        config["api"],
        config["reasoning"],
    ):
        parser.error(
            "resume requires the original model, API and reasoning configuration"
        )

    def build(state: State) -> Agent:
        workspace = Path(config["workspace"])
        tools = (
            make_read_tool(cwd=workspace),
            make_edit_tool(cwd=workspace),
            make_bash_tool(
                cwd=workspace,
                recoverable_commands=tuple(config.get("recoverable_commands", ())),
            ),
            make_recall_tool(state),
            make_task_status_tool(
                state,
                save=lambda: SqliteCheckpointStore(root / "state").save("task", state),
            ),
        )
        return make_llm_agent(
            name="worker",
            provider=provider,
            tools=tools,
            system_prompt=SYSTEM
            + "\n\n"
            + repository_guidance(workspace)
            + "\nSaved task progress: "
            + json.dumps(state.data.get("task_progress", {})),
            context_policy=ContextPolicy(
                strategy=ToolCompactStrategy(config["compress_tokens"]),
                max_input_bytes=config.get(
                    "max_input_bytes", config.get("max_input_tokens", 64000)
                ),
            ),
            timeout_seconds=120,
            retry_model_calls=False,
        )

    result = drive(root, agent_for_state=build, once=args.once)
    print(json.dumps(result, indent=2))
    if result["status"] != "complete":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

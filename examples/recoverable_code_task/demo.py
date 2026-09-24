"""Run a real file repair with an independent test and optional SIGKILL."""

from __future__ import annotations

import argparse
from dataclasses import replace
import difflib
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from evals.evidence import capture_source

from simple_long_horizon_agent import (
    Agent,
    FileCheckpointStore,
    FileEventJournal,
    FileEvidenceStore,
    FileRunStore,
    RecoverableRunExecutor,
    RecoverableRuntimeService,
    ToolCallBlock,
    assistant_message,
)
from simple_long_horizon_agent.completion import CompletionResult
from simple_long_horizon_agent.messages import Message, text_of, tool_results_of
from simple_long_horizon_agent.reconciliation import EditOperationReconciler
from simple_long_horizon_agent.run_control import FileOperationLedger
from simple_long_horizon_agent.state import State
from simple_long_horizon_agent.tools.edit import make_edit_tool
from simple_long_horizon_agent.tools.read import make_read_tool


TASK = "Fix triangle(n) to sum all integers from 0 through n, including n. Keep tests unchanged."
SOURCE = "def triangle(n):\n    return sum(range(n))\n"
TEST = """import unittest
from triangle import triangle

class TriangleTest(unittest.TestCase):
    def test_inclusive_sum(self):
        for n, expected in [(0, 0), (1, 1), (3, 6), (10, 55)]:
            with self.subTest(n=n):
                self.assertEqual(triangle(n), expected)
"""


def verify(root: Path, state: State) -> CompletionResult:
    """Reinstall the trusted test outside the model's completion authority."""
    workspace = root / "workspace"
    (workspace / "test_triangle.py").write_text(TEST)
    command = [sys.executable, "-B", "-m", "unittest", "test_triangle", "-v"]
    result = subprocess.run(
        command, cwd=workspace, capture_output=True, text=True, timeout=20
    )
    # Keep exact verifier output independently of the model transcript.
    with (root / "verification.jsonl").open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "command": command,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                }
            )
            + "\n"
        )
    state.data["verification"] = {"command": command, "returncode": result.returncode}
    return CompletionResult(
        result.returncode == 0, reason=result.stdout + result.stderr
    )


def make_worker(
    root: Path, *, crash: bool = False, provider: str = "scripted"
) -> Agent:
    workspace = root / "workspace"
    edit = make_edit_tool(cwd=workspace)
    if crash:
        execute = edit.execute

        def pause_after_write(call_id, arguments, abort, on_update):
            result = execute(call_id, arguments, abort, on_update)
            if result.is_error:
                return result
            (root / "ready-to-kill").write_text("edit applied; result not confirmed")
            threading.Event().wait(30)
            raise RuntimeError("parent did not kill the worker within 30 seconds")

        edit = replace(edit, execute=pause_after_write)
    tools = (make_read_tool(cwd=workspace), edit)
    if provider == "openai":
        from simple_long_horizon_agent.llm.env import provider_from_env
        from simple_long_horizon_agent.llm_agent import make_llm_agent

        return make_llm_agent(
            name="repair",
            provider=provider_from_env(
                reexport_auth=True, read_reasoning=True, default_temperature=None
            ),
            tools=tools,
            system_prompt="Repair the given Python task using read and edit. Do not modify tests.",
        )

    def generate(visible: list[Message]) -> Message:
        if not any(TASK in text_of(message.content) for message in visible):
            raise AssertionError("original task is missing from model context")
        feedback = any(
            "External verification" in text_of(message.content) for message in visible
        )
        results = [
            result for message in visible for result in tool_results_of(message.content)
        ]
        if not feedback:
            return assistant_message(
                "I think it is done.", sender="repair", kind="final"
            )
        if not any(result.tool_name == "read" for result in results):
            call = ToolCallBlock("inspect", "read", {"path": "triangle.py"})
        elif not any(result.tool_name == "edit" for result in results):
            call = ToolCallBlock(
                "repair-once",
                "edit",
                {
                    "path": "triangle.py",
                    "old_string": "range(n)",
                    "new_string": "range(n + 1)",
                },
            )
        else:
            return assistant_message("Repair applied.", sender="repair", kind="final")
        return assistant_message([call], sender="repair", kind="step")

    return Agent("repair", generate, tools=tools)


def service(
    root: Path, *, crash: bool = False, provider: str = "scripted"
) -> RecoverableRuntimeService:
    executor = RecoverableRunExecutor(
        run_store=FileRunStore(root / "runs"),
        checkpoint_store=FileCheckpointStore(root / "checkpoints"),
        event_journal=FileEventJournal(root / "journal"),
        operation_ledger=FileOperationLedger(root / "operations"),
        evidence_store=FileEvidenceStore(root / "evidence"),
        reconciler_for=lambda operation: EditOperationReconciler(
            workspace=root / "workspace"
        ),
        completion_check=lambda state: verify(root, state),
        max_attempts=3,
        lease_seconds=30 if provider == "openai" else 0.9,
        lease_renew_interval_seconds=5 if provider == "openai" else 0.2,
    )
    return RecoverableRuntimeService(
        executor,
        worker_id=f"worker-{time.time_ns()}",
        agent_for=lambda record: make_worker(root, crash=crash, provider=provider),
    )


def run_demo(
    root: Path, *, hard_crash: bool = False, provider: str = "scripted"
) -> dict[str, object]:
    if root.exists():
        raise FileExistsError(f"Use a new output directory: {root}")
    if hard_crash and provider != "scripted":
        raise ValueError(
            "hard-crash acceptance uses the scripted model to place the fault precisely"
        )
    (root / "workspace").mkdir(parents=True)
    provenance = capture_source(root)
    if provider == "openai":
        from simple_long_horizon_agent.llm.env import provider_from_env

        configured = provider_from_env(read_reasoning=True, default_temperature=None)
        provenance.update(
            model=configured.model,
            api=configured.api,
            reasoning=str(configured.default_reasoning),
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                **provenance,
                "model_mode": provider,
                "hard_crash": hard_crash,
                "task": TASK,
                "fixture_sha256": hashlib.sha256((SOURCE + TEST).encode()).hexdigest(),
                "verifier": "python -B -m unittest test_triangle -v",
                "max_attempts": 3,
            },
            indent=2,
        )
        + "\n"
    )
    (root / "workspace" / "triangle.py").write_text(SOURCE)
    assert not verify(root, State(TASK)).done, "fixture must fail before repair"
    first = service(root, provider=provider)
    first.submit("repair", TASK)
    first.recover_once()
    if first.scheduler.last_errors:
        raise RuntimeError(first.scheduler.last_errors)
    killed_returncode = None
    if hard_crash:
        command = [
            sys.executable,
            "-m",
            __name__
            if __name__ != "__main__"
            else "examples.recoverable_code_task.demo",
            "--worker",
            "--output",
            str(root),
        ]
        with (root / "worker.log").open("w") as log:
            worker = subprocess.Popen(command, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while not (root / "ready-to-kill").exists():
                    if worker.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            f"Worker did not reach the crash window; see {root / 'worker.log'}"
                        )
                    time.sleep(0.02)
                worker.kill()
                killed_returncode = worker.wait(timeout=5)
            finally:
                if worker.poll() is None:
                    worker.kill()
                    worker.wait(timeout=5)
        assert killed_returncode == -signal.SIGKILL
        lease = first.executor.run_store.get("repair")
        while (
            lease.lease_expires_at is not None and time.time() <= lease.lease_expires_at
        ):
            time.sleep(0.02)

    resumed = service(root, provider=provider)
    for _ in range(3):
        if (
            resumed.executor.run_store.get("repair").status != "runnable"
            and not hard_crash
        ):
            break
        resumed.recover_once()
        if resumed.scheduler.last_errors:
            raise RuntimeError(resumed.scheduler.last_errors)
        if resumed.executor.run_store.get("repair").status == "complete":
            break
    status = resumed.executor.run_store.get("repair").status
    if status != "complete":
        raise AssertionError(f"repair did not pass verification: {status}")
    state = resumed.executor.checkpoint_store.load("repair")
    assert verify(root, state).done
    after = (root / "workspace" / "triangle.py").read_text()
    patch = "".join(
        difflib.unified_diff(
            SOURCE.splitlines(True),
            after.splitlines(True),
            fromfile="a/triangle.py",
            tofile="b/triangle.py",
        )
    )
    (root / "repair.patch").write_text(patch)
    from simple_long_horizon_agent.trace import run_trace_from_state, write_event_stream

    write_event_stream(
        root / "trajectory.jsonl",
        run_trace_from_state(
            state=state, trace_id="repair", producer=f"recovery-demo:{provider}"
        ),
    )
    journal = FileEventJournal(root / "journal").read("repair")
    edits = [
        event
        for event in journal
        if event.kind == "tool_execution_start" and event.tool_name == "edit"
    ]
    if provider == "scripted":
        assert len(edits) == 1, "the confirmed edit must not be executed twice"
    result = {
        "model_mode": provider,
        "status": status,
        "hard_crash": hard_crash,
        "killed_returncode": killed_returncode,
        "edit_executions": len(edits),
        "events": len(journal),
        "patch_sha256": hashlib.sha256(patch.encode()).hexdigest(),
        "scope": "code-repair integration acceptance; scripted runs do not measure model quality",
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hard-crash", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--provider", choices=("scripted", "openai"), default="scripted"
    )
    args = parser.parse_args()
    root = args.output.resolve()
    if args.worker:
        worker_service = service(root, crash=True)
        worker_service.recover_once()
        if worker_service.scheduler.last_errors:
            raise RuntimeError(worker_service.scheduler.last_errors)
    else:
        print(
            json.dumps(
                run_demo(root, hard_crash=args.hard_crash, provider=args.provider),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()

"""Multi-file acceptance through the public long-task runner, including SIGKILL."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from scripts.run_long_task import drive, initialize
from simple_long_horizon_agent import (
    Agent,
    ContextPolicy,
    ToolCallBlock,
    ToolCompactStrategy,
    assistant_message,
)
from simple_long_horizon_agent.messages import tool_results_of, text_of
from simple_long_horizon_agent.state import State
from simple_long_horizon_agent.tools.edit import make_edit_tool
from simple_long_horizon_agent.tools.read import make_read_tool
from simple_long_horizon_agent.tools.recall import make_recall_tool


FILES = {
    "ledger/__init__.py": "",
    "ledger/parse.py": "def parse(line):\n    name, amount = line.split(',')\n    return name, int(amount)\n",
    "ledger/totals.py": "def total(rows):\n    return len(rows)\n",
    "ledger/__main__.py": "import sys\nfrom .parse import parse\nfrom .totals import total\nrows = [parse(line) for line in sys.stdin if line.strip()]\nprint('total:', total(rows))\n",
}
EDITS = [
    (
        "ledger/parse.py",
        "return name, int(amount)",
        "return name.strip(), float(amount)",
    ),
    (
        "ledger/totals.py",
        "return len(rows)",
        "return sum(amount for _, amount in rows)",
    ),
    (
        "ledger/__main__.py",
        "print('total:', total(rows))",
        'print(f"{total(rows):.2f}")',
    ),
]
TASK = "Repair ledger: accept decimal amounts, trim names, sum amounts (including refunds), and print only the total with exactly two decimal places. Preserve empty-input behavior as 0.00."
VERIFY = r"""import subprocess
import sys
cases = [('alice,1.25\nbob,2.50\nrefund,-0.25\n', '3.50'), ('', '0.00'), ('a,10\nb,20\n', '30.00')]
for data, expected in cases:
    result = subprocess.run([sys.executable, '-m', 'ledger'], input=data, text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, (result.stdout, expected)
result = subprocess.run([sys.executable, '-c', "from ledger.parse import parse; assert parse(' a ,1.25') == ('a',1.25)"], capture_output=True, text=True, timeout=5)
assert result.returncode == 0, result.stderr
print('All caller-owned checks passed')
"""


def worker(root: Path, state: State, *, crash: bool = False) -> Agent:
    config = json.loads((root / "manifest.json").read_text())
    workspace = Path(config["workspace"])
    edit = make_edit_tool(cwd=workspace)
    if crash:
        execute = edit.execute

        def interrupted(*args):
            result = execute(*args)
            if not result.is_error:
                (root / "ready-to-kill").write_text("written, not confirmed")
                threading.Event().wait(30)
                raise RuntimeError("parent did not kill worker")
            return result

        edit = replace(edit, execute=interrupted)

    def generate(visible):
        failure = root / "injected-failure"
        if not failure.exists():
            failure.write_text("once")
            raise RuntimeError("injected transient provider failure")
        if not any(
            "External verification" in text_of(message.content)
            for message in state.messages
        ):
            return assistant_message("Already complete.", sender="worker", kind="final")
        completed = {
            result.tool_call_id
            for message in state.messages
            for result in tool_results_of(message.content)
        }
        for index, path in enumerate(FILES):
            call_id = f"read-{index}"
            if call_id not in completed:
                return assistant_message(
                    [ToolCallBlock(call_id, "read", {"path": path})],
                    sender="worker",
                    kind="step",
                )
        if "recall-original" not in completed:
            index = next(
                index
                for index, message in enumerate(state.messages)
                if any(
                    result.tool_call_id == "read-1"
                    for result in tool_results_of(message.content)
                )
            )
            return assistant_message(
                [ToolCallBlock("recall-original", "recall", {"indices": [index]})],
                sender="worker",
                kind="step",
            )
        for index, (path, old, new) in enumerate(EDITS):
            if f"edit-{index}" not in completed:
                return assistant_message(
                    [
                        ToolCallBlock(
                            f"edit-{index}",
                            "edit",
                            {"path": path, "old_string": old, "new_string": new},
                        )
                    ],
                    sender="worker",
                    kind="step",
                )
        return assistant_message(
            "Repair applied; ready for independent checks.",
            sender="worker",
            kind="final",
        )

    return Agent(
        "worker",
        generate,
        tools=(make_read_tool(cwd=workspace), edit, make_recall_tool(state)),
        context_policy=ContextPolicy(
            strategy=ToolCompactStrategy(200, preview_chars=60)
        ),
    )


def acceptance(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    workspace = output / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    for name, body in FILES.items():
        path = workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body + "# historical context for compaction\n" * 50)
    (workspace / ".gitignore").write_text("__pycache__/\n")
    verification = output / "verify.py"
    verification.write_text(VERIFY)
    import shlex

    command = f"{shlex.quote(sys.executable)} -B {shlex.quote(str(verification))}"
    root = output / "run"
    initialize(
        root,
        workspace,
        TASK,
        command,
        compress_tokens=200,
        model="scripted",
        max_attempts=6,
    )
    before = subprocess.run(
        ["bash", "-lc", command], cwd=workspace, capture_output=True, text=True
    )
    assert before.returncode != 0, "fixture must fail before repair"
    (root / "baseline-verification.json").write_text(
        json.dumps(
            {
                "returncode": before.returncode,
                "stdout": before.stdout,
                "stderr": before.stderr,
            }
        )
    )
    for _ in range(2):
        result = drive(
            root,
            agent_for_state=lambda state: worker(root, state),
            once=True,
            lease_seconds=0.9,
        )
        assert result["status"] == "runnable", result
    with (root / "worker.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "examples.recoverable_code_task.long_task",
                "--worker",
                "--output",
                str(root),
            ],
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 15
            while not (root / "ready-to-kill").exists():
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"Worker did not reach crash window; see {root / 'worker.log'}"
                    )
                time.sleep(0.02)
            process.kill()
            assert process.wait(timeout=5) == -signal.SIGKILL
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    from simple_long_horizon_agent import FileRunStore, SqliteCheckpointStore

    lease = FileRunStore(root / "runs").get("task")
    while lease.lease_expires_at is not None and time.time() <= lease.lease_expires_at:
        time.sleep(0.02)
    result = drive(
        root, agent_for_state=lambda state: worker(root, state), lease_seconds=0.9
    )
    state = SqliteCheckpointStore(root / "state").load("task")
    edits = [
        event
        for event in state.events
        if event.kind == "tool_execution_start" and event.tool_name == "edit"
    ]
    compressions = sum(event.kind == "context_compression" for event in state.events)
    assert result["status"] == "complete", result
    assert len(edits) == 3, "an interrupted edit was dispatched twice"
    assert compressions > 0, "acceptance did not exercise compaction"
    result.update(
        edit_executions=len(edits),
        compressions=compressions,
        killed_returncode=-signal.SIGKILL,
        scope="scripted integration acceptance, not real-model task quality",
    )
    (root / "acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = args.output.resolve()
    if args.worker:
        drive(
            root,
            agent_for_state=lambda state: worker(root, state, crash=True),
            once=True,
            lease_seconds=0.9,
        )
    else:
        print(json.dumps(acceptance(root), indent=2))


if __name__ == "__main__":
    main()

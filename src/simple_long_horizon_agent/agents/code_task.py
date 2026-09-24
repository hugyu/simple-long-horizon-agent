"""Repository guidance, explicit task notes, and delivery review for code tasks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any
from collections.abc import Callable

from simple_long_horizon_agent.state import State
from simple_long_horizon_agent.messages import message_text
from simple_long_horizon_agent.tools import AgentTool, ToolResult, text_result


def repository_files(workspace: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=workspace,
        capture_output=True,
        check=True,
    )
    return sorted(set(result.stdout.decode().split("\0")) - {""})


def repository_guidance(workspace: Path) -> str:
    """Include scoped instructions; fail explicitly rather than truncate rules."""
    workspace = workspace.resolve()
    top = Path(
        subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"], cwd=workspace, text=True
        ).strip()
    )
    top = top.resolve()
    if top != workspace:
        raise ValueError(f"workspace must be the Git root {top}, got {workspace}")
    paths = set(
        workspace / name
        for name in repository_files(workspace)
        if Path(name).name == "AGENTS.md"
    )
    if (workspace / "AGENTS.md").exists():
        paths.add(workspace / "AGENTS.md")
    sections = [
        "Repository instructions below apply only within each named directory and its descendants; deeper rules override ancestor rules."
    ]
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError(f"Repository instruction must not be a symlink: {path}")
        sections.append(
            f"Scope: {path.parent.relative_to(workspace)}\n{path.read_text()}"
        )
    text = "\n\n".join(sections)
    if len(text) > 64000:
        raise ValueError(
            "Repository instructions exceed 64000 characters; choose a smaller task repository"
        )
    return text


def make_task_status_tool(
    state: State, *, save: Callable[[], None] | None = None
) -> AgentTool:
    """Model-owned notes are recoverable progress claims, never completion proof."""

    def execute(call_id, args, abort, on_update) -> ToolResult:
        del call_id, abort, on_update
        if "requirements" in args:
            requirements = args["requirements"]
            if not isinstance(requirements, list) or not 1 <= len(requirements) <= 30:
                return text_result(
                    "requirements must contain 1 to 30 items", is_error=True
                )
            for item in requirements:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("requirement"), str)
                    or not item["requirement"].strip()
                    or item.get("status") not in {"pending", "in_progress", "done"}
                ):
                    return text_result(
                        "Each requirement needs nonempty requirement text and pending/in_progress/done status",
                        is_error=True,
                    )
                evidence = item.get("evidence", [])
                if not isinstance(evidence, list) or any(
                    type(index) is not int or not 0 <= index < len(state.messages)
                    for index in evidence
                ):
                    return text_result(
                        "evidence must reference existing transcript message indices",
                        is_error=True,
                    )
                if item["status"] == "done" and not evidence:
                    return text_result(
                        "Done requirements need evidence message indices; external verification remains authoritative",
                        is_error=True,
                    )
            note = {
                "requirements": requirements,
                "next_action": args.get("next_action", ""),
                "blocker": args.get("blocker", ""),
            }
            if (
                not isinstance(note["next_action"], str)
                or not isinstance(note["blocker"], str)
                or len(json.dumps(note)) > 12000
            ):
                return text_result(
                    "Task notes must use strings and fit within 12000 characters",
                    is_error=True,
                )
            with state.write_guard():
                previous = state.data.get("task_progress")
                state.data["task_progress"] = note
                try:
                    if save is not None:
                        save()
                except Exception:
                    if previous is None:
                        state.data.pop("task_progress", None)
                    else:
                        state.data["task_progress"] = previous
                    raise
        progress = state.data.get(
            "task_progress", {"requirements": [], "next_action": "derive requirements"}
        )
        recent = [
            {
                "index": index,
                "kind": state.messages[index].kind,
                "preview": message_text(state.messages[index])[:250],
            }
            for index in range(max(0, len(state.messages) - 8), len(state.messages))
        ]
        return text_result(
            json.dumps(
                {"progress": progress, "recent_evidence": recent}, ensure_ascii=False
            )
        )

    return AgentTool(
        name="task_status",
        description="Read or replace the requirement checklist, evidence references, blocker and next action. Use after compaction or recovery. These notes do not declare the run complete.",
        parameters={
            "type": "object",
            "properties": {
                "requirements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "requirement": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done"],
                            },
                            "evidence": {"type": "array", "items": {"type": "integer"}},
                        },
                        "required": ["requirement", "status"],
                    },
                },
                "next_action": {"type": "string"},
                "blocker": {"type": "string"},
            },
            "additionalProperties": False,
        },
        execute=execute,
        execution_mode="sequential",
    )


def fingerprint(path: Path) -> str | None:
    if path.is_symlink():
        return "symlink:" + str(path.readlink())
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def delivery_review(workspace: Path, before: Path) -> dict[str, Any]:
    original = {
        str(path.relative_to(before))
        for path in before.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    names = original | set(repository_files(workspace))
    changes = []
    protected = []
    for name in sorted(names):
        old, new = fingerprint(before / name), fingerprint(workspace / name)
        if old == new:
            continue
        changes.append({"path": name, "before": old, "after": new})
        path = Path(name)
        if old is not None and (
            path.name == "AGENTS.md"
            or "tests" in path.parts
            or path.name.startswith("test_")
            or path.name.endswith("_test.py")
        ):
            protected.append(name)
    diagnostics = []
    with tempfile.TemporaryDirectory() as temporary:
        empty = Path(temporary) / "empty"
        empty.write_text("")
        for change in changes:
            name = change["path"]
            current = workspace / name
            if not current.is_file() or current.is_symlink():
                continue
            original_path = before / name
            previous = (
                original_path
                if original_path.is_file() and not original_path.is_symlink()
                else empty
            )
            whitespace = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-index",
                    "--check",
                    "--",
                    str(previous),
                    str(current),
                ],
                capture_output=True,
                text=True,
            )
            if (
                whitespace.returncode not in (0, 1)
                or whitespace.stdout
                or whitespace.stderr
            ):
                diagnostics.append(
                    whitespace.stdout + whitespace.stderr
                    or f"diff check failed for {name}"
                )
    return {
        "changes": changes,
        "protected_changes": protected,
        "diff_check_passed": not diagnostics,
        "diff_check_output": "\n".join(diagnostics),
    }

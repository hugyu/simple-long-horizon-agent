from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from simple_long_horizon_agent import State, text_of
from simple_long_horizon_agent.memory import FilesystemMemory, MemoryContext
from simple_long_horizon_agent.memory.filesystem import filesystem_distillation_prompt


class MemoryMaintenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = self.root / "project"
        self.memory = FilesystemMemory(root=self.root)
        self.memory.ensure_layout(self.directory)
        self.old = "- Use the old test command.\n  Scope: project\n  Source: runs/old/transcript.md"
        self.other = "- Keep this independent preference."
        self.write_handbook(f"# Memory Handbook\n\n{self.old}\n{self.other}\n")

    def write_handbook(self, text: str) -> None:
        (self.directory / "MEMORY.md").write_text(text)

    def context(self, run_id: str, **kwargs) -> MemoryContext:
        state = State("User correction: use the new test command.")
        state.send("task", "user", "worker", state.task)
        return MemoryContext(
            agent="worker",
            task=str(state.task),
            state=state,
            memory_name="project",
            run_id=run_id,
            **kwargs,
        )

    def test_invalidate_removes_whole_entry_and_clears_stale_navigation(self) -> None:
        (self.directory / "memory_summary.md").write_text("Use the old test command.")
        self.memory.invalidate(
            "project", lesson=self.old, reason="User corrected the command"
        )
        current = (self.directory / "MEMORY.md").read_text()
        self.assertNotIn("old test command", current)
        self.assertNotIn("Source: runs/old", current)
        self.assertIn(self.other, current)
        self.assertIn(self.old, (self.directory / "MEMORY.previous.md").read_text())
        self.assertNotIn(
            "old test command", (self.directory / "memory_summary.md").read_text()
        )
        control = json.loads((self.directory / "MEMORY.control.json").read_text())
        self.assertEqual(control["invalidated"][self.old], "User corrected the command")
        visible = text_of(self.memory.initial(self.context("next"))[0].content)
        self.assertIn("MEMORY.control.json", visible)
        self.assertIn("Scope matches", visible)

    def test_invalid_arguments_do_not_change_handbook(self) -> None:
        before = (self.directory / "MEMORY.md").read_text()
        for name, lesson, reason in (
            ("project", "old", "reason"),
            ("project", self.old, ""),
            ("typo", self.old, "reason"),
        ):
            with self.subTest(name=name, lesson=lesson, reason=reason):
                with self.assertRaises(ValueError):
                    self.memory.invalidate(name, lesson=lesson, reason=reason)
        self.assertEqual(before, (self.directory / "MEMORY.md").read_text())

    def test_reset_keeps_evidence_and_allows_new_learning(self) -> None:
        evidence = self.directory / "runs/old"
        evidence.mkdir()
        (evidence / "transcript.md").write_text("historical evidence")
        self.memory.reset("project", reason="Project migrated to a new environment")
        self.assertNotIn(self.other, (self.directory / "MEMORY.md").read_text())
        self.assertEqual(
            (evidence / "transcript.md").read_text(), "historical evidence"
        )
        self.assertTrue(
            json.loads((self.directory / "MEMORY.control.json").read_text())["reset"]
        )
        self.memory.distiller = lambda payload: {
            "memory_md": "# Memory Handbook\n- A new confirmed workflow."
        }
        self.memory.finish(self.context("new"))
        self.assertIn(
            "new confirmed workflow", (self.directory / "MEMORY.md").read_text()
        )

    def test_retired_advice_cannot_be_restored_by_changing_metadata(self) -> None:
        self.memory.invalidate("project", lesson=self.old, reason="obsolete")

        def distill(payload):
            prompt = filesystem_distillation_prompt(payload)
            self.assertIn("obsolete", prompt)
            self.assertIn("single unexplained failure", prompt)
            self.assertIn("Scope, Source, Verified, Status, and Recheck", prompt)
            return {
                "memory_md": "# Memory Handbook\n- Use the old test command.\n  Source: runs/new/transcript.md",
                "memory_summary_md": "v1\nUse the old test command.",
            }

        self.memory.distiller = distill
        self.memory.finish(self.context("new"))
        self.assertNotIn("old test command", (self.directory / "MEMORY.md").read_text())
        self.assertNotIn(
            "old test command", (self.directory / "memory_summary.md").read_text()
        )
        self.assertIn(
            "invalidated lesson",
            (self.directory / "runs/new/memory_error.md").read_text(),
        )

    def test_update_tracks_each_source_and_preserves_unchanged_provenance(self) -> None:
        self.memory.distiller = lambda payload: {
            "memory_md": f"# Memory Handbook\n{self.other}\n- Use the new test command."
        }
        self.memory.finish(self.context("first", data={"memory_revision": "abc123"}))
        first = json.loads((self.directory / "MEMORY.provenance.json").read_text())
        self.assertEqual(first["lessons"][0]["source"], "legacy:unknown")
        self.assertEqual(first["lessons"][1]["source"], "runs/first/transcript.md")
        self.assertEqual(first["lessons"][1]["observed_revision"], "abc123")
        self.assertNotIn("old test command", (self.directory / "MEMORY.md").read_text())
        self.memory.distiller = lambda payload: {
            "memory_md": payload.notes + "\n- Another lesson."
        }
        self.memory.finish(self.context("second"))
        second = json.loads((self.directory / "MEMORY.provenance.json").read_text())
        self.assertEqual(second["lessons"][:2], first["lessons"])
        self.assertEqual(second["lessons"][2]["observed_revision"], "unknown")
        self.assertEqual(
            second["handbook_sha256"],
            hashlib.sha256((self.directory / "MEMORY.md").read_bytes()).hexdigest(),
        )

    def test_empty_model_update_does_not_reset_or_change_provenance(self) -> None:
        before = (self.directory / "MEMORY.md").read_text()
        self.memory.distiller = lambda payload: {"memory_md": ""}
        self.memory.finish(self.context("empty"))
        self.assertEqual(before, (self.directory / "MEMORY.md").read_text())
        self.assertFalse((self.directory / "MEMORY.provenance.json").exists())

    def test_interrupted_reset_leaves_authoritative_invalidation_barrier(self) -> None:
        from simple_long_horizon_agent.memory import filesystem

        original = filesystem._write_text_atomic

        def interrupted(path, text):
            if path.name == "MEMORY.previous.md":
                raise OSError("interrupted")
            original(path, text)

        with mock.patch.object(
            filesystem, "_write_text_atomic", side_effect=interrupted
        ):
            with self.assertRaises(OSError):
                self.memory.reset("project", reason="migration")
        self.assertTrue(
            json.loads((self.directory / "MEMORY.control.json").read_text())["reset"]
        )
        self.assertIn(self.old, (self.directory / "MEMORY.md").read_text())
        self.memory.reset("project", reason="migration")
        self.assertNotIn(self.old, (self.directory / "MEMORY.md").read_text())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_long_horizon_agent import FileWorkspaceManager, WorkspaceRef


class WorkspaceManagerTest(unittest.TestCase):
    def test_create_resolve_and_retain_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            manager = FileWorkspaceManager(Path(raw_root))
            ref = manager.create("run-1")
            path = manager.resolve(ref)
            (path / "note.txt").write_text("work", encoding="utf-8")
            manager.release(ref)
            self.assertEqual(manager.resolve("run-1"), path)
            self.assertEqual(ref, WorkspaceRef("run-1"))

    def test_create_can_copy_a_baseline_and_reject_escape(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            source = root / "source"
            source.mkdir()
            (source / "README.md").write_text("base", encoding="utf-8")
            manager = FileWorkspaceManager(root / "workspaces")
            ref = manager.create("run-2", source=source)
            self.assertEqual(
                manager.resolve(ref).joinpath("README.md").read_text(encoding="utf-8"),
                "base",
            )
            with self.assertRaises(ValueError):
                manager.resolve("../outside")

    def test_release_remove_and_gc(self) -> None:
        now = [100.0]
        with tempfile.TemporaryDirectory() as raw_root:
            manager = FileWorkspaceManager(Path(raw_root), clock=lambda: now[0])
            old = manager.create("old")
            new = manager.create("new")
            old_path = manager.resolve(old)
            old_path.touch()
            import os

            os.utime(old_path, (90, 90))
            removed = manager.gc(older_than_seconds=5)
            self.assertEqual(removed, [old])
            with self.assertRaises(FileNotFoundError):
                manager.resolve(old)
            manager.release(new, remove=True)
            with self.assertRaises(FileNotFoundError):
                manager.resolve(new)


if __name__ == "__main__":
    unittest.main()

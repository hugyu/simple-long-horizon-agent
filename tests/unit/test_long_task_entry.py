"""Exercise the unified runner through independent verification and process death."""

import json
from pathlib import Path
import tempfile
import unittest

from examples.recoverable_code_task.long_task import acceptance
from scripts.run_long_task import drive, initialize
from simple_long_horizon_agent import Agent, assistant_message


class LongTaskEntryTest(unittest.TestCase):
    def test_multifile_crash_compression_and_recall(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "acceptance"
            result = acceptance(output)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["edit_executions"], 3)
            self.assertGreater(result["compressions"], 0)
            root = output / "run"
            for name in (
                "manifest.json",
                "source.tar.gz",
                "trajectory.jsonl",
                "repair.patch",
                "verification.jsonl",
                "cost.json",
            ):
                self.assertTrue((root / name).stat().st_size)
            verdicts = [
                json.loads(line)
                for line in (root / "verification.jsonl").read_text().splitlines()
            ]
            self.assertNotEqual(verdicts[0]["returncode"], 0)
            self.assertEqual(verdicts[-1]["returncode"], 0)
            repeated = drive(
                root,
                agent_for_state=lambda state: self.fail(
                    "terminal run must not restart"
                ),
            )
            self.assertEqual(repeated["attempts_started"], result["attempts_started"])

    def test_builder_failures_exhaust_budget(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            initialize(root / "run", workspace, "task", "exit 1", max_attempts=2)

            def build(state):
                raise RuntimeError("factory unavailable")

            result = drive(root / "run", agent_for_state=build)
            self.assertEqual(result["status"], "budget_exhausted")
            self.assertEqual(result["attempts_started"], 2)

    def test_model_claim_does_not_override_verifier(self):
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            initialize(root / "run", workspace, "task", "exit 1", max_attempts=2)
            result = drive(
                root / "run",
                agent_for_state=lambda state: Agent(
                    "worker",
                    lambda visible: assistant_message(
                        "done", sender="worker", kind="final"
                    ),
                ),
            )
            self.assertEqual(result["status"], "budget_exhausted")

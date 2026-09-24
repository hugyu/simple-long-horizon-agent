import contextlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from evals.context_ablation import experiment


class ContextAblationTest(unittest.TestCase):
    def test_paired_run_preserves_conditions_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "experiment"
            with contextlib.redirect_stdout(io.StringIO()):
                summary = experiment(output, cases=2, mode="scripted")
            self.assertEqual(summary["variants"]["baseline"]["passed"], 2)
            self.assertEqual(summary["variants"]["compact"]["passed"], 2)
            rows = [
                json.loads(line)
                for line in (output / "results.jsonl").read_text().splitlines()
            ]
            self.assertEqual(
                [(row["case"], row["variant"]) for row in rows],
                [(0, "baseline"), (0, "compact"), (1, "compact"), (1, "baseline")],
            )
            self.assertTrue(
                all(
                    row["compactions"] > 0
                    for row in rows
                    if row["variant"] == "compact"
                )
            )
            self.assertTrue(
                all(
                    row["compactions"] == 0
                    for row in rows
                    if row["variant"] == "baseline"
                )
            )
            self.assertTrue(all(row["estimated_cost_usd"] is None for row in rows))
            with tarfile.open(output / "source.tar.gz") as archive:
                names = archive.getnames()
            self.assertIn("evals/context_ablation.py", names)
            self.assertIn("uv.lock", names)
            self.assertFalse(
                any(".env" in name or name.startswith("evals/out/") for name in names)
            )

    def test_failed_cases_remain_in_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "experiment"
            with patch(
                "evals.context_ablation.scripted",
                side_effect=RuntimeError("provider failure"),
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    summary = experiment(output, cases=1, mode="scripted")
            self.assertEqual(summary["variants"]["baseline"]["tasks"], 1)
            self.assertEqual(summary["variants"]["baseline"]["passed"], 0)
            self.assertEqual(summary["variants"]["compact"]["passed"], 0)
            rows = [
                json.loads(line)
                for line in (output / "results.jsonl").read_text().splitlines()
            ]
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["failure"] == "RuntimeError" for row in rows))

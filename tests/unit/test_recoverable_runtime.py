from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_long_horizon_agent import (
    Agent,
    FileCheckpointStore,
    FileRunStore,
    RecoverableRun,
    RecoverableRunExecutor,
    State,
    assistant_message,
)


class RecoverableRuntimeTest(unittest.TestCase):
    def test_run_is_checkpointed_and_completed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                checkpoint_every_events=1,
            )
            state = State("finish task")
            executor.create("run-1", state, worker_id="worker-a")
            agent = Agent(
                "writer",
                lambda visible: assistant_message(
                    "done", sender="writer", target="user", kind="final"
                ),
            )
            resumed, events = executor.execute(
                RecoverableRun("run-1", "run-1", "worker-a"), agent
            )
            list(events)

            self.assertTrue(resumed.events)
            self.assertEqual(executor.run_store.get("run-1").status, "complete")
            self.assertEqual(
                executor.checkpoint_store.load("run-1").events, resumed.events
            )

    def test_exception_checkpoints_and_releases_for_takeover(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            run_store = FileRunStore(root / "runs")
            executor = RecoverableRunExecutor(
                run_store=run_store,
                checkpoint_store=FileCheckpointStore(root / "checkpoints"),
                lease_seconds=30,
            )
            executor.create("run-2", State("continue"), worker_id="worker-a")
            agent = Agent(
                "writer",
                lambda visible: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            _, events = executor.execute(
                RecoverableRun("run-2", "run-2", "worker-a"), agent
            )
            with self.assertRaises(RuntimeError):
                list(events)
            self.assertEqual(run_store.get("run-2").status, "runnable")

            takeover = RecoverableRun("run-2", "run-2", "worker-b")
            safe_agent = Agent(
                "writer",
                lambda visible: assistant_message(
                    "continued", sender="writer", target="user", kind="final"
                ),
            )
            _, resumed_events = executor.execute(takeover, safe_agent)
            list(resumed_events)
            self.assertEqual(run_store.get("run-2").status, "complete")


if __name__ == "__main__":
    unittest.main()

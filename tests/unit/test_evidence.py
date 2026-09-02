from __future__ import annotations

import tempfile
import unittest

from simple_long_horizon_agent import (
    AgentEndEvent,
    EvidencePack,
    FileEvidenceStore,
    SkillInvokedEvent,
    State,
    ToolExecutionEndEvent,
    evidence_pack_from_state,
)
from simple_long_horizon_agent.checkpoint import (
    state_from_checkpoint,
    state_to_checkpoint,
)


class EvidencePackTest(unittest.TestCase):
    def test_evidence_pack_summarizes_runtime_facts(self) -> None:
        state = State("fix bug")
        state.data.update(
            {
                "run_id": "run-1",
                "workspace_ref": "workspace-1",
                "verification": {"tests": "passed"},
            }
        )
        state.record_event(
            SkillInvokedEvent(
                skill_name="pytest",
                version="a" * 64,
                input_sha256="b" * 64,
                source="repo",
                trigger="mention",
            )
        )
        state.record_event(
            ToolExecutionEndEvent(
                tool_call_id="call-1", tool_name="bash", is_error=False, terminate=False
            )
        )
        state.record_event(
            ToolExecutionEndEvent(
                tool_call_id="call-2", tool_name="edit", is_error=True, terminate=False
            )
        )
        state.record_event(AgentEndEvent(reason="done"))

        pack = evidence_pack_from_state(state)
        self.assertIsInstance(pack, EvidencePack)
        self.assertEqual(pack.run_id, "run-1")
        self.assertEqual(pack.workspace_ref, "workspace-1")
        self.assertEqual(pack.tool_calls, 2)
        self.assertEqual(pack.tool_errors, 1)
        self.assertEqual(pack.stop_reason, "done")
        self.assertEqual(pack.verification, {"tests": "passed"})
        self.assertEqual(pack.as_dict()["skills"][0]["name"], "pytest")
        self.assertEqual(pack.as_dict()["skills"][0]["input_sha256"], "b" * 64)

    def test_skill_observation_survives_checkpoint_round_trip(self) -> None:
        state = State("task")
        state.record_event(
            SkillInvokedEvent(
                skill_name="skill",
                version="a" * 64,
                input_sha256="b" * 64,
            )
        )
        restored = state_from_checkpoint(state_to_checkpoint(state))
        self.assertEqual(restored.events, state.events)

    def test_file_store_round_trips_and_replaces_atomically(self) -> None:
        state = State("task")
        state.data.update({"run_id": "run-1", "workspace_ref": "workspace-1"})
        pack = evidence_pack_from_state(state)
        with tempfile.TemporaryDirectory() as raw_root:
            store = FileEvidenceStore(raw_root)
            path = store.save("run-1", pack)
            self.assertTrue(path.is_file())
            self.assertEqual(store.load("run-1"), pack)
            self.assertFalse(list(path.parent.glob("*.part")))


if __name__ == "__main__":
    unittest.main()

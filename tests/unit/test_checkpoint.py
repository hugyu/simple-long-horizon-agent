from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from simple_long_horizon_agent import (
    Agent,
    AgentEndEvent,
    ContextCompressionEvent,
    State,
    TokenUsage,
    ToolCallBlock,
    assistant_message,
    runtime_message,
    tool_result_message,
)
from simple_long_horizon_agent.checkpoint import (
    CHECKPOINT_SCHEMA,
    FileCheckpointStore,
    state_from_checkpoint,
    state_to_checkpoint,
)


class CheckpointTest(unittest.TestCase):
    def _state(self) -> State:
        state = State("fix the bug")
        state.send("task", "user", "solver", "fix the bug")
        state.record(runtime_message("policy", target="solver"))
        state.record(
            assistant_message(
                [
                    ToolCallBlock("call-1", "edit", {"path": "a.py"}),
                ],
                sender="solver",
                target="user",
                kind="step",
                usage=TokenUsage(input_tokens=10, output_tokens=4),
                model="fake-model",
            )
        )
        state.record(
            tool_result_message(
                "changed",
                tool_call_id="call-1",
                tool_name="edit",
                target="solver",
            )
        )
        state.record_event(
            ContextCompressionEvent(
                agent="solver",
                summary_message_index=1,
                compressed_message_indices=[0],
                active_context_indices=[1, 2, 3],
                before_tokens=100,
                after_tokens=70,
                strategy="test",
            )
        )
        state.record_event(AgentEndEvent(reason="max_turns"))
        state.data["workspace_ref"] = "workspace-1"
        return state

    def test_round_trip_preserves_events_messages_and_active_context(self) -> None:
        original = self._state()
        restored = state_from_checkpoint(state_to_checkpoint(original))

        self.assertEqual(restored.task, original.task)
        self.assertEqual(restored.data, original.data)
        self.assertEqual(restored.events, original.events)
        self.assertEqual(restored.messages, original.messages)
        self.assertEqual(
            restored.active_context_items(), original.active_context_items()
        )

    def test_file_store_writes_atomically_and_loads(self) -> None:
        original = self._state()
        with tempfile.TemporaryDirectory() as tmp:
            store = FileCheckpointStore(Path(tmp))
            path = store.save("run-1", original)
            self.assertTrue(path.is_file())
            self.assertEqual(store.load("run-1").events, original.events)
            self.assertFalse(list(Path(tmp).glob("*.part")))

    def test_restored_state_can_resume_without_resetting_elapsed_time(self) -> None:
        original = self._state()
        restored = state_from_checkpoint(state_to_checkpoint(original))
        before = restored.events[-1].elapsed

        agent = Agent(
            "solver",
            lambda visible: assistant_message(
                "continued", sender="solver", target="user", kind="final"
            ),
        )
        _, events = agent.resume(restored, "continue", max_turns=1)
        list(events)

        self.assertGreaterEqual(restored.events[-1].elapsed, before)

    def test_rejects_hash_tampering_and_unknown_schema(self) -> None:
        payload = state_to_checkpoint(self._state())
        payload["body"]["data"]["workspace_ref"] = "changed"
        with self.assertRaisesRegex(ValueError, "hash"):
            state_from_checkpoint(payload)

        payload = state_to_checkpoint(self._state())
        payload["schema"] = "unknown"
        with self.assertRaisesRegex(ValueError, "schema"):
            state_from_checkpoint(payload)

    def test_checkpoint_has_expected_schema_and_coverage(self) -> None:
        state = self._state()
        payload = state_to_checkpoint(state)
        self.assertEqual(payload["schema"], CHECKPOINT_SCHEMA)
        self.assertEqual(payload["covered_event_index"], 5)
        self.assertEqual(payload["covered_event_uuid"], state.events[-1].uuid)


if __name__ == "__main__":
    unittest.main()

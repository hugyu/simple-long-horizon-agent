"""Durable limits must survive rebuilding the executor after failures."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from simple_long_horizon_agent import (
    Agent,
    FileRunStore,
    RecoverableRunExecutor,
    State,
    SqliteCheckpointStore,
    assistant_message,
)
from simple_long_horizon_agent.messages import TokenUsage
from simple_long_horizon_agent.completion import CompletionResult


class RunBudgetTest(unittest.TestCase):
    def executor(self, root, **kwargs):
        return RecoverableRunExecutor(
            run_store=FileRunStore(root / "runs"),
            checkpoint_store=SqliteCheckpointStore(root / "state"),
            **kwargs,
        )

    def test_exception_attempt_limit_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.executor(root, max_attempts=2)
            handle = first.create("task", State("task"), worker_id="worker")
            calls = []

            def fail(visible):
                calls.append(1)
                raise RuntimeError("permanent failure")

            agent = Agent("worker", fail)
            for index in range(2):
                executor = (
                    first if index == 0 else self.executor(root, max_attempts=100)
                )
                _, events = executor.execute(handle, agent)
                with self.assertRaisesRegex(RuntimeError, "permanent failure"):
                    list(events)
            self.assertEqual(len(calls), 2)
            self.assertEqual(executor.run_store.get("task").status, "budget_exhausted")
            self.assertEqual(
                executor.checkpoint_store.load("task").data["attempts_started"], 2
            )

    def test_model_call_limit_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.executor(
                root,
                max_attempts=10,
                max_model_calls=1,
                completion_check=lambda state: CompletionResult(False),
            )
            handle = first.create("task", State("task"), worker_id="worker")
            calls = []

            def generate(visible):
                calls.append(1)
                return assistant_message("not done", sender="worker", kind="final")

            agent = Agent("worker", generate)
            _, events = first.execute(handle, agent)
            list(events)
            second = self.executor(
                root,
                max_model_calls=100,
                completion_check=lambda state: CompletionResult(False),
            )
            _, events = second.execute(handle, agent)
            list(events)
            self.assertEqual(len(calls), 1)
            self.assertEqual(second.run_store.get("task").status, "budget_exhausted")

    def test_deadline_includes_downtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.executor(root, wall_clock_seconds=5)
            with patch(
                "simple_long_horizon_agent.recoverable_runtime.time.time",
                return_value=100,
            ):
                handle = first.create("task", State("task"), worker_id="worker")
            agent = Agent(
                "worker", lambda visible: self.fail("expired run called model")
            )
            second = self.executor(root, wall_clock_seconds=10000)
            _, events = second.execute(handle, agent)
            list(events)
            self.assertEqual(second.run_store.get("task").status, "budget_exhausted")

    def test_tokens_stop_before_tools(self):
        from simple_long_horizon_agent import ToolCallBlock
        from simple_long_horizon_agent.tools import AgentTool, text_result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = self.executor(root, max_tokens=10)
            handle = executor.create("task", State("task"), worker_id="worker")
            calls = []
            tool = AgentTool(
                name="write",
                description="write",
                parameters={},
                execute=lambda *args: calls.append(1) or text_result("ok"),
            )
            from dataclasses import replace

            output = replace(
                assistant_message(
                    [ToolCallBlock("one", "write", {})], sender="worker", kind="step"
                ),
                usage=TokenUsage(input_tokens=8, output_tokens=3),
            )
            _, events = executor.execute(
                handle, Agent("worker", lambda visible: output, tools=(tool,))
            )
            list(events)
            self.assertEqual(calls, [])
            self.assertEqual(executor.run_store.get("task").status, "budget_exhausted")

    def test_invalid_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            for kwargs in (
                {"max_tokens": 0},
                {"max_model_calls": -1},
                {"wall_clock_seconds": float("nan")},
            ):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.executor(Path(directory), **kwargs)

    def test_last_allowed_call_can_still_pass_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executor = self.executor(
                root,
                max_model_calls=1,
                completion_check=lambda state: CompletionResult(True),
            )
            handle = executor.create("task", State("task"), worker_id="worker")
            agent = Agent(
                "worker",
                lambda visible: assistant_message("work", sender="worker", kind="step"),
            )
            _, events = executor.execute(handle, agent, max_turns=1)
            list(events)
            self.assertEqual(executor.run_store.get("task").status, "complete")

    def test_disabling_inner_retries_surfaces_first_failure(self):
        from simple_long_horizon_agent.llm_agent import make_llm_agent
        from simple_long_horizon_agent.llm.env import FAKE_PROVIDER

        agent = make_llm_agent(
            name="worker", provider=FAKE_PROVIDER, retry_model_calls=False
        )
        with (
            patch(
                "simple_long_horizon_agent.llm_agent.complete",
                side_effect=RuntimeError("429"),
            ) as call,
            patch(
                "simple_long_horizon_agent.llm_agent.complete_with_tool_call_retry"
            ) as retry,
        ):
            _, events = agent.run("task")
            with self.assertRaisesRegex(RuntimeError, "429"):
                list(events)
            self.assertEqual(call.call_count, 1)
            retry.assert_not_called()

    def test_json_request_reservation_is_checkpointed_before_call(self):
        from simple_long_horizon_agent import FileCheckpointStore
        from simple_long_horizon_agent.protocols import ModelRequestEvent

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = FileCheckpointStore(root / "state")
            executor = RecoverableRunExecutor(
                run_store=FileRunStore(root / "runs"),
                checkpoint_store=store,
                max_model_calls=1,
                checkpoint_every_events=1000,
            )
            handle = executor.create("task", State("task"), worker_id="worker")

            def generate(visible):
                restored = store.load("task")
                self.assertEqual(
                    sum(
                        isinstance(event, ModelRequestEvent)
                        for event in restored.events
                    ),
                    1,
                )
                return assistant_message("done", sender="worker", kind="final")

            _, events = executor.execute(handle, Agent("worker", generate))
            list(events)

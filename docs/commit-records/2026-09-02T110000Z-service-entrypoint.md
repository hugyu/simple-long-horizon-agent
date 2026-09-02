# Commit Record: Add recoverable runtime service entry point

- Date: 2026-09-02T11:00:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add recoverable service entry point`

## Summary

Add a thin service facade that wires durable Run submission and resident
recovery into a process lifecycle. The facade keeps the core runtime explicit:
it delegates lease acquisition and execution to `RecoverableRunExecutor`, while
callers provide an Agent factory for rebuilding execution inputs after restart.

## Changed Areas

- `src/simple_long_horizon_agent/service.py`: add
  `RecoverableRuntimeService` with `submit`, `recover_once`, `start`, `stop`,
  and context-manager lifecycle methods.
- `src/simple_long_horizon_agent/__init__.py`: expose the service facade and
  `AgentFactory`.
- `scripts/arch_lint.py`: classify the new service module in the core zone.
- `tests/unit/test_recoverable_runtime.py`: cover durable submission, Agent
  rebuilding, and service context-manager shutdown.
- `docs/design/16-recoverable-code-task-runtime.md`: document the service entry
  contract and its framework-neutral boundary.

## Verification

- `uv run python -m unittest tests.unit.test_recoverable_runtime -v`: passed, 24 tests.
- `uv run ruff check src/simple_long_horizon_agent/service.py tests/unit/test_recoverable_runtime.py scripts/arch_lint.py`: passed.
- `uv run ruff format --check src/simple_long_horizon_agent/service.py tests/unit/test_recoverable_runtime.py scripts/arch_lint.py`: passed.
- `uv run ty check src`: passed.
- `uv run python -m scripts.arch_lint`: passed.
- `uv run python -m scripts.lint_docs`: passed.
- `bash runs/dev/run_ci.sh`: passed; 740 unit tests, 6 skipped.

## Compatibility and Security

The new facade is additive and does not change `Agent`, `RunStore`, or
`RecoverableRunExecutor` behavior. It does not expose worker IPs, retain page
state, or bypass lease/fencing checks.

## Known Limitations

This is a framework-neutral in-process service facade. It does not provide an
HTTP server, authentication, distributed queue, or database transaction
boundary; those remain deployment-owned concerns.

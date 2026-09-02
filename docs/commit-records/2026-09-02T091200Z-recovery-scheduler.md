# Commit Record: Add resident recovery scheduler

- Date: 2026-09-02T09:12:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add resident recovery scheduler`

## Summary

Connect the durable recovery scanner to a stoppable background loop so a
restarted service can automatically discover candidate Runs and hand them to
the existing lease-fenced executor.

## Changed Areas

- `src/simple_long_horizon_agent/recoverable_runtime.py`: add
  `RecoveryScheduler` with one-shot scans, daemon lifecycle, stop handling, and
  per-Run error reporting.
- `src/simple_long_horizon_agent/__init__.py`: expose the scheduler publicly.
- `tests/unit/test_recoverable_runtime.py`: cover candidate processing,
  per-Run error isolation, and background shutdown.
- `tests/unit/test_core.py`: update the public API contract.
- `docs/design/16-recoverable-code-task-runtime.md`: document the scheduler
  boundary, restart behavior, and service shutdown limitations.

## Verification

- `uv run python -m unittest tests.unit.test_recoverable_runtime tests.unit.test_core -v`: passed, 40 tests.
- `uv run python -m scripts.lint_docs`: passed.
- `uv run python -m scripts.arch_lint`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed; 723 unit tests, 6 skipped, formatting,
  lint, type, documentation, architecture, environment, and demo checks.

## Compatibility and Security

The existing Agent loop and executor contracts are unchanged. The scheduler
does not execute tools or bypass lease checks; it only invokes a caller-owned
recovery callback. No new network access or credentials were introduced.

## Known Limitations

This is an in-process scheduler over the filesystem prototype. It has no
persistent queue, distributed wake-up, concurrency quota, or exponential
backoff. Those concerns belong to a future service-level control plane.

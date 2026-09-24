# Commit Record: Add graceful recovery scheduler shutdown

- Date: 2026-09-02T09:50:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add graceful recovery scheduler shutdown`

## Summary

Make service shutdown explicit for the resident recovery scheduler. A stop
request prevents remaining candidates in the current scan from being started,
allows an active recovery callback to finish, and reports whether the scheduler
drained within the caller's timeout.

## Changed Areas

- `src/simple_long_horizon_agent/recoverable_runtime.py`: stop-aware candidate
  iteration and boolean drain result from `RecoveryScheduler.stop()`.
- `tests/unit/test_recoverable_runtime.py`: cover stopping while a callback is
  active and verify that later candidates are not started.
- Runtime design notes (subsequently removed during documentation cleanup):
  document service shutdown
  behavior and timeout semantics.

## Verification

- `uv run python -m unittest tests.unit.test_recoverable_runtime -v`: passed, 16 tests.
- `uv run ty check src`: passed.
- `uv run python -m scripts.arch_lint`: passed.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed; 729 unit tests, 6 skipped, formatting,
  lint, type, documentation, architecture, environment, and demo checks.

## Compatibility and Security

The scheduler remains an opt-in in-process coordinator. Existing callbacks and
executor behavior are unchanged; shutdown only controls new scheduler work and
does not bypass lease fencing or forcefully terminate external tools.

## Known Limitations

The scheduler cannot interrupt a callback that ignores cancellation. A service
must enforce its own process-level deadline and rely on the executor's existing
checkpoint, lease expiry, and takeover behavior after a forced stop.

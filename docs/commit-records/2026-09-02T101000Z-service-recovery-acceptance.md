# Commit Record: Add service-level recovery acceptance tests

- Date: 2026-09-02T10:10:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `test(runtime): cover service recovery lifecycle`

## Summary

Connect the resident `RecoveryScheduler` and `RecoverableRunExecutor` in
service-level acceptance tests. The scenarios model a restarted service taking
over multiple Runs, two service instances racing for the same Runs, and a
graceful shutdown that drains the active callback while leaving unclaimed Runs
for the next service instance.

## Changed Areas

- `tests/unit/test_recoverable_runtime.py`: add restart takeover, concurrent
  scheduler fencing, and shutdown-drain coverage.
- Runtime design notes (subsequently removed during documentation cleanup):
  document the validated
  service lifecycle contract and test locations.

## Verification

- `uv run python -m unittest tests.unit.test_recoverable_runtime -v`: passed, 22 tests.
- `uv run ruff check tests/unit/test_recoverable_runtime.py`: passed.
- `uv run ruff format --check tests/unit/test_recoverable_runtime.py`: passed.
- `bash runs/dev/run_ci.sh`: passed; 738 unit tests, 6 skipped.

## Compatibility and Security

Tests and documentation only; runtime APIs and behavior are unchanged. The
concurrent scenario verifies that lease ownership and fencing remain the only
authority for advancing a Run.

## Known Limitations

The scenarios use in-process threads and filesystem stores. They do not replace
multi-process, cross-host, or shared-database integration tests, and they do not
add scheduler backoff or concurrency quotas.

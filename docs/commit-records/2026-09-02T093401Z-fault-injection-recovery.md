# Commit Record: Add fault-injection recovery acceptance tests

- Date: 2026-09-02T09:34:01Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `test(runtime): verify fault-injection recovery`

## Summary

Add an end-to-end filesystem acceptance test for the Phase 3 recovery boundary.
The test simulates a Worker exiting after an edit reaches the workspace but
before the operation ledger is confirmed, then verifies that a new Worker
reconciles the post-image and continues without repeating the edit. A second
case verifies that a tampered post-image fails closed and blocks the Run.

## Changed Areas

- `tests/unit/test_recoverable_runtime.py`: add recoverable edit interruption
  and failed-reconciliation tests.
- `docs/design/16-recoverable-code-task-runtime.md`: mark Phase 3 fault
  injection acceptance as complete and document the continue/block outcomes.

## Verification

- `uv run python -m unittest tests.unit.test_recoverable_runtime -v`: passed, 19 tests.
- `uv run ruff check tests/unit/test_recoverable_runtime.py`: passed.
- `uv run ruff format --check tests/unit/test_recoverable_runtime.py`: passed.

## Compatibility and Security

Tests and documentation only; no runtime behavior or public API changes.
The scenarios explicitly preserve the fail-closed rule for uncertain side
effects.

## Known Limitations

The acceptance remains a deterministic local filesystem simulation. It does not
replace a multi-process or distributed storage integration test, and it does
not exercise a live SWE-bench or Terminal-Bench service.

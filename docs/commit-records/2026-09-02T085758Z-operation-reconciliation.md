# Commit Record: Reconcile pending side-effect operations

- Date: 2026-09-02T08:57:58Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): reconcile pending side-effect operations`

## Summary

Make recovery fail closed around side-effecting tool calls. A restarted Run
now inspects pending operations, uses caller-provided reconcilers to confirm
observable outcomes, and blocks the Run when the outcome cannot be determined.

## Changed Areas

- `src/simple_long_horizon_agent/run_control.py`: persist operation metadata,
  list pending operations, and support the blocked operation state.
- `src/simple_long_horizon_agent/reconciliation.py`: add reconciliation
  protocol, edit post-image hash verification, and pending-operation handling.
- `src/simple_long_horizon_agent/core.py`: record tool-specific side-effect
  metadata with operation intents.
- `src/simple_long_horizon_agent/recoverable_runtime.py`: reconcile pending
  operations before resuming model execution.
- `src/simple_long_horizon_agent/tools/edit.py`: record expected post-edit hash.
- `tests/unit/test_recoverable_runtime.py`, `tests/unit/test_core.py`: cover
  successful edit recovery and fail-closed behavior.
- `docs/design/16-recoverable-code-task-runtime.md`: document the recovery
  contract and adapter limitation.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed; 721 unit tests, 6 skipped, formatting,
  lint, type, documentation, architecture, environment, and demo checks.

## Compatibility and Security

Existing tools and the basic Agent loop remain compatible. Unknown or
unverifiable side effects are blocked instead of being silently retried,
reducing duplicate-write risk. No credentials or new network permissions were
introduced.

## Known Limitations

Only tools with a registered reliable reconciler can resume after an uncertain
side effect. The filesystem ledger is still a local prototype without a
shared database transaction boundary.

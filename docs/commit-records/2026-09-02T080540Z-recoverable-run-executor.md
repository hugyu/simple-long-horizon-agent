# Commit Record: Add the recoverable Run executor

- Date: 2026-09-02T08:05:40Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add recoverable run executor`

## Summary

Connect the existing filesystem Run, Checkpoint, and Operation Ledger
primitives through a small `RecoverableRunExecutor`. A worker can acquire a
lease, run the existing Agent loop, checkpoint at event boundaries, release a
resumable Run on failure, and let another worker continue from the checkpoint.

## Changed Areas

- `src/simple_long_horizon_agent/recoverable_runtime.py`: add the coordinator
  for create, lease, checkpoint, completion, failure release, and takeover.
- `src/simple_long_horizon_agent/run_control.py`: add lease-protected progress
  updates and terminal-status release semantics.
- `src/simple_long_horizon_agent/__init__.py`, `scripts/arch_lint.py`: expose and
  classify the new core module.
- `tests/unit/test_recoverable_runtime.py`: cover completion, checkpointing,
  failure release, and worker takeover.
- `tests/unit/test_core.py`: update the public API contract.
- Runtime design notes (subsequently removed during documentation cleanup):
  document the implemented
  coordinator and remaining production gaps.

## Verification

- `bash runs/dev/run_ci.sh`: passed, including formatting, lint, docs,
  generated-doc, architecture/environment lint, type checking, 712 unit tests
  with 6 skips, and the bash demo.
- `uv run python -m unittest tests.unit.test_recoverable_runtime tests.unit.test_run_control tests.unit.test_checkpoint tests.unit.test_core -v`: passed, 40 tests.
- `git diff --check`: passed.

## Compatibility and Security

The existing `Agent` and `core.run()` interfaces remain unchanged. The
coordinator is opt-in and uses the existing `State.data` extension point for
run metadata. It does not expand shell permissions or claim a security
sandbox; workspace and backend isolation remain separate controls.

## Known Limitations

This is a local filesystem coordinator. It has no background recovery scanner,
shared database transaction, independent Event Journal, or general workspace
snapshot/restore lifecycle. A caller must explicitly invoke `execute()` for a
new worker takeover.

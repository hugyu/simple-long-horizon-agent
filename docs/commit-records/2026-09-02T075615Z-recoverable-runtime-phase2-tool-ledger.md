# Commit Record: Connect the operation ledger to side-effecting tools

- Date: 2026-09-02T07:56:15Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): track side-effecting tool operations`

## Summary

Connect the Phase 2 filesystem Operation Ledger to the core tool dispatch path
so recoverable code-task runs can record and gate tool side effects. The
implementation remains intentionally small and does not claim distributed
exactly-once execution.

## Changed Areas

- `src/simple_long_horizon_agent/checkpoint.py`: add durable State checkpoint
  serialization and atomic file persistence.
- `src/simple_long_horizon_agent/run_control.py`: add Run leases, fencing,
  version checks, and idempotent operation records.
- `src/simple_long_horizon_agent/core.py`: record side-effecting tool intent,
  start, confirmation, and unknown outcomes; block unsafe replays.
- `src/simple_long_horizon_agent/tools/__init__.py`: add tool side-effect
  metadata and detectors.
- `src/simple_long_horizon_agent/tools/edit.py`: mark edits as side-effecting.
- `src/simple_long_horizon_agent/tools/bash.py`: conservatively detect common
  shell writes and destructive Git commands.
- `tests/unit/test_checkpoint.py`, `tests/unit/test_run_control.py`,
  `tests/unit/test_core.py`, `tests/unit/test_bash_agent.py`: cover durability,
  fencing, lifecycle, replay blocking, and command classification.
- `docs/design/16-recoverable-code-task-runtime.md`: document the implemented
  Phase 2 boundary and its reconciliation limitations.

## Verification

- `uv run python -m unittest discover -s tests/unit`: passed, 710 tests, 6 skipped.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed, including formatting, lint, generated docs,
  architecture/environment lint, type checking, unit tests, and the bash demo.

## Compatibility and Security

Existing tools without side-effect metadata retain their prior behavior. Ledger
activation is opt-in through `State.data["run_id"]` and
`State.data["operation_ledger"]`. Operation records persist argument digests,
not raw tool arguments. Bash detection is a policy hint, not a security
sandbox; backend isolation and command policy remain separate concerns.

## Known Limitations

The filesystem ledger is a local prototype. `unknown` operations require an
upper layer to inspect the workspace or external system and explicitly mark
them `reconciled`; cross-process exactly-once execution and general workspace
recovery are future work.

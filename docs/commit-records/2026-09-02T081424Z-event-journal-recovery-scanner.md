# Commit Record: Add event journal and recovery scanning

- Date: 2026-09-02T08:14:24Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add event journal and recovery scanner`

## Summary

Add the next recovery layer for the code-task scenario: an independent
append-only Event Journal and a scanner that discovers runnable, reconciling,
waiting, and expired-lease Runs after a process restart. The existing
RecoverableRunExecutor now writes events to the journal before yielding them.

## Changed Areas

- `src/simple_long_horizon_agent/event_journal.py`: add fsynced JSONL journal
  with contiguous indexes and idempotent duplicate appends.
- `src/simple_long_horizon_agent/run_control.py`: add Run listing for recovery
  scans.
- `src/simple_long_horizon_agent/recoverable_runtime.py`: integrate journaling
  and add `RecoveryScanner` with an injectable clock.
- `src/simple_long_horizon_agent/__init__.py`, `scripts/arch_lint.py`,
  `tests/unit/test_core.py`: expose and classify the new APIs.
- `tests/unit/test_recoverable_runtime.py`: cover journal durability,
  contiguous append rules, scanner filtering, and executor journal output.
- `docs/design/16-recoverable-code-task-runtime.md`: sync implemented recovery
  behavior and remaining production gaps.

## Verification

- `bash runs/dev/run_ci.sh`: passed, including formatting, lint, docs,
  generated-doc, architecture/environment lint, type checking, 715 unit tests
  with 6 skips, and the bash demo.
- `uv run python -m unittest discover -s tests/unit`: passed, 715 tests, 6 skips.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.

## Compatibility and Security

The existing Agent loop remains unchanged. Journaling and recovery scanning
are opt-in through the recoverable executor and filesystem stores. Journal
records use the existing typed event serialization and do not grant new tool
permissions or expose workspace paths beyond caller-provided metadata.

## Known Limitations

The scanner is a synchronous discovery helper, not a resident scheduler. The
filesystem JSONL journal has no multi-process transaction with Checkpoint or
Run updates; a future shared backend must preserve the same contiguous event
and lease-fencing invariants.

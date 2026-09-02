# Commit Record: Merge checkpoint and journal state on recovery

- Date: 2026-09-02T08:18:51Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): merge checkpoint and event journal on recovery`

## Summary

Make restart recovery deterministic by validating the Event Journal prefix
covered by a Checkpoint and replaying only journal events beyond that prefix.
The recoverable executor now writes initial State events to the journal and
uses the merge path before continuing an acquired Run.

## Changed Areas

- `src/simple_long_horizon_agent/event_journal.py`: add checkpoint/journal
  merge validation and tail replay.
- `src/simple_long_horizon_agent/recoverable_runtime.py`: use merged state on
  recovery and journal initial events when creating a Run.
- `src/simple_long_horizon_agent/__init__.py`, `tests/unit/test_core.py`: expose
  the merge helper and update the public API contract.
- `tests/unit/test_recoverable_runtime.py`: cover tail replay and prefix
  conflict rejection.
- `docs/design/16-recoverable-code-task-runtime.md`: document the recovery
  merge order and consistency rule.

## Verification

- `bash runs/dev/run_ci.sh`: passed, including formatting, lint, docs,
  generated-doc, architecture/environment lint, type checking, 717 unit tests
  with 6 skips, and the bash demo.
- `uv run python -m unittest tests.unit.test_recoverable_runtime tests.unit.test_core tests.unit.test_checkpoint tests.unit.test_run_control -v`: passed, 45 tests.
- `git diff --check`: passed.

## Compatibility and Security

The existing Agent loop and checkpoint schema remain unchanged. Recovery only
adds events beyond the validated checkpoint prefix; conflicting or incomplete
journal data fails closed rather than guessing. No new tool permissions or
workspace access are introduced.

## Known Limitations

Journal, Checkpoint, and Run progress updates are still separate filesystem
operations. A shared production backend will need a transaction or durable
commit protocol across those records.

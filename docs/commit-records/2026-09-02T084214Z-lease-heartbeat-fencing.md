# Commit Record: Add lease heartbeat and stale-worker fencing

- Date: 2026-09-02T08:42:14Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add lease heartbeat and stale-worker fencing`

## Summary

Protect long-running recoverable Runs from lease expiry while a model or tool
is still executing. The executor now renews its lease in a daemon heartbeat,
stops fail-closed when renewal or fencing fails, and avoids releasing a lease
that may already belong to a replacement Worker.

## Changed Areas

- `src/simple_long_horizon_agent/recoverable_runtime.py`: add lease heartbeat,
  `LeaseLostError`, combined abort signaling, and stale-worker handling.
- `src/simple_long_horizon_agent/__init__.py`: expose `LeaseLostError`.
- `tests/unit/test_recoverable_runtime.py`: cover long-run renewal and takeover
  fencing under a lease race.
- `tests/unit/test_core.py`: update the public API contract.
- `docs/design/16-recoverable-code-task-runtime.md`: document heartbeat timing,
  fail-closed behavior, and external-process cancellation limits.

## Verification

- `bash runs/dev/run_ci.sh`: passed, including formatting, lint, docs,
  generated-doc, architecture/environment lint, type checking, 719 unit tests
  with 6 skips, and the bash demo.
- `uv run python -m unittest tests.unit.test_recoverable_runtime -v`: passed, 9 tests.
- `uv run ty check src`: passed.
- `git diff --check`: passed.

## Compatibility and Security

The existing Agent loop remains unchanged. Lease heartbeat is enabled only by
the recoverable executor and uses the existing conditional RunStore updates.
Losing a lease prevents further control-plane writes; it does not pretend to
cancel an already-running external process, which remains a tool/backend
responsibility.

## Known Limitations

The heartbeat is an in-process daemon thread and the RunStore remains a local
filesystem prototype. A production implementation still needs shared durable
storage, service-level shutdown coordination, and explicit cancellation or
reconciliation for external processes.

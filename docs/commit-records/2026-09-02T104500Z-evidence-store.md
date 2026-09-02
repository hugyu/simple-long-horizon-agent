# Commit Record: Persist recoverable Run evidence

- Date: 2026-09-02T10:45:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): persist recoverable run evidence`

## Summary

Persist the evidence projection produced by long-running Runs. The executor
now writes an atomic JSON evidence pack when a Run completes, is blocked during
recovery, or releases after a worker failure. A restarted service can read the
latest summary without reconstructing the entire in-memory execution.

## Changed Areas

- `src/simple_long_horizon_agent/evidence.py`: add `EvidenceStore` and
  `FileEvidenceStore` with safe Run-ID validation, atomic replacement, and
  round-trip decoding.
- `src/simple_long_horizon_agent/recoverable_runtime.py`: accept an optional
  evidence store and persist summaries at completion, blocking, and failure
  boundaries.
- `src/simple_long_horizon_agent/__init__.py`, `tests/unit/test_core.py`: expose
  the evidence-store API and update the public surface contract.
- `tests/unit/test_evidence.py`, `tests/unit/test_recoverable_runtime.py`:
  cover durable storage and executor completion output.
- `docs/design/16-recoverable-code-task-runtime.md`: document durable evidence
  behavior and its remaining limitations.

## Verification

- `uv run python -m unittest tests.unit.test_evidence tests.unit.test_recoverable_runtime tests.unit.test_core -v`: passed, 47 tests.
- `uv run ty check src`: passed.
- `uv run python -m scripts.arch_lint`: passed.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed; 733 unit tests, 6 skipped, formatting,
  lint, type, documentation, architecture, environment, and demo checks.

## Compatibility and Security

Evidence persistence is opt-in and does not change the Agent Loop or recovery
decision rules. Files are written below a validated Run-ID path using atomic
replacement; raw task text and Skill bodies are not copied into the summary.

## Known Limitations

The filesystem evidence store is not part of a shared transaction with the Run
record, Event Journal, or Checkpoint. A production backend must preserve the
same lifecycle boundaries and decide how to retain or garbage-collect packs.

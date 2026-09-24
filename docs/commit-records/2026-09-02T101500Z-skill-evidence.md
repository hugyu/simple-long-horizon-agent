# Commit Record: Add Skill observation and evidence packs

- Date: 2026-09-02T10:15:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add skill observation and evidence packs`

## Summary

Make Skill usage and long-task evidence explicit runtime facts. Skill injection
now records a content fingerprint, task-input digest, source scope, and trigger
as a replayable event. A pure evidence-pack projection summarizes those facts
with tool outcomes, verification metadata, workspace identity, and stop reason.

## Changed Areas

- `src/simple_long_horizon_agent/protocols.py`: add `SkillInvokedEvent` and its
  event-kind discriminator.
- `src/simple_long_horizon_agent/checkpoint.py`: deserialize Skill events and
  fix covered-event validation so event index `0` is not treated as missing.
- `src/simple_long_horizon_agent/skills/discovery.py`: expose a SHA-256 content
  fingerprint for Skill versions.
- `src/simple_long_horizon_agent/skills/runtime.py`: record Skill invocation
  facts when a mention or preload injects a Skill body.
- `src/simple_long_horizon_agent/evidence.py`: add the JSON-shaped
  `EvidencePack` projection and serializer.
- `src/simple_long_horizon_agent/__init__.py`, `scripts/arch_lint.py`: expose
  and classify the new core APIs.
- `tests/unit/test_skills.py`, `tests/unit/test_evidence.py`,
  `tests/unit/test_core.py`: cover Skill event recording, evidence summaries,
  public API, and Checkpoint round trips.
- Runtime design notes (subsequently removed during documentation cleanup):
  mark the Phase 3 boundary
  and its non-invasive evidence semantics.

## Verification

- `uv run python -m unittest tests.unit.test_evidence tests.unit.test_skills tests.unit.test_checkpoint tests.unit.test_core -v`: passed, 70 tests.
- `uv run ty check src`: passed.
- `uv run python -m scripts.arch_lint`: passed.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed; 731 unit tests, 6 skipped, formatting,
  lint, type, documentation, architecture, environment, and demo checks.

## Compatibility and Security

Skill events and evidence are observe-only. Raw Skill bodies and task text are
not duplicated into the evidence projection; only content/input digests are
stored. Existing Skill loading and Agent Loop behavior remain unchanged.

## Known Limitations

Evidence packs are currently derived in memory and are not automatically
written as a separate artifact. Verification details remain caller-owned
metadata, and external Skill/MCP side effects still use the existing operation
ledger and reconciliation contracts.

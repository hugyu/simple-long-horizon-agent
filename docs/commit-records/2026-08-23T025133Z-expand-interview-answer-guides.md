# Commit Record: Expand structured interview answer guides

- Date: 2026-08-23T02:51:33Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): expand structured answer guides`

## Summary

Complete and deepen the interview preparation documents while keeping every
answer grounded in the current code, tests, design documents, and published
results. The guides now separate first-round spoken answers, checklist
follow-up questions, and implementation evidence.

## Changed Areas

- `docs/interview/answer-guidelines.md`: define the three-layer preparation
  format and require checklist follow-up questions to remain explicit.
- `docs/interview/01-answers-agent-runtime.md` through
  `docs/interview/07-answers-evaluation-experiments.md`: complete missing
  questions, preserve listed follow-ups, and clarify implementation and
  evidence boundaries.
- `docs/interview/08-answers-production-agent-platform.md`: expand production
  platform, durability, scheduling, recovery, fencing, and idempotency answers
  without claiming those designs are implemented.
- Original security answer guide: expand prompt injection, tool capability,
  delegation, MCP, sandbox, secret, and trace security answers with explicit
  current-project limitations.

## Verification

- `uv run python -m scripts.lint_docs`: passed (`Docs lint passed.`).
- `git diff --check`: passed with no whitespace errors.
- Reviewed changed file types and diff size: documentation text only; no binary
  or unexpectedly large generated files.
- Reviewed credential-related diff matches: terminology and examples only; no
  credential files or actual secret values found.

## Compatibility and Security

Documentation-only change. No runtime, API, configuration, dependency, or data
format behavior changes. Security answers explicitly distinguish available
extension points from policy engines, hardened sandboxes, secret managers, and
other production controls that the repository does not implement.

## Known Limitations

The production platform and security sections describe proposed engineering
designs where the repository has no corresponding production implementation.
The answers state those boundaries directly.

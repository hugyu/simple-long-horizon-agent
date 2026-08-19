# Commit Record: Refresh resume guidance and documentation workflow

- Date: 2026-08-19T13:49:19Z
- Branch: `项目梳理`
- Intended commit: `docs: refresh resume and documentation workflow`

## Summary

Rewrite the resume project description around verifiable Agent engineering
work, remove the remaining interview question bank, and remove historical
records dedicated to the superseded interview preparation documents. Define a
focused verification rule for documentation-only changes so they do not run
the full repository quality gate.

## Changed Areas

- `docs/design/15-resume-project-description.md`: focus the resume wording on
  Agent Runtime, context engineering, tool use, orchestration, observability,
  evaluation, role targeting, and defensible benchmark claims.
- Remaining Agent interview question bank: remove the interview question
  document.
- Earlier interview-documentation commit record: remove the historical record
  dedicated to the old interview answer guides.
- Structured interview-guide commit record: remove the historical record
  dedicated to the structured interview guide.
- `AGENTS.md`, `CONTRIBUTING.md`, and `docs/development.md`: require docs lint
  and diff checking for documentation-only changes, reserving the full quality
  gate for source, configuration, test, runnable example, dependency, generated
  input, or CI changes.

## Verification

- `bash runs/dev/run_ci.sh`: passed; formatting, lint, docs lint, generated
  documentation, architecture lint, environment lint, type checking, 696 unit
  tests with 6 skips, and the deterministic bash-agent demo completed
  successfully before the documentation-only fast-path rule was added.
- `uv run python -m scripts.lint_docs`: passed after all documentation edits.
- `find docs/interview -type f -print`: returned no remaining interview
  question or answer documents.
- `git diff --check`: passed.

## Compatibility and Security

Documentation-only changes. No runtime behavior, public API, dependency,
configuration, or security boundary changed. No credentials, private keys,
environment files, generated caches, or binary artifacts are included.

## Known Limitations

The resume benchmark values remain the published README results; this change
does not add new experiment artifacts or component ablations.

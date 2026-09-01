# Commit Record: Normalize interview answer headings

- Date: 2026-09-01T04:51:21Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): normalize answer headings`

## Summary

Align answer headings with the question checklist and add the missing spoken-answer markers so interview material can be indexed deterministically.

## Changed Areas

- `docs/interview/01-answers-agent-runtime.md`: add spoken-answer headings and align selected question titles.
- `docs/interview/02-answers-model-adapters.md`: align question 19.
- `docs/interview/03-answers-context-engineering.md`: align questions 24 and 25.
- `docs/interview/05-answers-workflows-completion.md`: align questions 59, 61, and 63.
- `docs/interview/08-answers-production-agent-platform.md`: align question 92.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.

## Compatibility and Security

Only Markdown headings changed; answer content and runtime behavior are unchanged. No credentials were added.

## Known Limitations

Questions 100 through 106 still have no source answer and remain excluded from the generated interview index.

# Commit Record: Restructure interview preparation

- Date: 2026-08-20T15:16:20Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): restructure interview preparation`

## Summary

Move the interview preparation material under `docs/interview/`, replace the
previous topic guides with a question-led preparation path, and standardize
every recorded answer into a concise oral response plus bounded technical
follow-up notes.

## Changed Areas

- `docs/interview/answer-guidelines.md`: define candidate, coaching, and
  preparation-document modes with explicit oral/follow-up structure.
- `docs/interview/question-checklist.md`: organize the current interview
  question set across runtime, adapters, context, tools, workflows,
  observability, evaluation, production design, and security.
- `docs/interview/01-answers-agent-runtime.md` through the original security
  answer guide: add code-grounded oral answers and concise technical follow-up
  material.
- `AGENTS.md`, `README.md`, `docs/README.md`, and `docs/design/README.md`: update
  documentation paths and remove stale interview-index references.
- `interview/`: remove the superseded root-level interview documentation.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- Secret-pattern scan over the intended interview and index files: no
  credential-like matches.

## Compatibility and Security

This is a documentation-only change. Existing root-level interview paths move
to `docs/interview/`; repository links and agent instructions are updated in
the same commit. Security answers explicitly distinguish current safeguards
from production controls and do not add credentials or private artifacts.

## Known Limitations

- Published benchmark answers retain the repository's existing evidence
  boundaries: complete per-task artifacts, repeated-run statistics, and
  component ablations are not present.
- The commit-context helper required by the local commit skill is not present
  in this repository, so the equivalent branch, status, diff, history, and
  secret checks were performed directly with Git and `rg`.

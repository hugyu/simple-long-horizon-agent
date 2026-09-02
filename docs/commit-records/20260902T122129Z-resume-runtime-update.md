# Commit Record: Update resume with recoverable runtime work

- Date: 2026-09-02T12:21:29Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `docs(resume): add recoverable runtime experience`

## Summary

Update the project resume description to explain the implemented application
flows instead of listing isolated Agent concepts. The new five-item version
covers tool-selection conditions, context and Skill loading, task delegation,
restart recovery, side-effect reconciliation, observation, and evaluation.

## Changed Areas

- `docs/design/15-resume-project-description.md`: replace the full and concise
  descriptions with the approved scenario-oriented five-item versions.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.

## Compatibility and Security

Documentation-only change. The resume wording stays within behavior supported
by current source, tests, design documents, and published README results.

## Known Limitations

The benchmark values are described as project-published results because the
current checkout does not include every original per-instance result artifact.

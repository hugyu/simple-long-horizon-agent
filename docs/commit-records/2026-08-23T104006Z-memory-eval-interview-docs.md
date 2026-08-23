# Commit Record: Refine memory and eval interview docs

- Date: 2026-08-23T10:40:06Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): refine memory and eval answers`

## Summary

Update interview preparation notes so Memory and evaluation answers match the
current repository behavior, and keep benchmark result descriptions precise
about absolute percentage-point changes versus relative improvement.

## Changed Areas

- `docs/design/15-resume-project-description.md`: add Skills progressive
  loading to the resume description and limit the listed benchmark claims to
  SWE-bench Pro and Terminal-Bench 2.1.
- `docs/interview/03-answers-context-engineering.md`: add a Memory
  implementation answer covering namespace layout, lifecycle, locking,
  distillation, evidence retention, and limits.
- `docs/interview/07-answers-evaluation-experiments.md`: replace the broad eval
  framework answer set with a focused Terminal-Bench 2.1, SWE-bench Pro, result
  verification, and baseline discussion.
- `docs/interview/question-checklist.md`: add Memory and Skills follow-up
  questions, and narrow the eval section to the four answered questions.
- `docs/commit-records/2026-08-23T104006Z-memory-eval-interview-docs.md`:
  record the commit scope and validation evidence.

## Verification

- Commit-context helper command: failed because the requested helper script is
  not present in this checkout.
- `git status --short --branch`: showed four modified documentation files on
  `项目梳理...origin/项目梳理` before this commit record was added.
- `git diff --stat`: showed 184 insertions and 540 deletions across the four
  documentation files before this commit record was added.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- Scoped credential-pattern scan over the changed documentation files: no
  credential-like values found; matches were ordinary `Token`/`tokens`
  documentation terms.

## Compatibility and Security

Documentation-only change. No Runtime behavior, public API, dependencies,
configuration, or generated build output changed. The scoped scan found no
private keys, API keys, passwords, or token-shaped secrets in the changed
documentation.

## Known Limitations

The repository does not currently include the requested commit-context helper,
so commit context was collected with `git status`, `git diff --stat`, full diff
inspection, branch/upstream checks, and recent Git history instead.

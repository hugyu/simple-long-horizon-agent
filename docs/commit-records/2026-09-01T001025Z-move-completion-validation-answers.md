# Commit Record: Move completion validation answers

- Date: 2026-09-01T00:10:25Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): move completion validation answers`

## Summary

Move completion and SWE validation interview answers into the workflow and
completion guide, keeping the context engineering guide focused on context,
recall, and memory concerns.

## Changed Areas

- `docs/interview/03-answers-context-engineering.md`: remove completion,
  validation-signal, recall-trigger, and filesystem memory sections that do not
  belong in the context-engineering answer flow.
- `docs/interview/05-answers-workflows-completion.md`: add the task-completion
  and SWE validation-signal answers under the workflow/completion section.

## Verification

- `uv run python -m scripts.collect_commit_context --repo /Users/hgy/Desktop/simple-long-horizon-agent`:
  failed because this checkout does not include `scripts.collect_commit_context`.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.

## Compatibility and Security

Documentation-only change. No runtime behavior, dependencies, generated output,
or configuration changed.

## Known Limitations

None for this commit.

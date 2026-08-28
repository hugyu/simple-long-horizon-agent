# Commit Record: Refine interview docs and guidance

- Date: 2026-08-28T01:23:55Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): refine interview docs and guidance`

## Summary

Tighten the interview answer guides so extension questions lead with a concrete
design path from the current codebase, refresh the tools/MCP and workflow
answers, and align the resume project description with the current documented
scope.

## Changed Areas

- `docs/design/15-resume-project-description.md`: trim the resume bullets to
  the current documented MCP, workflow, trace, and benchmark claims.
- `docs/interview/04-answers-tools-integrations.md`: expand MCP connection,
  failure, and authorization answers with explicit lifecycle and layered
  security examples.
- `docs/interview/05-answers-workflows-completion.md`: refine budget, routing,
  reflection, planner/executor, and external-verification explanations.
- `docs/interview/answer-guidelines.md`: clarify how expansion questions should
  start from the existing implementation and separate design proposals from
  current project facts.
- `docs/interview/question-checklist.md` and older commit records: remove
  stale local path references after the security answer guide was deleted.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- Commit-context helper: unavailable in this checkout, so context was collected
  with `git status`, `git diff`, branch/upstream checks, and recent history.

## Compatibility and Security

Documentation-only change. No runtime behavior, dependencies, or configuration
changed. Stale links to the deleted security answer guide were removed so local
documentation checks stay valid.

## Known Limitations

None for this commit.

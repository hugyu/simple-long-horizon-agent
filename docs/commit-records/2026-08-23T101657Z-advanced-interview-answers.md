# Commit Record: Strengthen advanced interview answers

- Date: 2026-08-23T10:16:57Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): strengthen advanced interview answers`

## Summary

Revise advanced interview preparation answers so they lead with the current
project behavior, continue into architecture-grounded extension designs, and
keep implementation facts separate from proposed production mechanisms.

## Changed Areas

- `docs/interview/04-answers-tools-integrations.md`: deepen timeout, resource
  conflict, MCP discovery, failure, and permission answers.
- `docs/interview/05-answers-workflows-completion.md`: strengthen delegation,
  workspace integration, budgeting, planning, reflection, and completion
  answers; add a workflow overview and selection guide.
- `docs/interview/06-answers-observability-cost.md`: clarify trace schema,
  parent-child tracing, failure diagnosis, token accounting, and auditable
  cost aggregation.
- `docs/interview/08-answers-production-agent-platform.md`: refine durable
  execution, scheduling, tool separation, fault tolerance, checkpointing,
  concurrent recovery, and external-side-effect designs.
- Original security answer guide: strengthen prompt injection, capability,
  delegation, MCP trust, sandbox, credential, and trace-data security answers.
- `docs/interview/question-checklist.md`: add the workflow overview question
  without renumbering the existing global checklist.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- Secret-pattern scan over the scoped documentation diff: no matches.

## Compatibility and Security

Documentation-only change. No Runtime behavior, public API, dependency, or
configuration changes. Security sections explicitly distinguish current
project boundaries from proposed production controls.

## Known Limitations

The commit excludes unrelated existing edits in
`docs/design/15-resume-project-description.md`,
`docs/interview/03-answers-context-engineering.md`,
`docs/interview/07-answers-evaluation-experiments.md`, and the unrelated
question-checklist hunks. Several answers intentionally describe extension
designs that are not implemented in the current Runtime.

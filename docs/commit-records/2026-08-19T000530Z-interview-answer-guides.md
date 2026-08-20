# Commit Record: Add the first four interview answer guides

- Date: 2026-08-19T00:05:30Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): add first four topic answer guides`

## Summary

Add a code-grounded interview preparation path for the first four topics in the
Agent interview checklist. The answers use natural Chinese for the reasoning
while retaining common English terms and real code identifiers where they add
precision.

## Changed Areas

- `AGENTS.md`: define candidate and interview-coach modes for Agent interview
  practice.
- `docs/design/15-resume-project-description.md`: reduce the resume description
  to focused full and concise versions used by the question checklist.
- `docs/design/16-interview-answer-guidelines.md`: define evidence priority,
  answer structure, factual boundaries, oral style, and balanced Chinese-English
  terminology.
- `docs/design/17-interview-question-checklist.md`: organize likely interview
  questions and link the first four topics to their answer guides.
- `docs/design/18-interview-answers-agent-runtime.md`: answer Runtime, core data,
  stop, resume, and minimal-loop questions.
- `docs/design/19-interview-answers-provider-adapters.md`: answer provider
  protocol, Adapter, streaming, retry, raw response, and test questions.
- `docs/design/20-interview-answers-context-engineering.md`: answer context
  layering, compression, Recall, evidence recovery, and experiment questions.
- `docs/design/21-interview-answers-parallel-tools.md`: answer parallel
  scheduling, timeout, cancellation, conflict, retry, and idempotency questions.
- `docs/design/README.md`: index the checklist and interview answer guides.

## Verification

- `uv run python -m scripts.lint_docs`: passed (`Docs lint passed.`).
- `git diff --check`: passed with no output.
- Secret-pattern scan across the changed interview documentation: no matches.

## Compatibility and Security

Documentation-only change. No runtime API, configuration, dependency, or
evaluation behavior changes. No credentials, private keys, tokens, or generated
binaries were added.

## Known Limitations

- Only the first four topics in the interview checklist have dedicated answer
  guides.
- The Context Engineering guide explicitly records that Recall can retrieve
  messages by index, but the built-in summaries do not yet guarantee that those
  indices are visible to the model for autonomous recovery.
- The repository branch already contains one local commit ahead of its upstream;
  a normal push will publish that existing commit together with this commit.

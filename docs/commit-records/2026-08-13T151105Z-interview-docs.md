# Commit Record: refine agent interview documentation

- Date: 2026-08-13T15:11:05Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): refine agent interview preparation`

## Summary

Refine the interview question map and split the long answer material into focused
project-positioning and Agent Runtime answer guides. Update the design index so
the new documents and their scope are discoverable.

## Changed Areas

- `docs/design/14-interview-question-map.md`: filter questions by realistic
  interview priority and add P0/P1/P2 guidance.
- `docs/design/16-interview-questions-and-answers.md`: remove the superseded
  monolithic answer guide.
- `docs/design/16-interview-answers.md`: add project-positioning and ownership
  answers.
- `docs/design/17-interview-runtime-answers.md`: add Runtime control-flow,
  context, tool, stopping, laziness, and resume answers.
- `docs/design/README.md`: update the design index for the new documents.

## Verification

- `bash runs/dev/run_ci.sh`: passed.
- `uv run python -m scripts.lint_docs`: passed as part of the full gate.
- Unit tests: 696 passed, 6 skipped.
- Bash fake-provider demo: passed.

## Compatibility and Security

Documentation-only changes. No runtime behavior, dependencies, or security
boundaries were changed. No credentials or private configuration were added.

## Known Limitations

Personal ownership, team structure, and historical development order remain
candidate-specific facts and are explicitly marked for replacement where needed.

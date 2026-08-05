# Commit Record: Add interview preparation guides

- Date: 2026-08-05T14:14:53Z
- Branch: `项目梳理`
- Intended commit: `docs: add interview preparation guides`

## Summary

Add evidence-bounded interview preparation material for explaining the project
without assuming access to its source code. The guides distinguish published
project facts from missing evidence and simulated answers.

## Changed Areas

- `docs/design/13-interview-deep-dive.md`: documents quantitative results,
  performance, security, recovery, design evolution, and personal contribution
  boundaries with coherent simulated answers.
- `docs/design/14-interview-questions-and-answers.md`: provides 50 project-level
  interview questions and simulated answers for an interviewer without code
  access.
- `docs/design/README.md`: indexes both interview guides and adds an interview
  reading route.
- `docs/design/12-system-scenarios-and-completeness.md`: links both guides from
  the full-document reading exit.

## Verification

- `bash runs/dev/run_ci.sh`: passed; formatting, lint, generated docs,
  architecture/environment lint, type checking, 696 unit tests with 6 skipped,
  and the deterministic Bash Agent demo all completed successfully.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- Credential-pattern scan of the changed design documents: no credential
  assignments or private-key headers found.

## Compatibility and Security

Documentation-only change. No runtime behavior, public API, dependency, or
configuration compatibility impact. The guides explicitly avoid presenting the
runtime as a production security sandbox and contain no credentials.

## Known Limitations

Component-level ablations, capacity measurements, a complete threat model,
process-level crash consistency, verifiable architecture history, and personal
ownership evidence remain unavailable. The guides label these gaps and mark
personal-contribution answers as simulations that require candidate review.

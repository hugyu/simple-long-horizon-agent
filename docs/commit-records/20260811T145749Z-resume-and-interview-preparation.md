# Commit Record: Refine resume and interview preparation

- Date: 2026-08-11T14:57:49Z
- Branch: `项目梳理`
- Intended commit: `docs: refine resume and interview preparation`

## Summary

Add an evidence-bounded resume description and rebuild the interview guide as
87 progressively deeper questions whose answers remain consistent with the
current design, code, tests, and published benchmark claims.

## Changed Areas

- `docs/design/15-resume-project-description.md`: adds full, one-page, and
  minimal resume descriptions, a spoken introduction, and benchmark math.
- `docs/design/14-interview-questions-and-answers.md`: replaces the flat
  question list with resume-driven answers covering runtime, providers,
  context, tools, multi-agent workflows, trace/eval, experiments, and pressure
  questions, plus an evidence index.
- `docs/design/README.md`: indexes the resume guide and updates the interview
  preparation reading route.

## Verification

- `bash runs/dev/run_ci.sh`: passed; formatting, Ruff, documentation and
  generated-doc checks, architecture/environment lint, type checking, 696 unit
  tests with 6 skipped, and the deterministic Bash Agent demo completed.
- `uv run python -m unittest tests.unit.test_core tests.unit.test_llm_retry tests.unit.test_model_metadata tests.unit.test_compression_control tests.unit.test_compression_effectiveness tests.unit.test_evals_framework tests.unit.test_swebench_pro_chain_runner -v`:
  passed; 151 focused tests completed.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- Credential-pattern scan of the changed design documents: no credential
  assignments found.

## Compatibility and Security

Documentation-only change. No runtime behavior, public API, dependencies, or
configuration changed. The interview answers preserve the current security and
recovery boundaries instead of presenting the runtime as a production sandbox.

## Known Limitations

The repository does not publish instance-level success/failure artifacts,
component ablations, repeated-run confidence intervals, or a complete immutable
manifest for every published score. The guide marks these gaps explicitly and
does not invent causal attribution or production guarantees.

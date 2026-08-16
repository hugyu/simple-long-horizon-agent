# Commit Record: Add structured agent interview guide

- Date: 2026-08-16T14:01:38Z
- Branch: `项目梳理`
- Intended commit: `docs(interview): add structured interview preparation`

## Summary

Move interview preparation out of the architecture design guide, organize the
material into a question bank and topic-specific answer files, and add a
complete Agent fundamentals and Skills study guide.

## Changed Areas

- `docs/interview/agent-interview-question-bank.md`: add the resume-derived
  interview question bank, system-design prompts, preparation risks, and Agent
  fundamentals questions.
- `docs/interview/sections/`: add topic-specific interview answer documents,
  including 162 Agent fundamentals and Skills questions with matching answers.
- Historical files `14-interview-question-map.md`,
  `16-interview-answers.md`, and `17-interview-runtime-answers.md`: remove
  superseded interview material from the architecture design directory.
- `docs/design/README.md`: remove links and inventory entries for the
  superseded design documents.
- `docs/commit-records/2026-08-13T151105Z-interview-docs.md`: clarify that the
  earlier interview documents are now historical and have been removed.

## Verification

- `uv run bash runs/dev/run_ci.sh`: passed; formatting, lint, docs lint,
  generated docs, architecture lint, environment lint, type checking, 696 unit
  tests with 6 skips, and the deterministic bash-agent demo all completed
  successfully.
- `uv run python -c '<compare section 13 source questions with answer headings>'`:
  passed; all 162 questions match exactly and all 162 answers are present.
- `uv run git diff --check`: passed.
- `uv run python -c '<scan docs/interview Markdown for credential patterns>'`:
  passed for all 14 Markdown files.

## Compatibility and Security

Documentation-only change. No runtime behavior, public API, dependency, or
configuration changes. No credential patterns or unexpectedly large binary
files were found in the interview documents.

## Known Limitations

The Trace and observability and preparation-risks section files remain empty
placeholders for future answers.

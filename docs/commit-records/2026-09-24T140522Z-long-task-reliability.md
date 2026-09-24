# Commit Record: reliable long-task execution

- Date: 2026-09-24T14:05:22.777745+00:00
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): strengthen long-task recovery and context management`

## Summary

Provide a unified verified long-task entry with durable budgets, SQLite state,
recoverable tool operations and bounded model context. Include the requested
cleanup of obsolete learning, interview and design documentation.

## Changed Areas

- Runtime/state: SQLite history, durable budgets, lease renewal during recovery,
  explicit completion verification, and service error accounting.
- Tools/context: paged recall, input byte allowance, excerpts retaining evidence,
  edit pre/post-image recovery, and caller-approved shell retries preserving failures.
- Evaluation/examples: recovery fixtures, context ablation and evidence exports.
- Documentation: runtime contract, evidence requirements and obsolete guide removal.

## Verification

- `uv run bash runs/dev/run_ci.sh`: passed; 807 tests, 5 skipped; formatting,
  lint, documentation, architecture, environment and type checks passed.
- `uv run git diff --cached --check`: passed.
- Staged secret-pattern and sensitive-filename scan: no matches found.
- Earlier local SymPy SWE-bench Verified case: baseline 1 failed/3 passed;
  repaired 4 passed. This predates the latest runtime changes and is not their
  regression test or official Docker benchmark scoring.

## Compatibility and Security

The input limit is named `--max-input-bytes`; the old `--max-input-tokens`
spelling remains a byte-budget alias. Legacy manifests retain compatibility.
Unknown shell outcomes remain blocked unless the caller authorizes an exact
repeat-safe command. Recovery metadata contains edit text and is workspace data.
Ignored evaluation outputs and model credentials are excluded from the commit.

## Known Limitations

File writes are not yet atomic. Final trace export still materializes history.
Python tools require cooperative cancellation. History search and large-scale
real-task validation remain future work; input sizing is not exact tokenization.

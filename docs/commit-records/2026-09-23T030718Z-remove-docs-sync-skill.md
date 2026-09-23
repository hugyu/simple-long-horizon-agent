# Commit Record: Remove docs-sync skill

- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `chore(skills): remove project docs-sync skill`

## Summary

Remove the project-specific documentation synchronization skill at the user's request.

## Changed Areas

- Project docs-sync skill definition: deleted.

## Verification

- `uv run python -m scripts.lint_docs`: passed.
- `uv run git diff --check`: passed.
- Verified the skill file and empty containing directory are absent.
- Full CI is not required for this documentation-only commit.

## Compatibility and Security

Removes automatic discovery of this project skill. No runtime behavior changes or credentials added.

## Known Limitations

Other ongoing workspace changes are excluded from this commit. Historical references are preserved.

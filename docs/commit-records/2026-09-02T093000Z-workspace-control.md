# Commit Record: Add recoverable workspace control

- Date: 2026-09-02T09:30:00Z
- Branch: `codex/recoverable-runtime-phase1`
- Intended commit: `feat(runtime): add recoverable workspace control`

## Summary

Bring isolated filesystem workspaces into the recoverable Run control plane.
Runs now persist a stable workspace reference, recreate or copy a baseline at
creation, resolve the same directory during recovery, and fail closed when the
workspace has disappeared.

## Changed Areas

- `src/simple_long_horizon_agent/workspace.py`: add the small
  `WorkspaceManager` protocol and `FileWorkspaceManager` implementation for
  create, resolve, retain/remove, and mtime-based garbage collection.
- `src/simple_long_horizon_agent/run_control.py`: persist optional
  `RunRecord.workspace_ref` with backward-compatible loading.
- `src/simple_long_horizon_agent/recoverable_runtime.py`: create workspace
  references with a Run and resolve them before model execution.
- `src/simple_long_horizon_agent/__init__.py`, `scripts/arch_lint.py`: expose
  and classify the new core API.
- `tests/unit/test_workspace.py`, `tests/unit/test_recoverable_runtime.py`,
  `tests/unit/test_core.py`: cover workspace lifecycle, recovery, missing
  workspace blocking, and public API compatibility.
- Runtime design notes (subsequently removed during documentation cleanup):
  sync workspace lifecycle
  and recovery guarantees.

## Verification

- `uv run python -m unittest tests.unit.test_workspace tests.unit.test_recoverable_runtime tests.unit.test_run_control tests.unit.test_checkpoint tests.unit.test_core -v`: passed, 56 tests.
- `uv run ty check src`: passed.
- `uv run python -m scripts.arch_lint`: passed.
- `uv run python -m scripts.lint_docs`: passed.
- `git diff --check`: passed.
- `bash runs/dev/run_ci.sh`: passed; 728 unit tests, 6 skipped, formatting,
  lint, type, documentation, architecture, environment, and demo checks.

## Compatibility and Security

Existing Run records without `workspace_ref` still load and execute as before.
Workspace IDs are simple names constrained below the manager root, and missing
or invalid references block execution before the model or tools run.

## Known Limitations

This is a local filesystem manager. It does not snapshot Git state, mount
remote volumes, bind workspace lifetime to leases, or coordinate garbage
collection across multiple service instances.

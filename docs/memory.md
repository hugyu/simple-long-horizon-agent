# Filesystem Memory

Implementation guide for `src/simple_long_horizon_agent/memory/`. Code and
`tests/unit/test_memory.py` remain the source of truth for behavior; this file
holds the decisions that are not visible in either.

## The interface

```text
Memory
  initial(ctx)  -> tuple[Message, ...]    at SESSION_START
  tools(ctx)    -> tuple[AgentTool, ...]  at assembly
  finish(ctx)   -> None                   at SESSION_END
```

That is exactly what the core hook points can drive today. Query-shaped recall
and per-turn observation would need pre-model and after-turn hook points the
runtime does not expose — add the method together with the hook point that
calls it, never a no-op that reads as a live extension seam.

Memory stays outside the core: it injects ordinary `Message` values, exposes
ordinary `AgentTool`s, and binds through the normal `HookMap`. Nothing in
`Message`, the context view, or the provider adapters knows memory exists.

Bind before building the agent, then pass `binding.tools` and `binding.hooks`
into `make_llm_agent` — the same path MCP toolsets use.

Injected memory is a `RuntimeMessage` with `sender="memory"`,
`kind="context"` (`memory_context_message(...)`). Prompt-only injection is a
special case and must still be visible in the trace — hidden prompt mutation
makes it impossible to explain why the model knew something.

## Quality, not recall

The durable lesson is not to maximize recall:

- Do not save secrets, large raw outputs, generic advice, current-task state, or
  anything cheaper to read from code, docs, or git.
- User messages, corrections, accepted workflows, and tool evidence outrank
  assistant-authored summaries.
- Memory naming current files, flags, or repo state is a dated observation;
  verify it against the checkout when that is cheap. Workspace facts always
  outrank stale memory.
- "No memory update" is a successful outcome.
- Do not start with vector or hybrid RAG — coding-agent memory is sparse,
  high-value, and drift-prone enough that inspectable file-backed memory is the
  better first path.

## FilesystemMemory layout and guarantees

Memory root defaults to `~/.simple/memory`; pass `root=...` to place it under an
eval output dir, a mounted container dir, or a workshop dir. Each namespace is
`{root}/{memory_name}` and holds:

| File | Owner | Role |
| --- | --- | --- |
| `MEMORY.md` | the distiller model | the single durable handbook, rewritten whole |
| `memory_summary.md` | rebuilt from `INDEX.md` unless the model supplies one | cold-start navigation |
| `INDEX.md` | `finish(...)` | routes to per-run evidence |
| `runs/{run_id}/` | host-written | raw evidence + `artifacts/` |

Decisions worth knowing:

- **One writer per root.** `finish(...)` serializes the whole
  read-distill-commit operation with an inter-process lock on the root — the
  model call included, because `MEMORY.md` is a full rewrite and the distiller
  may pick the namespace only after reading prior memory. Atomic temp-file
  replacement gives per-file atomicity; it is not a substitute for this
  logical single-writer boundary. Child-only namespace mounts share the root
  `.memory-lock/`.
- **The model owns merging.** The distiller sees the current `MEMORY.md` and
  returns the complete updated file, doing its own dedup, rewrite, and pruning.
  `finish(...)` keeps no section or promotion logic. Determinism lives only in
  the guardrails: an empty rewrite keeps the prior handbook, and one that is
  oversized, structurally empty, or would erase every lesson is rejected, with
  a `memory_error.md` marker recording the skip.
- **Failure is never fatal.** Bounded run evidence and a minimal `summary.md`
  are still written when distillation fails, so `INDEX.md` never points at a
  missing summary. A memory failure must not fail an otherwise valid run.
- **Limits are small on purpose.** 64 runs and 128 MiB per namespace, 128
  namespaces per root, oldest evidence pruned after a write. All in
  `FilesystemMemoryLimits`.
- **Run ids must be distinct.** `runs/{run_id}` falls back to `session_id` then
  a timestamp; a complete existing run with the same id makes `finish(...)` a
  no-op.

## Recall is model-driven

`initial(...)` injects a short policy block naming the memory path (or, when no
namespace is known, the root plus at most 64 namespace names). The model then
reads memory through the ordinary `read`/`bash` tools. The runtime does **not**
prefetch or inject matching lines before a model request. Keep it that way for
filesystem memory; a runtime-retrieval shape (vector snippets) would be a
different `Memory` implementation, not a change to this one.

## Keeping it domain-neutral

The mechanism came from SWE-bench-shaped work. Keep the generic shape:

- Domain outputs go under `artifacts/` — not a hard-coded `patch.diff`. SWE
  stores `submission.txt`; another domain stores a JSON or a report.
- Index fields stay generic: `Summary`, `Scope`, `Signals`, `Keywords`,
  `Artifacts`.
- Benchmark labels and scoring outcomes must never leak into future runs.

## Evals integration

Container runs opt in through `SAL_MEMORY_HOME` (plus `SAL_MEMORY_NAME` /
`SAL_MEMORY_RUN_ID`); `LocalDockerBackend(memory_home=...)` bind-mounts the host
directory and sets them. Memory itself never imports Docker or evals — assembly
code passes it a local path. A suite's optional `memory_artifacts(...)` hook
supplies that run's durable products, captured inside `memory.finish` at
`SESSION_END` while the workspace is still intact.

## Updating and retiring advice

New or changed lessons should be top-level Markdown bullets with indented
`Scope`, `Source`, `Verified`, `Status` (`active` or `needs-verification`), and
`Recheck` fields. The distiller is instructed to replace advice contradicted by
explicit user corrections or current workspace evidence, and to leave unexplained
failures uncertain. Age and commit changes trigger rechecking, not automatic expiry.
This semantic policy is model-driven, not a proof of correctness.

After an accepted rewrite, `MEMORY.provenance.json` records each bullet's namespace,
source run and observed revision. Pass a known revision in
`MemoryContext(data={"memory_revision": "abc123"}, ...)`; unknown stays unknown.
Unchanged bullets retain their provenance; preexisting untracked bullets are marked
`legacy:unknown`. These are provenance records, not verified truth. The stored
handbook hash permits detecting manual changes; runtime does not automatically
revalidate them. Missing/pruned evidence and scope mismatches require fresh checks.

Explicit host maintenance uses the same root lock as distillation:

```python
from simple_long_horizon_agent.memory import FilesystemMemory

memory = FilesystemMemory(root="/path/to/memory")
memory.invalidate(
    "project",
    lesson="- Old advice.\n  Scope: project",  # exact bullet, including metadata
    reason="Current project evidence contradicts this advice",
)
memory.reset("project", reason="Project migrated; relearn from current evidence")
```

`invalidate` removes one exact top-level bullet and its indented metadata; a missing
entry raises `ValueError`. `reset` explicitly clears the handbook, bypassing only
the automatic rewrite's erasure guard. Neither operation deletes run evidence.
Both replace the navigation summary, keep one `MEMORY.previous.md` backup, and
record reasons/retired bullets in `MEMORY.control.json`. Readers must consult that
control file first; it overrides old advice in handbooks, backups and run summaries.
The distiller also receives it and must not revive retired advice from historical
runs. A new namespace is appropriate for an unrelated environment.

The control record is written first so interrupted maintenance still leaves an
invalidation barrier. Writes are atomic per file, not a transaction across files;
retry maintenance after an I/O failure. Existing agents may already hold old
context, so start a fresh session after maintenance. No automatic TTL or semantic
conflict classifier is implemented. The host rejects rewrites containing a retired
bullet's original first-line advice, even if its metadata changed; paraphrases
still rely on the model policy and workspace revalidation. Retirements are not
silently pruned; their record is capped at 100,000 characters and a full record
requires a new namespace. Run evidence remains subject to normal retention limits.

Regression coverage: `tests/unit/test_memory_maintenance.py` exercises corrections,
provenance retention, invalidation, reset, resurrection rejection and interrupted
maintenance. `tests/unit/test_memory.py` covers root-lock serialization and the
existing bounded storage and rewrite guards.


Recall can page through a compressed message with a character `offset`; follow
its `next_offset` marker to retrieve details beyond the first page. The long-task
entry also enforces a pre-dispatch input allowance after ordinary compression.
See [runtime recovery](runtime-recovery.md#long-task-context-and-command-recovery)
for fixed-input limits and configuration.

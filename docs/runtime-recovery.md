# Recoverable code-task runtime

The recoverable service runs one logical task across worker lifetimes. Its
contract is implemented in `recoverable_runtime.py`, `run_control.py`,
`checkpoint.py`, `event_journal.py`, and `reconciliation.py` under
`src/simple_long_horizon_agent/`. The ordinary Agent loop remains independent
of the persistence backend.

## Completion authority

Pass a `completion_check: Callable[[State], CompletionResult]` to
`RecoverableRunExecutor` to verify the environment after each Agent attempt.
The same verdict type is used by the in-process Goal Loop. A check should be
safe to repeat, have a timeout, and use caller-owned requirements or tests.

| Run status | Meaning |
| --- | --- |
| `finished` | Agent ended without an external completion check; success is unverified |
| `complete` | The configured external completion check passed |
| `runnable` | Another attempt is needed; verification feedback is in the transcript |
| `budget_exhausted` | The attempt limit or a configured durable-state resource budget was reached |
| `blocked` | Verification or operation reconciliation cannot safely proceed |
| `aborted` | The caller cancelled execution |

`max_attempts` defaults to three admitted worker attempts and is persisted when
the Run is created. The counter is checkpointed before constructing a state-bound
agent or making model calls, so exceptions and crashed workers consume attempts.
Recovery that only repeats a verifier does not consume another attempt. Rebuilding the executor with a larger default does not reset
an existing Run's budget. Each verdict becomes a `GoalStatusEvent`; failed
checks also append model-visible feedback before the Run is released.
`max_turns` bounds each inner Agent attempt, not the entire service lifetime.

Runs created with a verifier remember that verification is required. Removing
the callback on recovery must not turn them into verified successes. A check
that raises an exception blocks the Run with a recorded reason. Historical
`complete` records created by older versions are not retroactive evidence of
external verification; inspect their verdict events.

## Recovery and write ownership

- `State.task` is persisted separately from its model-visible task message.
  The executor initializes the message when restoring an empty transcript,
  preserving workspace and runtime metadata.
- The Journal is an append-only event stream. A Checkpoint contains a typed
  event prefix and metadata. Recovery validates the prefix and replays the
  Journal tail. A newline commits a Journal record: an unterminated suffix
  from a killed writer is discarded on the next append, while malformed
  committed records or a conflicting Checkpoint fail closed.
- `RunStore.guard(record)` protects artifact writes using the same
  cross-process lock as lease acquisition. The executor uses it for Journal,
  Checkpoint, evidence and operation-ledger writes. A replaced worker cannot
  commit these artifacts using its old lease. Custom RunStore implementations
  must provide an equivalent exclusion and fencing contract.
- Every Bash command is treated as potentially side-effecting. Python,
  package managers and test commands can write files; shell keyword matching
  is not a read-only proof. Use the dedicated `read` tool for explicit reads.
- Tool outcome and execution certainty are independent. An observed Bash exit,
  including a failing test, confirms execution; it does not certify task success.
  Argument/precondition failures that never write also confirm a known outcome.
  Timeouts, interrupted writes and unknown exceptions still require reconciliation.
- An unconfirmed side effect is reconciled before model execution. The edit
  reconciler checks the recorded post-image hash. Unknown tools require a
  caller-supplied reconciler; absent proof blocks the Run.
- If the side effect was confirmed but its model-visible result was lost,
  recovery supplies a result that reports confirmation and missing output.
  It does not repeat the operation or fabricate the original output.
- If a worker dies after the Agent-end event, recovery repeats the verifier
  rather than starting a new model attempt. A durable terminal verdict can be
  released without repeating model work.

These guarantees concern the local filesystem control plane. Already-running
external processes can continue after lease loss, so tools must support their
own cancellation or reconciliation. There is no general exactly-once shell
execution, shared-database transaction, power-loss durability guarantee, or
multi-tenant sandbox. Stores used outside the executor must obey the same
guard discipline. Keep workspaces available until reconciliation is complete.

## Executable acceptance

Run from the repository with a fresh output directory:

```bash
uv run python -m examples.recoverable_code_task.demo \
  --hard-crash --output evals/out/recovery-demo
uv run python -m unittest tests.unit.test_recovery_acceptance
```

The demo first proves the fixture fails, then rejects a premature completion
claim, executes an edit, and kills a worker with `SIGKILL` after the file write
but before ledger confirmation. A new service waits for lease expiry,
reconciles the edit, restores its tool result and passes the independent test.
It asserts that the edit was dispatched once. Outputs include exact verifier
logs, `repair.patch`, `trajectory.jsonl`, Checkpoint, Journal and evidence.
`manifest.json` and `source.tar.gz` preserve task, model settings and the source
used by the run, including uncommitted code and the dependency lock.

The default model is scripted to place the fault precisely. It proves
integration behavior, not model quality. To run the repair with the configured
real provider, omit `--hard-crash` and add `--provider openai`; use the standard
provider environment described in [configuration.md](configuration.md).


## Bounded history with SQLite

The default `State` and JSON `FileCheckpointStore` retain/decode complete event
history in memory. Context compression alone does not bound that history.
For long runs, opt into `SqliteCheckpointStore`:

```python
from simple_long_horizon_agent import (
    FileRunStore, RecoverableRunExecutor, SqliteCheckpointStore, StateLimits,
)

executor = RecoverableRunExecutor(
    run_store=FileRunStore("runs/control"),
    checkpoint_store=SqliteCheckpointStore("runs/state", limits=StateLimits()),
)
```

Use the same `create` / `execute` or service API as above. Omit `event_journal`:
SQLite is the single authority for events, message indexes and the active view.
Each recorded event commits those projections in one SQLite transaction before
returning. `save()` checkpoints task and scratch metadata; `load()` opens the
latest committed projection, including events newer than that metadata, without
replaying history. Scratch metadata changes still require `save()`. A database
interrupted during initial creation fails closed; it is not an admitted Run.

For a standalone loop, create a durable state with
`store.create("run-id", task)`, seed its task message with `state.send(...)`, and
pass it to `run(agent, state)`. Attach `make_recall_tool(state)` to that same
state. Reopen with `store.load("run-id")` after restart. Use distinct run IDs;
creating over an existing database is rejected. Standalone callers must own
the writer; the recoverable executor additionally fences every state append
with its Run lease, including appends made before an event is yielded.

`messages` and `events` are read-only, fixed-length lazy sequences in this mode.
Indexing, slicing and iteration do not materialize the whole history; slices
remain lazy. Recall reads just the requested original message IDs. No decoded
history cache is retained in Python; each SQLite connection has a 2 MiB page
cache budget. Message IDs never change when a message leaves active context.
The active view remains bounded and preserves the normal compression ordering.

Default `StateLimits` cap serialized events at 8 MiB each, active messages at
2,048 / 8 MiB, cumulative event payloads at 256 MiB, metadata at 64 KiB and the
database file at 512 MiB. Limits are stored with the run; changing constructor
defaults does not silently increase an existing run's budgets. Leave space below
the active limit for a turn and its compression summary. No automatic history
pruning is performed because it would invalidate Recall citations. Crossing a
limit raises `StateResourceLimitError` and rolls back the rejected event; the
executor releases the run as `budget_exhausted` instead of retrying indefinitely.

These are storage/retention budgets, not a hard process RSS limit. JSON decoding,
active model requests and parallel tools need additional memory. SQLite rollback
journals need temporary disk space (allow roughly another database-sized file).
Custom tools must bound their own output before returning, especially images
and subprocess output. Full trace exports, `list(state.events)`, and extensions
that build whole-transcript strings explicitly materialize history; perform
large exports as streaming/offline work. The generic filesystem-memory distiller
is such an extension and is not a bounded streaming exporter. SQLite mode does
not change the simple demos' in-memory default or imply production-scale support.

Validation:

```bash
uv run python -m unittest tests.unit.test_sqlite_state -v
```

The suite checks disk Recall after compaction/restart, lazy recovery, fixed-view
semantics, long-history Python allocation bounds, resource-limit rollback,
parallel readers, stale-worker fencing, and SIGKILL in an uncommitted transaction.

## Durable execution budgets

The executor accepts `max_model_calls`, `max_tokens` and `wall_clock_seconds`.
Limits are persisted at creation; the deadline is an absolute timestamp, so
restarting a worker does not reset limits or exclude downtime. Model request
reservations and reported usage are recovered from committed events. Tokens
include fresh input, output, cache reads and cache writes. A request reservation
without a response still consumes a call slot. Budget exhaustion releases the
Run as `budget_exhausted` and records its reason in state metadata.

These are boundary checks, not a provider billing cap: an in-flight response
can exceed the remaining token allowance, missing provider usage cannot be
reconstructed, and SDK/internal model retries are not separate core request
events. Child agents and custom model-calling tools need their own shared
accounting; the unified entry deliberately uses a single worker and rule-based
compression. Blocking model calls are not preempted by the deadline. Bash polls cancellation
and on POSIX kills and reaps its process group; deliberately detached processes
are outside this guarantee. Generic Python tools receive cooperative cancellation
and are joined before returning a timeout, preventing a timed-out writer from
racing subsequent actions. An uncooperative Python tool can still delay return;
use process-backed tools for cancellable external work. No dollar budget
is inferred from possibly missing model prices.

`execute(..., agent_for_state=...)` optionally constructs the agent after state
recovery and attempt admission. The first successful factory persists the agent name; subsequent
attempts must preserve that name. Service factories also run inside attempt
admission, so construction errors cannot bypass retry limits. Use it to bind Recall to the actual restored SQLite state. Factory failures
consume the same attempt budget as model failures.

## Unified long-task entry

Configure the provider environment described in [configuration.md](configuration.md),
then run from this repository:

```bash
uv run python -m scripts.run_long_task \
  --workspace /absolute/path/to/task-repository \
  --run-dir /absolute/path/to/run-artifacts \
  --task "Implement the requested change and preserve existing behavior" \
  --verify-command "uv run python -m unittest discover -s tests" \
  --max-attempts 10 --max-model-calls 100 --max-tokens 500000 \
  --wall-clock-seconds 3600

uv run python -m scripts.run_long_task \
  --run-dir /absolute/path/to/run-artifacts --resume
```

The entry disables inner model re-asks and application retries so failures return
to its durable attempt budget; SDK transport retries remain provider-owned.

The workspace must be a Git working directory. Keep the run directory outside
it. The entry stores SQLite history, an operation ledger, fixed caller-owned
verification, tool compaction and Recall. Resume uses the saved task, workspace,
verifier and limits, and requires the same model/API/reasoning settings. An
unexpired lease must expire before a killed worker can be replaced. `--once`
drives one attempt and exports artifacts; it does not declare success unless the
verifier passes. Exit code 2 means the result is not verified complete.

Artifacts include the runtime source archive and manifest, before/after task
file snapshots, a binary-capable patch, trajectory, exact verification logs,
errors, result and cost rollup. Snapshots use tracked and non-ignored files;
keep credentials ignored. Trace/cost export is offline work that can materialize
history. Missing usage or prices must not be interpreted as zero actual cost.
The verifier command is fixed in the manifest, but workspace tests remain
editable: use caller-controlled tests outside the workspace for stronger
acceptance. Local tools are not a sandbox. Arbitrary interrupted Bash commands
still block if their effects cannot be reconciled.

A multi-file integration acceptance uses this same entry:

```bash
uv run python -m examples.recoverable_code_task.long_task \
  --output evals/out/long-task-acceptance
uv run python -m unittest tests.unit.test_run_budgets tests.unit.test_long_task_entry
```

It rejects a premature completion claim, injects a model failure, compacts and
recalls history, kills a worker after a file write, then recovers and passes
caller-owned checks for parsing, aggregation and CLI output. Three file edits
must be dispatched exactly three times. The scripted worker proves wiring and
fault recovery, not real-model long-task success rates. Use the unified entry
with a configured provider to collect real-model evidence on a chosen task.

## Coding task guidance and delivery

The unified entry reads the Git root and non-ignored nested `AGENTS.md` files
with explicit directory scopes. Instructions are limited to 64,000 characters;
oversized guidance fails explicitly. Saved guidance and initial Git status are
included in run artifacts. Guidance is refreshed when constructing the worker.
This is instruction loading, not a filesystem sandbox or permission boundary.

The `task_status` tool stores a bounded requirement checklist, evidence message
indices, next action and blocker in the same durable State. Updates are saved
under the Run write guard before acknowledging the tool. Reading the tool returns
recent message IDs for citation. Notes are model claims, not independent proof;
marking every requirement done never overrides the caller verifier. Recall can
recover cited evidence after compaction. Tool compaction replaces its previous
summary with bounded recent distinct observations, retaining links to earlier summaries in the
append-only transcript. The threshold is a trigger, not a hard context cap:
pinned instructions and recent oversized tool results still require headroom.

Delivery compares files against the initial snapshot, records hashes and checks
new diff whitespace. Existing tests and instruction files are protected by
default; adding regression tests is allowed. A caller intentionally changing
those files can set `--allow-test-changes` when creating the run. The setting is
persisted. A passing command cannot override a protected-file or diff-check
failure. `delivery.json` records the checks and `task-progress.json` exports the
last notes. Changes already present before the run form the comparison baseline.
Three identical verification failures with unchanged file changes stop as
`blocked`; a changed patch resets this streak. This detects repetition across
attempts, not every possible unproductive sequence within an attempt.

Validation: `uv run python -m unittest tests.unit.test_code_task_safety` covers
failed-command recovery, service factory limits, cooperative cancellation,
process-group termination, repeated compaction, scoped rules, protected tests and
durable task notes. These mechanisms support a single-agent coding workflow;
repository isolation, hard billing limits and a broad real-task benchmark remain
separate work.


## Long-task context and command recovery

The long-task entry records `--max-input-bytes` (default 64000) in its manifest.
The legacy `--max-input-tokens` option remains an alias with its original byte
semantics; it is not a token count. Reserve output space separately. Before dispatch, a conservative UTF-8 byte estimate includes
messages, system instructions and tool schemas. When normal compaction cannot
fit it, the largest non-pinned messages are shortened to head/tail excerpts
with paged-recall citations, preserving tool linkage. If that is insufficient,
older messages are folded while keeping recent exchanges and saved task progress. Task, system and context instructions stay intact. If fixed input
still cannot fit, the run stops with a resource-limit reason rather than retrying
an unchanged oversized request. Explicit provider context-window errors trigger one emergency compaction retry;
a second rejection stops instead of repeating the same request. This estimate
is not a provider tokenizer or an
image-token guarantee; configure extra margin for multimodal tasks.

Recall accepts `offset` in characters of a rendered message body. A truncated
page provides `next_offset`; request that same message index with the new offset.
Offsets refer to immutable original transcript messages, so later compactions do
not move them. Images remain omitted from text recall.

Bash recovery defaults to blocking unknown outcomes. Pass repeatable
`--recoverable-command 'exact command'` options to authorize recovery retries.
The match is exact, not a shell prefix; the model cannot grant itself permission.
Only commands safe to repeat even if an earlier child survives should be listed.
This can include caller-reviewed test or build commands, but never assumes all
tests, builds or package installers are idempotent. Recovery re-executes an
approved command with a timeout capped by the remaining task deadline. Normal
exit confirms execution even when a test fails; its exit code and bounded output
are restored to the model. Interrupted commands remain blocked. Recovery runs
under an actively renewed lease. Recovery feedback records re-execution.
Existing manifests without this list retain the default blocking behavior.


New edit intents store both file hashes and replay arguments. Recovery confirms
an existing post-image, replays an unchanged pre-image, and blocks conflicting
content. Older intents without pre-image metadata retain post-image-only checks.
Replay arguments contain edited text and should be treated as workspace data.

# agent-repl v2 Plan

## Goal

Build `agent-repl` v2 around a native `agent-repl` core that owns notebook identity, shared state, history, collaboration, and execution orchestration, while keeping Jupyter kernels and `.ipynb` compatibility as boundary adapters.

This plan assumes:

- VS Code is no longer the source of truth
- `.ipynb` is no longer the live authority
- Jupyter kernels remain the default execution backend
- humans and agents are both first-class live clients

## Why v2 exists

v1 is clever, useful, and already proves demand, but it inherits a set of problems from treating editor state as the authority:

- multiple sources of truth: `.ipynb` on disk, live editor state, installed extension code, kernel state
- lazy identity assignment
- different semantics for read, edit, and execute paths
- operational drift between CLI, bridge, editor, and kernel
- no natural model for branching, sub-notebooks, or agent-owned work regions

v2 should solve those problems from the center instead of continuing to patch them at the edges.

## Product Shape

`agent-repl` v2 should behave like a shared notebook runtime with multiple clients:

- human client: VS Code notebook UI, later web UI
- agent client: CLI, long-running agents, subagents, automations
- system client: background import/export sync, kernel manager, recovery worker

The key design choice is:

- **core authority**: `agent-repl-core`
- **execution adapter**: Jupyter kernel/session manager
- **file adapter**: `.ipynb` import/export and sync
- **UI adapters**: VS Code now, browser later

## Core Principles

1. **One source of truth**
   The canonical notebook state lives in the core runtime, not in VS Code and not in `.ipynb`.

2. **Durable identity from birth**
   Every node has a durable ID at creation time, with no fallback IDs.

3. **Ops before mutation**
   All changes are represented as operations/events applied by the core.

4. **RTC for humans and agents**
   Humans and agents interact with the same live document model through subscriptions and operations.

5. **Execution is authoritative**
   Execution state, queue state, ownership, cancellation, and outputs are tracked by the core, not inferred from editor heuristics.

6. **Compatibility without authority**
   `.ipynb` and Jupyter kernels remain important, but they are adapters, not the live state model.

7. **Crash-safe continuity**
   Restarting CLI, VS Code, or an agent process must not lose notebook state, execution state, or task continuity.

## Non-Goals

- v2 does not need to replace Jupyter kernels immediately
- v2 does not need a browser UI in the first slice
- v2 does not need offline-first peer-to-peer collaboration
- v2 does not need to preserve every quirk of VS Code notebook behavior

## Definitions

- **workspace**: top-level project container with permissions, files, kernels, and notebooks
- **document**: canonical notebook-like state object in the core
- **node**: a cell-like unit in the canonical graph; may be code, markdown, prompt, response, section, branch node, sub-notebook reference, or agent task node
- **projection**: a linearized view of a document for an adapter such as `.ipynb` or VS Code notebook UI
- **session**: a connected client session for a human, agent, or system client
- **run**: a tracked execution request against a node or a subset of nodes
- **runtime**: the compute process backing a document, typically a Jupyter kernel

## User Stories v2 must support

### Human workflows

- open notebook in VS Code and see live state
- edit text while another human or an agent is also editing
- run selected cells or branches
- close VS Code and later reopen without losing the live notebook state
- see what agents are doing in real time
- review agent changes before merging them into the main notebook path

### Agent workflows

- connect with CLI only, even if VS Code is closed
- read, edit, branch, and execute without relying on VS Code notebook APIs
- recover after agent process restart and continue from prior state
- work in isolated regions or branches without stomping on human edits
- coordinate with other agents and subagents

### System workflows

- import an existing `.ipynb`
- export a live document back to `.ipynb`
- detect external file overwrite and resolve it safely
- persist state continuously
- restart cleanly after daemon crash
- kill zombie kernels while preserving intended long runs

## Capability Model

### What the core must natively support

- durable IDs
- event log
- snapshots
- live subscriptions
- branching
- merge
- leases and ownership
- execution graph
- kernel/session lifecycle
- file sync metadata
- permissions and actor attribution

### What adapters must support

- VS Code projection: map canonical nodes to a linear notebook editing experience
- `.ipynb` projection: import/export linear notebooks
- Jupyter runtime adapter: execute code and stream outputs

## Canonical Data Model

### Workspace

Fields:

- `workspace_id`
- `root_path`
- `documents`
- `kernel_specs`
- `runtime_policies`
- `permissions`

### Document

Fields:

- `document_id`
- `workspace_id`
- `title`
- `projection_mode`
- `root_graph`
- `head_revision`
- `snapshot_revision`
- `dirty_state`
- `file_binding`
- `runtime_binding`
- `presence_state`

### Node

Fields:

- `node_id`
- `document_id`
- `node_type`
- `parent_id`
- `children`
- `attrs`
- `source`
- `outputs`
- `execution_state`
- `created_by`
- `updated_by`
- `branch_membership`

Initial node types:

- `markdown`
- `code`
- `prompt`
- `response`
- `section`
- `subnotebook_ref`
- `agent_task`

Later node types:

- `branch_marker`
- `review_thread`
- `result_panel`

### Runtime binding

Fields:

- `runtime_id`
- `runtime_type`
- `kernel_id`
- `status`
- `attached_sessions`
- `lease_policy`
- `last_heartbeat_at`
- `last_activity_at`
- `startup_policy`
- `shutdown_policy`

### File binding

Fields:

- `binding_id`
- `path`
- `format`
- `last_imported_hash`
- `last_exported_hash`
- `sync_policy`
- `last_sync_at`
- `external_change_state`

## Event Model

Every meaningful change goes through the event log.

### Event categories

- document lifecycle
- node operations
- text operations
- branch operations
- runtime operations
- execution operations
- file sync operations
- session operations
- lease operations

### Core event examples

- `document.created`
- `document.imported`
- `document.closed`
- `node.inserted`
- `node.deleted`
- `node.moved`
- `node.attrs_updated`
- `node.source_replaced`
- `node.source_text_delta`
- `branch.created`
- `branch.merged`
- `runtime.requested`
- `runtime.attached`
- `runtime.detached`
- `runtime.heartbeat_missed`
- `execution.requested`
- `execution.started`
- `execution.stdout_appended`
- `execution.output_replaced`
- `execution.completed`
- `execution.failed`
- `execution.canceled`
- `file.exported`
- `file.import_conflict_detected`
- `session.connected`
- `session.disconnected`
- `lease.acquired`
- `lease.released`
- `lease.expired`

### Event storage

Persist:

- append-only op log
- periodic snapshots
- runtime metadata
- resumable execution/task metadata

The first implementation can use SQLite or Postgres. SQLite is good enough for local first slices if the write path is disciplined.

## RTC Model

v2 needs real-time collaboration for both humans and agents.

### Core rule

All clients subscribe to the canonical document and receive ordered updates from the core.

### Recommended model

- **server-authoritative ordered ops** for structure, execution, leases, runtime changes, and branch operations
- **CRDT or text deltas** for node source editing only

### Why hybrid

Text editing benefits from CRDT-like behavior.

Execution, branch merge, lease ownership, and kernel lifecycle do not. Those should be server-authoritative operations.

### Presence model

Track:

- connected sessions
- selected node(s)
- active branch
- typing/editing state
- execution state
- ownership badges

### Agent-specific RTC requirements

Agents need:

- subscriptions to live state
- visible ownership of active regions
- branch-specific workspaces
- durable task state across reconnect

Agents do not need human-style cursors, but they do need visible work intent.

## Session Model

### Session types

- `human.vscode`
- `human.web`
- `agent.cli`
- `agent.daemon`
- `system.sync`
- `system.kernel_manager`

### Session semantics

Each session gets:

- `session_id`
- actor identity
- capabilities
- last heartbeat
- attached documents
- attached runtime views
- resumable work tokens

### Reconnect semantics

If a session disconnects and reconnects:

- it requests the latest snapshot revision
- it replays missed events
- it reattaches to document and runtime subscriptions
- it recovers active tasks or leases if still valid

## Notebook Lifecycle Use Cases

### 1. Notebook is imported from `.ipynb`

Desired behavior:

- import file into canonical model
- assign durable IDs immediately
- preserve imported metadata where useful
- create an initial linear projection

Mechanism:

- `document.imported`
- `snapshot.created`
- `file_binding.created`

### 2. Notebook is open in VS Code

Desired behavior:

- VS Code shows a projection of canonical state
- edits flow to core first, then back to all clients including VS Code

Mechanism:

- VS Code adapter subscribes over websocket
- local edits become core ops
- adapter updates notebook view from acknowledged ops

Important rule:

- never let VS Code maintain a shadow authority model

### 3. Notebook is closed in VS Code

Desired behavior:

- document remains alive in core if agents or runtimes still depend on it
- document may be evicted from memory after idle timeout if no work is attached

Mechanism:

- close only detaches that UI session
- document state remains persisted
- runtime follows its own lifecycle policy

### 4. Notebook is edited while open

Desired behavior:

- all clients see updates in real time
- source edits merge safely
- structural edits are ordered and conflict-safe

Mechanism:

- text delta or replace op for source
- server-ordered node ops for structure
- branch or lease escalation for risky parallel work

### 5. Notebook is saved

v2 should distinguish:

- **core-persisted**: state is durable in the op log/snapshot store
- **file-exported**: state has been flushed to `.ipynb`

Desired behavior:

- every accepted op is durable immediately
- file export happens automatically or explicitly based on policy

Important rule:

- "saved" should not mean "the editor file happened to flush"

### 6. Notebook file is externally overwritten on disk

Desired behavior:

- never silently clobber core state
- detect overwrite via file watcher/hash mismatch
- if no local divergence, import as new revision
- if there is divergence, create conflict state or a new branch

Mechanism:

- compare `last_exported_hash` and current file hash
- emit `file.import_conflict_detected`
- present:
  - accept external revision
  - keep core as authority and overwrite file on next export
  - create side branch and diff/merge

### 7. Notebook is deleted or moved on disk

Desired behavior:

- file binding becomes invalid
- canonical document remains intact
- user can rebind to a new path or export elsewhere

## VS Code Lifecycle Use Cases

### VS Code opens after core is already running

Desired behavior:

- adapter discovers core
- requests document projection and current presence state
- attaches without mutating authority

### VS Code closes while runs are active

Desired behavior:

- active runs continue if runtime policy allows
- human session disconnects
- agents can keep working
- reopening VS Code shows current state and outputs

### VS Code crashes

Desired behavior:

- no loss of canonical state
- no stuck busy flags
- reconnect acts like a normal resubscribe

Important consequence:

- execution state must never be tracked only by editor event handlers

## CLI and Agent Lifecycle Use Cases

### CLI starts with no VS Code open

Desired behavior:

- CLI can still discover and operate against the core
- no dependency on a notebook editor being visible

This is a key v2 requirement.

### CLI restarts

Desired behavior:

- reconnect to same workspace/document
- resume from op log and current snapshot
- query active runs, branches, and leases

### Agent process restarts

Desired behavior:

- agent can recover prior work context
- agent-owned branches and leases remain durable for a grace period
- agent can adopt prior task state with a resume token

Mechanism:

- store `task_id`, `branch_id`, `lease_id`, `last_seen_revision`
- allow explicit `resume-task` flow

### Multiple agents work concurrently

Desired behavior:

- no stomping by default
- agents either:
  - work on separate branches
  - acquire leases on regions
  - make mergeable text edits in same node

Default policy:

- branch by default for broad changes
- shared editing only for narrow text work

## Execution Model

### Core execution rules

- execution requests are explicit ops
- core owns queue state
- outputs stream back into canonical node state
- execution status persists independently of client connections

### Execution targets

- single node
- linear range
- branch/subgraph
- whole notebook projection
- background task node

### Output ownership

Outputs should be associated with:

- `run_id`
- `runtime_id`
- `node_id`
- actor/session that initiated the run

### Continuity rule

If the initiating client disconnects:

- execution continues unless explicitly canceled
- output remains visible to all subscribers

## Kernel and Runtime Plan

### Runtime abstraction

Introduce a runtime manager with pluggable backends.

First backend:

- Jupyter kernel adapter

Future backends:

- Pyodide
- containerized Python worker
- domain-specific workers

### Runtime states

- `starting`
- `ready`
- `busy`
- `idle`
- `detached`
- `draining`
- `stopped`
- `failed`

### Runtime attachment modes

- **interactive**: bound to an actively edited document
- **shared**: multiple sessions attached
- **headless**: no UI attached, but runs or tasks remain active
- **pinned**: keep alive across idle periods
- **ephemeral**: safe to stop when idle

### Zombie kernel prevention

Zombie kernels are a first-class risk. v2 should prevent them by policy, not by wishful thinking.

Required mechanisms:

1. **Heartbeat tracking**
   Runtime manager tracks liveness independent of editor UI.

2. **Lease-based ownership**
   A runtime belongs to a document binding, not to a random editor tab.

3. **Idle policy**
   Stop ephemeral runtimes after configurable idle TTL if:
   - no attached sessions
   - no active runs
   - no pinned task

4. **Draining policy**
   When last session disconnects, runtime moves to `detached` or `draining`, not immediately to dead.

5. **Reaper**
   Background worker scans for:
   - stale heartbeats
   - detached runtimes past TTL
   - orphaned child processes

6. **Persistent run ledger**
   If a runtime dies unexpectedly, affected runs are marked failed or interrupted explicitly.

7. **No UI-owned runtime truth**
   Closing VS Code must not be the signal that decides whether a kernel exists.

### Runtime reuse rules

Reuse a runtime only if:

- same workspace trust boundary
- same environment spec
- same document or explicitly compatible branch
- no policy violation

Default:

- one primary runtime per document head
- optional branch-local runtimes for risky or divergent work

## Save and Sync Model

### Core persistence

Every op is persisted immediately.

### File export policies

Support:

- auto-export on stable intervals
- explicit export command
- export on close
- disabled export for transient documents

### `.ipynb` export rules

Projection must define:

- node ordering
- supported node types
- unsupported node representation
- output serialization policy
- branch flattening policy

Unsupported native v2 concepts should round-trip predictably, for example via metadata or sidecar structures.

### Sidecar support

For rich v2 features not representable in `.ipynb`, support a sidecar file:

- `.agent-repl.json`

This can store:

- branch graph
- leases
- agent task nodes
- merge history
- presence history if needed

Rule:

- `.ipynb` export should remain useful without the sidecar
- full fidelity restore may require the sidecar

## Conflict Model

### Text conflicts

Handled by text delta / CRDT strategy.

### Structural conflicts

Handled by ordered ops and branch rules.

Examples:

- two inserts at same location
- move vs delete
- branch merge after parallel edits

### Runtime conflicts

Handled by explicit policy.

Examples:

- human runs while agent rewrites source
- two agents request mutually incompatible runtime setup

Default policy:

- source mutation on a running node creates stale state but does not mutate the in-flight run
- risky concurrent work should branch

## Continuity and Recovery

### Core daemon restart

Desired behavior:

- replay op log
- restore latest snapshots
- restore runtime metadata
- reconcile actual kernel processes
- resume websocket service

### Runtime recovery after core restart

Cases:

- kernel still alive and reattachable
- kernel dead and must be marked interrupted
- kernel alive but unreachable and should be fenced off then reaped

### Agent continuation

Allow agents to:

- list active branches
- list active tasks
- list active runs
- resume a previous task by ID

### Human continuation

When reopening a notebook:

- show latest projection
- show unresolved conflicts
- show stale branches or interrupted runs

## Permissions and Safety

v2 needs at least basic actor-aware safety.

### Capabilities

- view
- edit
- execute
- create branch
- merge
- manage runtime
- export/import

### Safety defaults

- agents cannot silently overwrite human work in main branch when lease is held by a human
- broad agent changes default to branch mode
- merge into main branch should be explicit

## Migration Strategy

### Phase 0: stabilize v1

- remove fake `index-*` IDs from public workflows
- make reads and writes use the same identity model
- reduce dependence on editor visibility
- improve installed-source drift detection

### Phase 1: introduce core authority for identity and ops

- add local daemon
- introduce canonical document store
- route CLI through daemon first
- keep VS Code adapter thin

### Phase 2: move execution authority into the core

- add runtime manager
- move queue, run tracking, and output stream ownership out of editor event heuristics
- support CLI-only operation

### Phase 3: add branches, leases, and sub-notebooks

- branch ops
- merge ops
- agent task nodes
- sub-notebook references

### Phase 4: advanced multiplayer and secondary UI

- browser UI
- richer review flows
- collaborative merge views

## Thin First Slice

The smallest meaningful v2 slice is not "rewrite notebooks." It is:

1. run a local `agent-repl-core` daemon
2. represent documents canonically in the daemon
3. assign durable IDs on import/create only in the daemon
4. make CLI read/edit go through the daemon
5. make VS Code extension become a projection client
6. keep Jupyter kernel execution through a runtime adapter
7. export back to `.ipynb`

If this slice works, the biggest class of v1 flakiness disappears:

- no disk/live identity mismatch
- no editor-owned source of truth
- no "cat says one thing, edit resolves another"

## Acceptance Criteria for v2

v2 is successful when all of the following are true:

1. `cat`, `edit`, and `exec` all operate against the same canonical document state
2. CLI-only operation works without VS Code being open
3. restarting CLI or agent processes does not lose work context
4. closing VS Code does not corrupt state or strand notebook progress
5. zombie kernels are detected and reaped by policy
6. concurrent human and agent work is visible, attributable, and conflict-safe
7. `.ipynb` import/export remains useful
8. branch/sub-notebook workflows are possible without abusing duplicated files

## Open Questions

1. Should the first text sync implementation use CRDTs immediately, or start with server-ordered replace ops plus optimistic editing?
2. Should branches always own separate runtimes, or only optionally?
3. How much of v2 fidelity should be stored in `.ipynb` metadata versus a sidecar file?
4. Do we want one daemon per workspace, or one daemon managing many workspaces?
5. Should execution results be content-addressed for caching and replay?
6. What is the minimal merge UX for humans reviewing agent branches in VS Code?

## External References and Reuse Strategy

This section reflects a web-grounded scan completed on March 24, 2026.

### Strong reuse candidates

- `jupyter-server-ydoc`: backend shared document model and RTC server foundation
- `jupyter-ydoc` and `@jupyter/ydoc`: canonical notebook shared-model schema
- `jupyter-docprovider`: adapter pattern for loading collaborative document models
- `pycrdt-websocket`: lower-level transport reference if custom sockets are needed
- `pycrdt-store`: persistence/checkpoint reference for reconnect and recovery
- `nextgen-kernels-api`: promising reference for decoupled multi-client kernel access
- `jupytext`: useful adjunct if we want paired text representations for review and Git workflows
- `nbdime`: useful adjunct for notebook diff and merge UX

### High-value product references, low direct reuse

- `Jupyter AI`: strong reference for provider plumbing, personas, frontend/backend extension points, and notebook-native AI affordances
- `Jupyter AI Agents`: strong reference for notebook-wide agent tools and live coordination patterns
- `Notebook Intelligence`: useful reference for notebook-native agent tools and inline/chat/autocomplete separation
- VS Code notebook + Copilot flows: useful benchmark for inline edits, multi-cell generation, and approval-oriented agent loops
- Deepnote Agent and Datalore AI: useful product references for plan visibility, whole-notebook changes, and cell-scoped AI actions

### Reference only

- `jupyter-collaboration`: excellent if we choose to integrate more directly with JupyterLab and Notebook 7 UX, but it should not become the authority if `agent-repl-core` owns document state
- `jupyter-scheduler`: reference for job APIs and execution-control UX
- `jupyterlab-git`: reference for extension packaging, frontend/server split, and operational UX
- `jupyterlab-lsp`: reference for notebook-aware diagnostics and navigation
- `Elyra` and `Voilà`: reference for larger workflow composition and notebook-to-app publishing, not core collaboration

### What to adopt vs what to own

We should likely adopt or prototype against:

- shared-model primitives from the `jupyter-ydoc` family
- CRDT transport and persistence ideas from the `pycrdt` stack
- kernel/session ideas from `nextgen-kernels-api`
- notebook review tooling from `jupytext` and `nbdime`

We should own:

- document authority
- operation log and replay model
- branch and sub-notebook semantics
- human/agent session model
- runtime ownership, leases, and zombie-kernel policy
- the CLI and VS Code adapter contract

### Deepnote decision

Do not switch the foundation of `agent-repl` v2 to Deepnote.

Reason:

- Deepnote is now publicly open around its notebook format and conversion tooling, but there is not clear evidence of a fully open, self-hostable runtime/platform that we can treat as a project foundation
- its strongest value to us is product inspiration, not an embeddable architecture
- it is better read as "portable format plus hosted product" than as "open notebook runtime we can build directly on"

The practical Deepnote takeaway is:

- study its product shape
- do not anchor v2 on its stack
- keep direct compatibility with Jupyter and local workflows instead

### Current best strategic fit

The strongest outside-supported direction is:

- own the live notebook/runtime model in `agent-repl-core`
- keep Jupyter kernels as execution adapters
- borrow shared-model and RTC building blocks from the Jupyter `ydoc` and `pycrdt` ecosystem where they fit
- treat commercial notebook AI products as workflow references, not infrastructure
- treat Deepnote as a design benchmark, not a platform dependency

## Recommended Answer to the Main Strategic Question

Yes, v2 should move to a native `agent-repl` core.

No, v2 should not abandon Jupyter compatibility.

The durable architecture is:

- **canonical authority**: `agent-repl-core`
- **RTC**: core-managed live subscriptions for humans and agents
- **execution**: Jupyter kernels via runtime adapter
- **files**: `.ipynb` import/export boundary
- **UI**: VS Code as client, not authority

That is the architecture that supports:

- notebook open or closed
- VS Code open or closed
- CLI-only agents
- agent restarts and resumability
- execution continuity
- branch/sub-notebook workflows
- true multiplayer
- non-linear collaboration
- zombie-kernel prevention

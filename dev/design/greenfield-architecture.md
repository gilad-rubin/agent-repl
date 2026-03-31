# Greenfield Architecture

This document answers a specific question:

If we were starting `agent-repl` from zero today, with the benefit of what we now know, what architecture would we choose on purpose?

It is a design document, not a migration plan.

Use it to sanity-check whether the system is being built around clean seams or around historical accidents.

## Why This Doc Exists

The current design set already points in the right direction:

- the daemon should be authoritative
- Jupyter compatibility should remain important without becoming product authority
- humans and agents should be peer actors in one shared system
- the notebook surface should stop reimplementing notebook semantics where JupyterLab is already better

What this doc adds is a stricter question:

If we did not have to preserve old routes, bridge assumptions, or intermediate compatibility layers, what would we build first?

That is useful because greenfield clarity often exposes which parts of the current codebase are durable architecture and which parts are migration sediment.

## Core Thesis

The clean-slate architecture for `agent-repl` should be:

- one daemon-owned live document authority
- one daemon-owned execution authority
- one event-driven projection protocol for all clients
- one human notebook surface built on JupyterLab
- one shared agent/tool facade over the daemon

The main architectural idea is simple:

Clients do not own notebook truth.

Clients attach to notebook truth, render notebook truth, and submit explicit mutations against notebook truth.

## What We Would Keep

If starting from zero today, we would keep these positions:

- `agent-repl-core` is the source of truth
- notebook identity must be durable from creation time
- Jupyter kernels remain the primary execution backend
- `.ipynb` is a compatibility and persistence format, not the live authority
- VS Code, browser preview, CLI, and MCP are all clients of the same core
- JupyterLab should own notebook editing and rendering semantics

These are not migration compromises. They are the right long-term choices.

## What We Would Do Differently

We would not build the system around:

- polling as the default synchronization model
- special-case "visible notebook" write APIs
- UI-owned commands as the primary notebook tool contract
- repeated "mutate, then reload contents, then ask for runtime again" loops
- cell indexes as the primary durable identity
- bridge-first assumptions that make the editor feel like the system

Instead, we would make transport, authority boundaries, and client sync explicit from day one.

## One-Screen Architecture

```text
Human / Agent / System Worker
    ↕
VS Code Host / Browser Host / CLI / MCP
    ↕
Projection Protocol (bootstrap + event stream + mutation RPC)
    ↕
agent-repl Core
    ├─ Document Authority
    ├─ Execution Authority
    ├─ Collaboration Authority
    ├─ Projection Gateway
    └─ Import/Export Compatibility Layer
    ↕
YDoc live state + execution ledger + runtime records + notebook files + Jupyter kernels
```

The important property is that the client surfaces all cross the same protocol boundary.

There should not be one architectural story for VS Code, another for browser preview, and a third for agent tooling.

## Architectural Boundaries

### 1. Document Authority

The document authority owns the canonical live notebook-like state.

It is responsible for:

- cell and node identity
- ordering and structure
- source content
- notebook metadata
- trust metadata
- branch-local document state
- durable document versioning

It should be backed by a shared-model representation such as YDoc.

The file on disk exists for persistence and compatibility, but live reads and writes should conceptually target the document authority first.

### 2. Execution Authority

Execution should be a separate authority from document mutation.

It owns:

- execution intent
- queue state
- current execution state
- output streaming
- execution ledger records
- restart and interrupt semantics
- runtime health transitions

It should identify work by durable cell or node identity, not by whichever UI cell happens to be active.

This boundary matters because notebook editing and notebook execution change at different speeds and fail in different ways.

### 3. Collaboration Authority

Collaboration should be first-class, not incidental UI metadata.

It owns:

- sessions
- actors
- presence
- leases or ownership claims
- branch attribution
- review state

This authority exists to make multi-actor work understandable and recoverable.

It should never depend on the editor being open.

### 4. Projection Gateway

The projection gateway is the only client-facing synchronization model.

Its job is to translate core truth into client-friendly state and deltas.

It owns:

- attach and detach
- bootstrap snapshots
- pushed events
- version-aware reconciliation
- stale-client recovery signals

This should be a real subsystem, not a side effect of unrelated read endpoints.

### 5. Compatibility Layer

Compatibility concerns stay at the edge.

This includes:

- `.ipynb` import and export
- Jupyter metadata mapping
- notebook trust persistence
- external file-change detection

Compatibility is important, but it must not be allowed to become the center of the live architecture.

## Core Contracts

These contracts should exist from the first correct version of the system.

### Durable Identity

Every authored cell or node gets a durable ID at creation time.

The system may still expose index-based convenience in user-facing places, but indexes are a view concern, not an authority concern.

### Versioned Mutations

Mutations should be version-aware.

Clients send:

- target document
- base document version
- explicit operations
- actor and session identity

The daemon returns:

- accepted version
- applied operations
- changed entities or patches
- conflicts or recovery hints when the base version is stale

### Event Stream

Every attached client should receive pushed updates rather than discovering change by frequent polling.

The stream should carry:

- document patches
- execution state changes
- output deltas
- runtime state changes
- presence changes
- lease changes
- recovery-required events

Polling can still exist as recovery fallback, but it should not be the primary live path.

### Actor Attribution

Every meaningful action should be attributable to:

- actor type
- session
- source surface

This should be true for human edits, agent edits, execution requests, recovery actions, and review actions.

### Explicit Recovery

Disconnect, stale clients, dead runtimes, and external file drift should be normal states with explicit handling.

Recovery should produce:

- a reason
- the current authoritative version
- the minimum client action needed to continue safely

## Client Model

All clients should follow the same model:

1. Attach to a document through the projection gateway.
2. Receive one bootstrap snapshot.
3. Subscribe to pushed deltas.
4. Submit explicit mutations or execution requests.
5. Reconcile local optimistic state against authoritative acknowledgements.

The differences between clients should mostly be shell concerns.

### VS Code Host

VS Code should own:

- custom editor hosting
- workbench integration
- local commands and menus
- links to editor-specific capabilities

It should not own:

- canonical notebook state
- execution truth
- collaboration truth

### Browser Host

The browser host should expose the same projection protocol and the same notebook surface semantics.

It may differ in shell chrome, but not in authority or core notebook behavior.

### CLI

The CLI should be a first-class daemon client, not a wrapper around editor behavior.

### MCP and Agent Tooling

MCP and agent tooling should operate on daemon-owned contracts directly.

The best shape is:

- raw tools for full power
- curated bundles for common notebook tasks
- optional visible-context hints when a human-facing client is attached

Visible context is advisory context, not authority.

## Human Notebook Surface

If starting fresh, the human notebook surface should be JupyterLab-backed from the beginning.

That means JupyterLab should own:

- code cell editing behavior
- markdown editing behavior
- notebook keyboard flows
- output rendering
- trust-sensitive HTML behavior
- widget-compatible presentation

The host shell should own:

- daemon connectivity
- toolbar and explorer chrome
- collaboration overlays
- kernel and runtime controls
- product-specific review or agent affordances

This prevents the project from quietly becoming a custom notebook frontend business.

## API Shape

The public daemon shape should be small and coherent.

The first correct API family would look something like:

- `attach(document_path, client_id, session_id)` -> bootstrap snapshot
- `subscribe(client_id, cursor?)` -> event stream
- `mutate(document_path, base_version, operations, session_id)` -> mutation acknowledgement
- `execute(document_path, cell_id, session_id)` -> execution acknowledgement
- `command(document_path, action, session_id)` -> restart, interrupt, run-all, trust
- `runtime(document_path)` -> current runtime snapshot
- `context_upsert(document_path, session_id, visible_context)` -> advisory client context

The important part is not the exact route names.

The important part is that:

- bootstrap is one call
- live updates are pushed
- mutations are versioned
- execution is explicit
- client-visible context is separate from authority

## Repository Shape

If starting clean, the codebase should reflect the architecture directly.

```text
src/agent_repl/
  core/
    document/
    execution/
    collaboration/
    projection/
    compatibility/
    api/
  clients/
    cli/
    mcp/
  hosts/
    vscode/
    browser/
  ui/
    jupyterlab_surface/
    shared_shell/
```

The goal is to make the architecture visible in the directory structure.

When the tree hides the boundaries, the code will eventually hide them too.

## What We Would Refuse To Build

On a greenfield build, we should explicitly refuse these shortcuts:

- no client-owned notebook truth
- no execution model inferred from UI focus
- no mandatory editor dependency for agent work
- no index-only identity in the core protocol
- no projection-only write path that bypasses the main mutation contract
- no steady-state polling as the primary live synchronization model
- no duplicate notebook logic in separate hosts when one shared surface can exist

These refusals matter because they prevent entire classes of cleanup work later.

## Preferred Bootstrap Order

If we were building the first correct version from scratch, the dependency order should be:

1. document authority with durable IDs and versioned mutations
2. execution authority with runtime records and output streaming
3. projection gateway with bootstrap plus event stream
4. one JupyterLab-backed human notebook surface
5. CLI and MCP clients on top of the same daemon contracts
6. collaboration overlays, review flows, and richer branch semantics

This order intentionally delays product flourishes until the authority and projection seams are correct.

## How To Judge Whether We Are Drifting

The architecture is drifting if:

- a client needs full notebook reloads after routine edits
- UI state changes the meaning of core operations
- the daemon cannot explain what is running without consulting a client
- agent work becomes impossible or ambiguous with no editor attached
- the same notebook action uses materially different authority paths across surfaces
- notebook semantics are being reimplemented in host-specific code instead of delegated to JupyterLab

The architecture is healthy if:

- every client tells the same truth about the same document
- execution can outlive the client that started it
- a notebook can be opened late and attach cleanly to work already in progress
- human-visible edits feel immediate without giving up daemon authority
- agent tooling talks directly to the core model rather than to UI-specific commands

## Relationship to the Other Design Docs

This doc does not replace the rest of the design set.

Instead, it acts as a clean-slate lens on them:

- [North Star](north-star.md) defines the end-state values
- [Core Authority and Sessions](core-authority.md) defines what the center of the system owns
- [Runtime and Execution](runtime-and-execution.md) defines execution lifecycle vocabulary
- [JupyterLab-Powered Notebook Surface](jupyterlab-surface.md) defines the notebook UI posture

This document sharpens one additional question:

If we had no historical baggage at all, would we still choose this seam?

If the answer is no, the seam is probably transitional and should be treated that way.

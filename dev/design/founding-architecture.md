# Founding Architecture

This document is the final clean-slate architecture statement for `agent-repl`.

It answers a practical question:

If we were deliberately founding this project today, knowing what we now know about notebook UX, daemon authority, JupyterLab, NBI-style ergonomics, and the failure modes in the current codebase, what would we choose on purpose?

This is a design document, not an implementation plan.

Use it as the main architectural compass when deciding:

- which seams are durable
- which seams are transitional
- which shortcuts we should refuse even if they look convenient

## Status

This document is intentionally more opinionated than the broader design set.

Where other design docs preserve optionality, this one makes founding-level bets.

If a future change conflicts with this document, that conflict should be treated as a serious architecture decision, not a casual local optimization.

## Core Position

`agent-repl` should be founded on five architectural decisions:

1. `agent-repl-core` is the sole authority for notebook, execution, and collaboration truth.
2. YDoc is the primary live synchronization protocol for notebook document state.
3. JupyterLab owns notebook editing and rendering semantics in all human-facing surfaces.
4. VS Code and browser use one shared web notebook surface; CLI is the separate text client.
5. Agents interact with the daemon through curated notebook-aware tool bundles, not a flat raw tool soup by default.

Everything else should be judged against those choices.

## What We Are Building

`agent-repl` is:

- a daemon-owned live notebook system
- an execution orchestration layer
- a collaboration system for humans and agents
- a projection system for multiple client surfaces
- a compatibility layer for Jupyter notebooks and notebook workflows

It is not:

- a smarter editor bridge
- a VS Code notebook extension with extra commands
- a notebook UI pretending to be the source of truth
- a pile of host-specific notebook implementations

## Architectural Decisions

### Decision 1: Core Authority Lives In The Daemon

The daemon owns:

- document truth
- execution truth
- session and actor truth
- presence and ownership state
- runtime lifecycle
- recovery semantics

If a client, file, or kernel disagrees with the daemon, the daemon wins and reconciliation flows outward.

This is the non-negotiable center of the system.

### Decision 2: YDoc Is The Live Document Protocol

YDoc should not be merely an internal mutation helper.

It should be the primary wire-level synchronization model for live notebook document state between the daemon and attached notebook clients.

This means:

- cell edits flow through YDoc
- structure changes flow through YDoc
- metadata changes flow through YDoc
- outputs become visible through YDoc-backed document updates
- clients receive live state by subscription, not by frequent polling and reload

The daemon remains authoritative, but it is an authoritative YDoc peer, not a separate document system pretending to sync with YDoc after the fact.

### Decision 3: Execution Is A Separate Authority With A Shared Data Path

Document authority and execution authority are separate concerns.

They should remain separate because they answer different questions:

- document authority answers what the notebook is
- execution authority answers what is running, why, and under what runtime

But their client-visible data path should feel unified.

The execution authority owns:

- queue state
- execution lifecycle
- runtime lifecycle
- restart and interrupt semantics
- output capture
- execution ledger and attribution

The outputs it produces should be written into the live document path so that clients see them immediately through the same synchronization model as the rest of notebook state.

### Decision 4: There Is One Human Notebook Surface

There should be one real notebook surface for humans:

- one shared web notebook implementation
- hosted in browser
- hosted in VS Code webview
- backed by the same daemon contracts

The browser and VS Code surfaces may differ in shell chrome, but not in notebook implementation or authority path.

This eliminates an entire category of drift and host-specific notebook logic.

### Decision 5: JupyterLab Owns The Notebook Area Completely

JupyterLab should own:

- code cell editing
- markdown editing
- notebook keyboard flows
- output rendering
- trust-sensitive HTML behavior
- widget-compatible notebook behavior

The host shell should own:

- toolbar and workspace navigation
- connection state
- runtime and kernel controls
- collaboration UI
- agent affordances

The notebook area itself should not become a custom rendering business.

### Decision 6: The Primary Live Transport Is WebSocket

The primary live client connection should be one long-lived WebSocket.

That connection should carry:

- YDoc synchronization for document state
- command or RPC messages for execution and runtime actions
- presence and session updates
- recovery and invalidation notices

REST remains useful, but only for:

- health checks
- diagnostics
- import and export
- compatibility endpoints
- coarse-grained status and automation surfaces

REST should not be the main interactive notebook sync path.

### Decision 7: Execution Uses One Authoritative Path

All meaningful execution should route through one daemon-owned execution model.

This means:

- one queue model
- one attribution model
- one interrupt model
- one ledger model
- one runtime lifecycle story

We explicitly reject a founding design where human "fast path" execution and daemon execution are separate modes.

That kind of split is tempting, but it reintroduces exactly the dual-path ambiguity this architecture is trying to remove.

The right solution is to make the daemon-owned path fast enough, not to create a second path with different semantics.

### Decision 8: Tool Bundles Are A First-Class Product Surface

Agents should not face a giant flat tool list by default.

The default agent surface should be curated bundles such as:

- notebook-observe
- notebook-edit
- notebook-execute
- workspace
- collaboration

Each bundle should include domain instructions that teach the model how to operate safely and efficiently.

The raw tool surface can still exist for advanced use, but the default experience should be opinionated.

### Decision 9: Ambient Human Context Belongs In The Daemon

When a human-facing client is attached, it should push advisory context to the daemon, such as:

- active notebook
- active cell
- nearby cells
- recent execution state
- current selection or focus region

The daemon should aggregate that context and make it available to agent flows as supplemental context.

This keeps the system aware of what the human is looking at without giving authority back to the UI.

### Decision 10: Checkpoints Are A Core Primitive

The founding safety primitive should be checkpoints, not branching.

The system should make it easy to:

- capture a known notebook state before risky work
- restore that state explicitly
- inspect recent checkpoint history
- let agents recover from mistakes without inventing merge semantics on day one

This gives the product a clear safety story without forcing early investment in branch, merge, compare, and review mechanics.

## Architectural Boundaries

### Document Authority

The document authority owns:

- durable cell and node identity
- structure and ordering
- source content
- notebook metadata
- trust metadata
- branch-local variants
- document versions

The live model may be richer than `.ipynb`.

`.ipynb` compatibility matters, but the internal model is allowed to be better than the export format.

### Execution Authority

The execution authority owns:

- runtimes
- kernels as child resources of runtimes
- execution queueing
- run lifecycle
- interrupts
- restart boundaries
- execution records and provenance

Execution should always target durable cell or node identity.

It should never depend on the active widget or current cursor to know what is running.

### Collaboration Authority

The collaboration authority owns:

- sessions
- actor attribution
- presence
- ownership claims or leases
- review state

This authority exists so the system can explain who did what and who appears to own what.

### Projection Layer

Even though YDoc is the document sync protocol, there is still a projection concern.

Projection does not disappear.

It is responsible for:

- attach and detach
- authentication and authorization
- initial bootstrap
- mapping daemon state into client-friendly shells
- recovery and stale-client guidance
- delivery of non-document live state such as presence and runtime changes

The founding mistake would be to think "YDoc on the wire" means "no projection system exists."

It means the document projection becomes much cleaner.

### Compatibility Layer

Compatibility owns:

- `.ipynb` import and export
- notebook metadata normalization
- trust persistence
- external file drift detection
- Jupyter-specific compatibility mapping

Compatibility is critical, but it should stay at the edge of the system.

## Client Model

All clients should conceptually follow this lifecycle:

1. Attach to a workspace and document with explicit session identity.
2. Receive bootstrap state from the daemon.
3. Join live document synchronization.
4. Submit explicit operations and commands.
5. Receive authoritative acknowledgements and live updates.
6. Recover explicitly if they become stale or disconnected.

### Browser And VS Code

Browser and VS Code should share:

- one notebook surface implementation
- one daemon protocol
- one authority path

The VS Code extension should be intentionally thin.

It should mostly do:

- custom editor registration
- webview hosting
- VS Code command bridging where truly necessary
- workspace integration

It should not become a second notebook runtime.

### CLI

The CLI is the text-mode daemon client.

It should not depend on editor presence and should never rely on editor-owned execution or editor-owned notebook state.

### MCP

MCP should expose daemon-owned capabilities and notebook-aware tool bundles.

It should not talk to UI-owned notebook commands as its primary model.

## Founding API Posture

The exact route names are less important than the shape of the system.

The founding API posture should be:

- live interactive state over WebSocket
- YDoc synchronization for document state
- explicit commands for execution and runtime actions over the same live channel or an adjacent RPC layer
- REST for health, import/export, diagnostics, and compatibility

The system should not require routine full notebook reloads after common actions.

## Non-Decisions

These things should remain open at the founding stage:

- the precise internal schema beyond durable identity and notebook compatibility
- whether collaboration protection uses leases, lightweight ownership, or a richer collaborative model later
- how far workspace-level execution should go beyond per-notebook execution
- which inline intelligence features call models directly versus through daemon-mediated policy

These are important questions, but they do not need to be frozen before the core seams are correct.

## Red Lines

These are the things we should explicitly refuse to build into the foundation:

- no UI-owned notebook truth
- no editor-required agent path
- no steady-state polling as the main sync model
- no separate host-specific notebook implementations
- no primary core protocol based on cell indexes
- no projection-only write APIs that bypass the main mutation model
- no second execution path with distinct semantics for humans
- no giant flat tool surface as the default agent experience
- no branch-and-merge notebook workflow in the founding build

If we cross one of these lines, it should be considered architecture drift, not harmless pragmatism.

## Failure Modes This Architecture Is Designed To Prevent

This architecture exists to prevent:

- browser and VS Code showing different notebook truths
- human-visible speed requiring a separate hidden execution path
- daemon state and UI state disagreeing about what is running
- agent work becoming impossible or unsafe without an editor open
- notebook reload loops after ordinary edits
- notebook semantics being reimplemented separately in each host
- collaboration state becoming guesswork instead of explicit system state

## What Good Looks Like

The founding architecture is working when:

- one client edits and another client sees the change immediately
- one client runs a cell and all clients see outputs appear live
- a notebook can be opened late and attach to current truth cleanly
- an agent can do real notebook work with no editor open
- human-visible interaction feels immediate without hiding a second authority path
- the system can always explain what is running, who started it, and where its outputs live
- the notebook area behaves like JupyterLab rather than like a custom imitation

## First Build Order

If we were building the first correct version from zero, the order should be:

1. YDoc-backed document authority with durable IDs
2. WebSocket live sync with daemon as authoritative peer
3. Execution authority with runtime lifecycle and output capture into live document state
4. Shared JupyterLab notebook surface for browser and VS Code webview
5. CLI and MCP clients over the same daemon contracts
6. Curated tool bundles and ambient context injection
7. Checkpoints, explicit restore flows, and lightweight collaboration affordances

This order intentionally front-loads the authority and sync seams.

## Relationship To The Other Design Docs

This is the most opinionated architecture statement in the design set.

Read it together with:

- [North Star](north-star.md)
- [Core Authority and Sessions](core-authority.md)
- [Runtime and Execution](runtime-and-execution.md)
- [JupyterLab-Powered Notebook Surface](jupyterlab-surface.md)
- [Greenfield Architecture](greenfield-architecture.md)

Use this document when you need a founding-level answer to:

What would we choose if we were optimizing for correctness and long-term simplicity rather than for preserving migration history?

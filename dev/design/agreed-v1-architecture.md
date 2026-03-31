# Agreed V1 Architecture

This document defines the architecture we currently agree to build first.

It is narrower than the broader design set and more practical than the founding memo.

It is based on these product requirements:

- any human or agent must be able to work through CLI or MCP with no UI open
- if a UI is already open or opens mid-execution, it should reflect current notebook state and, when possible, live in-progress cell output
- UI actions must go through the exact same mutation and execution path as CLI and MCP
- if a notebook is already executing and another client asks to run a cell in that notebook, the request should queue instead of inventing a second execution path
- multiple notebooks in the same workspace must work cleanly under one workspace daemon
- the notebook surface should preserve the normal JupyterLab experience, including shortcuts, undo and redo, cell copy and paste, search within a notebook, restart, run all, clear outputs, and other expected notebook affordances

This is a design document, not an implementation plan.

## Scope

This document is intentionally focused on the first correct version of the product.

It does not attempt to solve every future collaboration, review, or workflow question.

Its purpose is to prevent us from overbuilding the foundation while still choosing the right seams.

## Core Product Commitments

The first version of `agent-repl` should commit to these product truths:

1. The daemon is the real notebook and execution authority.
2. UI is optional.
3. The UI is a live projection of daemon truth, not a second notebook system.
4. All execution in a notebook goes through one daemon-owned queue and runtime model.
5. JupyterLab owns notebook behavior inside the notebook area.

Everything in this document follows from those five commitments.

## What We Are Building

The agreed v1 system has these major parts:

- one workspace-scoped daemon -- Implemented
- one live notebook document model per notebook -- Implemented (YDoc + nbformat)
- one runtime and execution queue model per notebook or shared-runtime policy context -- Implemented (execution ledger + daemon queue)
- one shared web notebook surface used in both browser and VS Code webview -- Implemented (JupyterLab-backed)
- one CLI client -- Implemented
- one MCP surface for agents -- Implemented (6 bundled tools)
- one compatibility layer for `.ipynb` persistence and import/export -- Implemented

## What We Are Explicitly Not Building In V1

To keep the foundation clean, we are explicitly not making these part of v1:

- branch and merge notebook workflows
- rich review workflows
- heavy collaboration UI beyond basic sessions and simple presence
- lease-heavy conflict systems unless real product pressure appears
- workspace-level orchestration across notebooks beyond independent multi-notebook support
- direct UI-to-LLM inline intelligence paths as a foundational requirement
- a giant flat default MCP tool surface -- Enforced: MCP uses 6 bundled tools, not flat
- any separate VS Code execution engine -- Enforced: all execution routes through daemon
- polling as the primary synchronization model -- Enforced: WebSocket is the only sync transport

## High-Level Architecture

```text
Human / Agent
    ↕
Browser UI / VS Code webview / CLI / MCP
    ↕
Workspace Daemon
    ├─ Document authority
    ├─ Execution authority
    ├─ Runtime manager
    ├─ Session and simple presence
    ├─ YDoc live sync
    └─ ipynb compatibility
    ↕
YDoc state + SQLite metadata/ledger + Jupyter kernels + .ipynb files
```

## Authority Model

### Daemon Authority -- Implemented

The daemon owns:

- notebook truth -- Implemented (YDoc + CoreState)
- execution truth -- Implemented (execution ledger, daemon-routed queue)
- runtime lifecycle -- Implemented (headless kernels)
- session identity -- Implemented (collaboration service)
- queue state -- Implemented (execution ledger service)
- checkpoints -- Implemented (CheckpointService, SQLite-backed)
- compatibility mapping to and from `.ipynb` -- Implemented

If the UI, CLI, or an external file view disagrees with the daemon, the daemon wins.

### UI Authority

The UI owns nothing canonical.

It owns:

- rendering
- local interaction state
- JupyterLab notebook behavior inside the notebook area
- shell chrome such as toolbar, explorer, and status presentation

Its job is to attach to daemon truth and present it well.

### CLI and MCP Authority

CLI and MCP are first-class daemon clients.

They should never require a UI and should never depend on UI-owned state or execution.

## Document Model -- Implemented

Each notebook has a live document model backed by YDoc.

That model owns:

- stable cell identity
- cell ordering
- cell source
- cell metadata
- outputs as shared notebook state
- notebook metadata

YDoc is not just an internal mutation helper.

It is the primary live synchronization mechanism for notebook state between the daemon and attached notebook clients.

## Execution Model -- Implemented

Each notebook executes through one daemon-owned execution path.

This means:

- one queue story
- one runtime story
- one interrupt story
- one attribution story
- one execution ledger story

If a cell is already running in a notebook and another client submits a run request in that notebook, the new request queues under the same notebook execution model.

This must be true regardless of whether the request came from:

- browser UI
- VS Code webview
- CLI
- MCP

We explicitly reject a split between "human fast path" execution and "daemon execution" for v1.

The right v1 architecture is one path that everyone uses.

## Live Sync Model -- Implemented

The live path is push-based.

The primary live transport is a WebSocket connection that carries:

- YDoc synchronization for notebook state
- daemon-driven execution and runtime updates
- session and simple presence updates
- recovery or invalidation notices

Polling has been removed. WebSocket is the only sync transport. HTTP polling was deleted in the v1 modernization.

## Multiple Notebooks In One Workspace -- Implemented

The workspace daemon supports many notebooks at once.

That means:

- multiple notebooks can be opened independently
- multiple notebooks can be edited independently
- multiple notebooks can execute independently, subject to runtime policy
- a UI can attach to one notebook while CLI or MCP works on another
- no single open notebook should become special merely because it is visible

This requirement is different from advanced workspace orchestration.

V1 must support many notebooks cleanly.

V1 does not need to understand notebook dependency graphs or pipeline semantics.

## UI Surface -- Implemented

The human notebook area is a JupyterLab-powered notebook surface.

JupyterLab should own:

- code and markdown cell editing
- notebook shortcuts and command behavior
- undo and redo behavior
- copy and paste of cells
- rich output rendering
- search within a notebook
- restart, run all, clear outputs, and normal notebook actions
- trust-sensitive notebook behavior

Search within a notebook should ideally use built-in JupyterLab behavior rather than a custom search implementation.

The host shell around the notebook should own:

- daemon connection and attach state
- kernel and runtime status presentation
- toolbar framing
- explorer and notebook navigation
- simple collaboration indicators

## Sessions And Presence -- Implemented

V1 models sessions explicitly.

We need enough to know:

- who is connected
- which notebook they are attached to
- whether they are a human or an agent
- basic activity such as observing or executing

That is enough for v1.

We do not need complex ownership or review mechanics to satisfy the current product requirements.

## Checkpoints -- Implemented

V1 has checkpoints instead of branching.

Checkpoints exist so we can:

- capture notebook state before risky work
- restore a prior state explicitly
- give agents a safe fallback when they make a mistake

This provides a simple safety primitive without introducing branch and merge complexity too early.

## CLI Surface -- Implemented

The CLI remains friendly and ergonomic for humans.

We should keep human-facing convenience commands when they are useful, even if they are not the canonical internal capability names.

For v1, the CLI surface should be treated in three groups.

### Core Notebook Commands

These are part of the main product story and should remain supported:

- `cat`
- `status`
- `edit`
- `exec`
- `ix`
- `run-all`
- `restart`
- `restart-run-all`
- `new`
- `open`
- `kernels`
- `select-kernel`

### Admin And Dev Commands

These should remain available, but they are not part of the core notebook capability model:

- `setup`
- `doctor`
- `mcp`
- `editor`
- `reload`

### Deferred From The Foundational V1 Story

These may continue to exist if needed, but they should not define the architecture or the default product narrative:

- `prompts`
- `respond`

### CLI Design Rule

CLI commands may be friendly aliases or shortcuts.

They do not need to be the canonical architecture vocabulary.

For example:

- `cat` is a convenient human alias for notebook observation
- `ix` is a convenient human alias for insert-and-execute behavior
- `run-all` is a convenient human alias for notebook-wide execution

These are good CLI ergonomics.

They should not force the MCP or core capability model to adopt the same names.

## MCP Surface -- Implemented

The default MCP surface is compact and notebook-aware.

The detailed MCP design rules for this architecture live in
[../mcp_dos_and_donts.md](../mcp_dos_and_donts.md).

It should expose a small set of high-value capabilities such as:

- observe notebook state
- edit notebook structure and source
- execute notebook cells and notebook-wide commands
- inspect runtime and queue state
- create and restore checkpoints
- find notebooks in the workspace

The default agent experience should be bundled and opinionated.

The model should get a manageable operating manual, not a phone book of tiny tools.

### MCP Design Rule

The MCP surface should not mirror the CLI one command at a time.

For v1, the default MCP and agent-facing capability model should be bundle-based:

- `notebook-observe`
- `notebook-edit`
- `notebook-execute`
- `notebook-runtime`
- `workspace-files`
- `checkpoint`

These bundles are the canonical agent-facing capabilities.

They may internally power friendly CLI commands such as `cat`, `exec`, or `ix`, but the MCP surface should not make agents learn that ad hoc CLI vocabulary.

### CLI To MCP Mapping

The intended relationship is:

- `cat` maps to `notebook-observe`
- `edit` maps to `notebook-edit`
- `exec` maps to `notebook-execute`
- `ix` maps to `notebook-edit` plus `notebook-execute`
- `run-all` maps to `notebook-execute`
- `restart` and `restart-run-all` map to `notebook-runtime` plus `notebook-execute`
- `kernels` and `select-kernel` map to `notebook-runtime`

This preserves human-friendly CLI ergonomics without turning the MCP surface into a bag of loosely related command names.

## Backend Storage -- Implemented

The daemon uses:

- YDoc for live notebook state
- SQLite for small durable metadata such as sessions, runtime records, execution ledger, and checkpoints
- `.ipynb` files for persistence and compatibility

The `.ipynb` file is important, but it is not the live source of truth during normal operation.

## Human-Focused Acceptance Criteria

These are the product-facing checks that should be true before we call v1 coherent.

- A human can open a notebook in browser or VS Code and see the current daemon-owned notebook state without needing to start the notebook from the UI first.
- A human can edit a notebook in the UI, and those edits go through the same daemon mutation path used by CLI and MCP.
- A human can run a cell in the UI while another cell in that notebook is already running from CLI or MCP, and the new request appears as queued rather than taking a separate path.
- A human can open the UI in the middle of a long-running cell execution and, when the kernel emits incremental output, see that output appear live.
- A human can use the notebook with normal JupyterLab behavior for shortcuts, undo and redo, copy and paste of cells, search within the notebook, restart, run all, and clear outputs.
- A human can work in one notebook while another notebook in the same workspace is being edited or executed by CLI or MCP with no special breakage.
- A human can close the UI and later reopen it without losing the shared notebook or execution truth.
- A human can restore a prior checkpoint explicitly after risky work.

## Backend-Facing Acceptance Criteria

These are the architecture and systems checks the backend must satisfy.

- The daemon can read, edit, and execute notebooks with no UI process running.
- Every notebook mutation from UI, CLI, and MCP enters the same core mutation path.
- Every notebook execution request from UI, CLI, and MCP enters the same queue and runtime model for that notebook.
- The live notebook state is synchronized through YDoc over a push-based connection rather than through polling-first full snapshot reloads.
- The daemon can attach a UI client to a notebook that is already mid-execution and deliver current document state plus live execution updates.
- Multiple notebooks in the same workspace can exist simultaneously with independent live document state and runtime state.
- The backend persists enough state to recover sessions, runtime records, execution ledger data, and checkpoints without treating the UI as authoritative.
- `.ipynb` persistence remains compatible with normal notebook workflows while the live daemon state stays authoritative during active work.
- The backend exposes a small, notebook-aware MCP surface rather than requiring agents to operate on a large flat list of low-level notebook tools by default.

## How To Evaluate Drift

We are drifting away from the agreed design if:

- the UI starts to require a separate execution engine
- notebook actions behave differently depending on whether they came from UI or CLI
- the system relies on polling loops for normal notebook synchronization
- visible notebooks become special in a way that changes daemon semantics
- multiple notebooks in a workspace stop feeling like normal first-class documents
- we start rebuilding notebook UX that JupyterLab already knows how to do well

We are aligned with the agreed design if:

- the daemon remains the only authority
- the UI remains optional
- the UI remains a faithful live projection
- execution remains unified
- multi-notebook workspace support remains normal
- the notebook surface feels like JupyterLab rather than a partial imitation

## Relationship To The Other Design Docs

This document is the narrow agreed-upon v1 target.

Read it alongside:

- [Founding Architecture](founding-architecture.md)
- [Greenfield Architecture](greenfield-architecture.md)
- [North Star](north-star.md)
- [JupyterLab-Powered Notebook Surface](jupyterlab-surface.md)

Use this document when the question is not "what could exist eventually?" but rather:

What are we actually agreeing to build first?

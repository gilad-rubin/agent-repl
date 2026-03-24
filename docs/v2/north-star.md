# agent-repl North Star

## Purpose

This document describes the target architecture for `agent-repl`.

It defines:

- the kind of system we want to build
- the boundaries we want to preserve
- the user experience we want for humans and agents
- the principles we should keep protecting as the code evolves

It does not describe phases or implementation steps.

## Vision

`agent-repl` should be a local-first, Jupyter-compatible shared notebook runtime where humans and agents work against the same canonical live state.

It should feel:

- collaborative
- durable
- recoverable
- explicit
- compatible with notebook workflows without being trapped by notebook file semantics

## Core Position

The clean architecture is:

- `agent-repl-core` owns live document authority
- Jupyter kernels are the primary execution backend
- `.ipynb` is a compatibility and export format
- VS Code and CLI are clients of the shared runtime
- humans and agents are peer actors in the same system

## What agent-repl Is

`agent-repl` is:

- a canonical notebook runtime
- a collaboration system for humans and agents
- an execution orchestration layer
- a projection system for editor and file surfaces
- a recovery-friendly stateful service

## What agent-repl Is Not

`agent-repl` is not:

- a smarter VS Code bridge
- a live `.ipynb` watcher with extra commands
- a notebook UI pretending to be the source of truth
- a Deepnote clone
- a Jupyter plugin treated as the whole product

## Foundational Principles

### One source of truth

There must be one canonical live document state.

That source of truth belongs to `agent-repl-core`, not:

- the notebook file on disk
- the notebook currently open in VS Code
- the currently loaded extension build
- the kernel process

### Durable identity from birth

Every authored node must get a durable ID at creation time.

The system should never depend on path-specific fallback identity such as positional cell IDs that work on one route but not another.

### Explicit operations

The system should reason in terms of operations and events rather than hidden mutation.

Edits, execution requests, merges, imports, exports, and recovery transitions should all be expressible in a durable way.

### Humans and agents are first-class peers

Humans and agents are different clients of the same shared runtime.

They are not separate coordination systems glued together after the fact.

### Compatibility without authority

Jupyter and `.ipynb` stay important, but they must remain adapters and compatibility layers rather than the center of product truth.

### Recovery is a product property

Restarts, disconnects, stale sessions, orphaned runtimes, and external file changes are normal conditions.

The architecture should make them understandable and recoverable.

## Desired Experience

### Human in VS Code

The human should be able to:

- open a notebook and see the latest shared state
- edit while an agent is also working
- see what is running and who changed what
- close VS Code and later continue without hidden drift
- review risky agent work before it lands in the main line

### Agent from the CLI

The agent should be able to:

- attach with no editor open
- read, edit, branch, and execute against the same authority
- recover after process restart
- leave work in a resumable state for a later agent or human

### Mixed collaboration

The system should make it natural for:

- a human to hand off work to an agent
- an agent to work in a scoped branch or owned region
- another agent to continue that work
- a human to review and merge or reject

## Actors

The architecture should explicitly model:

- `human`
- `agent`
- `system`

The `system` actor includes recovery workers, import/export workers, runtime reapers, and sync processes.

Every meaningful action should be attributable to one of these actors.

## Canonical Concepts

The architectural vocabulary should remain stable:

- **workspace**: trust boundary, root path, policies, runtimes
- **document**: canonical shared notebook-like state
- **node**: authored unit within the document
- **branch**: alternate line of work
- **session**: one connected actor instance
- **run**: an execution request and its lifecycle
- **runtime**: the compute process bound to a document or branch
- **projection**: a client-friendly representation of canonical state

## Document Model

The canonical model must be richer than `.ipynb` when needed.

It should comfortably support:

- code cells
- markdown cells
- prompt and response nodes
- sections
- agent-task nodes
- sub-notebook references
- branch-local structures
- future review or result-oriented nodes

The important architectural point is not the precise schema.

The important point is that the internal model is allowed to be richer than the compatibility format.

## Projection Model

Clients do not own state. They consume projections.

Examples:

- VS Code notebook projection
- `.ipynb` export/import projection
- review projection
- branch-scoped projection

This gives us freedom to preserve notebook ergonomics without forcing the internal model to stay linear.

## Collaboration Model

The collaboration model should support both humans and agents in real time through a server-authoritative shared state model.

It should naturally support:

- subscriptions
- presence
- actor attribution
- conflict-safe concurrent work
- reviewable risky edits

## Authority Boundaries

### The core owns

- document truth
- identity
- operation history
- branch state
- actor attribution
- leases and ownership
- execution intent
- run state

### The runtime layer owns

- actual code execution
- runtime health
- kernel/session state

### The file layer owns

- import/export
- compatibility mapping
- external file-change detection

### The client layer owns

- presentation
- interaction ergonomics
- local editing affordances

Clients must never become hidden authorities.

## Execution Model

Execution should be part of the shared runtime, not an accidental consequence of which editor is open.

That means:

- runs are explicit
- outputs belong to runs and nodes
- execution state persists across client disconnects
- clients reconnect to execution state instead of rediscovering it heuristically

## Runtime Model

Runtime ownership should be policy-driven rather than editor-driven.

The architecture should support:

- interactive runtimes
- shared runtimes
- headless runtimes
- pinned runtimes
- ephemeral runtimes

A runtime belongs to a document or branch context, not to a random UI tab.

## Zombie Kernel Philosophy

Zombie kernels are evidence that ownership is unclear.

The architecture should make them rare by construction through:

- explicit runtime ownership
- attachment and lease rules
- heartbeats
- activity tracking
- reaping policy
- durable run bookkeeping

The system should always be able to explain why a runtime exists.

## File Compatibility Philosophy

`.ipynb` remains a first-class compatibility format, but it is not rich enough to be the complete product model.

That means:

- `.ipynb` must remain useful
- `.ipynb` should not be the live authority
- richer v2 semantics may require sidecar or core-owned persistence beyond the exported notebook

## Editor Philosophy

VS Code should be an excellent client, not the source of truth.

That means:

- closing VS Code does not end the life of the document
- extension reloads should not threaten notebook integrity
- CLI-only operation is first-class
- notebook continuity must not depend on UI presence

## Branching Philosophy

Branches should be native notebook collaboration tools, not awkward file-copy substitutes.

They should support:

- risky changes
- parallel agent work
- reviewable experiments
- explicit merge or rejection

## Sub-Notebook Philosophy

The architecture should support notebook-like structures recursively when useful.

This enables:

- delegation
- scoped work
- intermediate review surfaces
- agent task isolation

## What Good Looks Like

At maturity, the following should be true:

- CLI, VS Code, and future clients see the same notebook truth
- `cat`, `edit`, and `exec` all hit the same authority
- humans and agents can collaborate without silent stomping
- restart and reconnect paths are understandable
- `.ipynb` remains useful without constraining the internal model
- branching and sub-notebooks feel native rather than improvised

## Architecture Smells

Treat these as warning signs:

- more than one live source of truth
- IDs that depend on which route read the notebook
- runtime truth inferred from editor behavior
- file format treated as product authority
- notebook copies used as fake branches
- agent work that cannot be resumed or attributed
- runtime cleanup that depends on UI closure

## Final Statement

The system we want is:

`agent-repl` as a local-first, Jupyter-compatible, server-authoritative notebook runtime where humans and agents collaborate on the same durable document model through clients like VS Code and CLI, with explicit execution state, native branch and sub-notebook workflows, and compatibility layers that serve the product rather than constrain it.

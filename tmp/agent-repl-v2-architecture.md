# agent-repl v2 Architecture North Star

## Purpose

This document describes the desired end-state architecture for `agent-repl` v2.

It is intentionally not an implementation plan.

It exists to answer:

- what system we are trying to build
- what should be authoritative and what should not
- what kinds of workflows must feel natural
- what external systems we should borrow from or compare against
- how we should evaluate progress after each run

We should use this document as a standing comparison target.

After every meaningful build, spike, or refactor, we should be able to ask:

- are we getting closer to this architecture
- are we accidentally reintroducing v1-style dual authority
- are we improving the right things, not just patching symptoms

## Vision

`agent-repl` v2 should feel like a shared notebook runtime where humans and agents work on the same live document model, with durable identity, recoverable execution, and clean compatibility with Jupyter and `.ipynb`.

It should feel:

- local-first
- agent-native
- multiplayer-ready
- Jupyter-compatible
- editor-friendly without being editor-owned

The system should support linear notebook use, but it should not be trapped by the linear notebook abstraction.

## What v2 Is

`agent-repl` v2 is:

- a canonical notebook runtime
- a shared state system for humans and agents
- a durable execution orchestration layer
- a compatibility bridge to Jupyter kernels and `.ipynb`
- a projection engine for clients like VS Code

## What v2 Is Not

`agent-repl` v2 is not:

- a thin wrapper around VS Code notebook state
- a smarter `.ipynb` file watcher
- a collection of bridge routes with no core authority
- a Deepnote replacement product
- a JupyterLab plugin pretending to be the system of record

## Core Architectural Position

The cleanest long-term architecture is:

- **authority**: `agent-repl-core`
- **execution backend**: Jupyter kernels
- **file compatibility**: `.ipynb`
- **client surfaces**: VS Code first, other clients later
- **collaboration model**: live shared state for humans and agents

Everything else should orbit that.

## Foundational Principles

### One source of truth

There must be one canonical live document state.

That authority belongs to `agent-repl-core`, not:

- the file on disk
- the currently open VS Code notebook
- the installed extension build
- the kernel process

### Durable identity from birth

Every node must have a durable ID at creation time.

There should never be fallback identities that only exist on one path, such as positional IDs that work for reads but not writes.

### Operations before mutation

The system should think in terms of explicit operations, not ad hoc mutations.

Edits, runs, merges, imports, exports, and lease changes should be representable as meaningful events in the system.

### Humans and agents are first-class peers

Humans and agents should be different kinds of clients of the same runtime, not separate systems glued together.

### Compatibility without subordination

Jupyter and `.ipynb` remain important, but they should be adapters and compatibility layers, not the center of the system.

### Recoverability is part of the product

Restarts, disconnects, stale clients, and orphaned runtimes are normal states, not edge cases.

## Desired User Experience

### For a human in VS Code

The human should be able to:

- open a notebook and see the latest shared state
- edit while an agent is also working
- see what changed, who changed it, and what is still running
- close VS Code and come back later without losing the live state
- review agent work without being surprised by silent overwrites
- branch, compare, and merge without duplicating notebook files manually

### For an agent using only the CLI

The agent should be able to:

- attach to a workspace with no editor open
- read, edit, branch, execute, and inspect state through one authority
- recover from process restart and continue prior work
- leave work in a resumable state for another agent or a human
- operate on notebook-like structures that are richer than a plain linear cell list

### For a mixed human-and-agent workflow

The system should make it natural for:

- a human to hand off work to an agent
- an agent to work in a scoped branch or owned region
- another agent to continue that work later
- a human to review and merge or reject the result

### For long-running or background work

The system should allow:

- runs to continue even if the initiating client disconnects
- state and output to remain visible to later clients
- interrupted work to be inspectable and recoverable

## Actors

The architecture should treat these as explicit actors:

- `human`
- `agent`
- `system`

The `system` actor includes:

- import/export workers
- recovery and replay workers
- runtime reapers
- background sync processes

Every meaningful action should be attributable to an actor.

## System Shape

The system should be understood as five layers:

1. **Core authority**
   The canonical document, branch, run, and presence state.

2. **Runtime layer**
   Execution backends, especially Jupyter kernel/session management.

3. **Compatibility layer**
   Import/export and projection for `.ipynb` and other notebook-like formats.

4. **Client layer**
   VS Code, CLI, and later browser or automation clients.

5. **Observation layer**
   History, inspection, replay, diff, merge, and debugging surfaces.

These layers should be cleanly separated.

## Canonical Concepts

The system should center around a few durable concepts:

- **workspace**: trust boundary, files, policies, runtimes
- **document**: canonical notebook-like shared object
- **node**: the fundamental authored unit
- **branch**: alternate line of work
- **session**: one connected client identity
- **run**: execution request and result stream
- **runtime**: compute process bound to a document or branch
- **projection**: a client-friendly representation of the canonical state

These concepts should remain stable even if their internal representation changes later.

## Document Model

The document model should support notebooks as they are today and notebooks as they will need to become later.

That means it should be able to represent:

- code cells
- markdown/text cells
- prompt and response nodes
- agent task nodes
- sections
- sub-notebook references
- branch markers or branch-local views
- richer future nodes such as review threads or result panels

The important point is not the exact schema.

The important point is that the canonical model is allowed to be richer than `.ipynb`.

## Projection Model

Clients should not own state. They should consume projections.

Examples:

- VS Code notebook view: a linear notebook projection
- `.ipynb`: a compatibility projection for file exchange
- review view: a diff-oriented projection
- branch view: a scoped projection over part of the document graph

This gives us a way to support richer internal state without losing editor usability.

## Collaboration Model

The collaboration model should support both humans and agents in real time.

This means:

- shared live state
- subscriptions and updates
- presence
- actor attribution
- conflict-safe concurrent work

It does not necessarily mean peer-to-peer or client-authoritative collaboration.

The preferred model is server-authoritative shared state.

## Authority Boundaries

These boundaries should remain true:

### The core owns

- document truth
- identity
- operation history
- branch state
- leases and ownership
- execution intent
- run state
- attribution and presence

### The runtime owns

- actual code execution
- runtime health
- kernel/session state

### The file layer owns

- import/export
- external file-change detection
- compatibility mappings

### The client owns

- presentation
- interaction ergonomics
- local editing affordances

Clients must not become hidden authorities.

## Execution Model

Execution should feel like part of the shared runtime, not a side effect of whatever editor happens to be open.

That implies:

- runs are explicit and attributable
- outputs belong to runs and nodes
- execution state survives client disconnects
- clients reconnect to execution state rather than re-infer it
- the system can distinguish queued, running, completed, interrupted, failed, and stale work

The runtime layer should be pluggable in principle, but the architecture should assume Jupyter kernels first.

## Runtime Model

Runtimes should be bound by policy, not by UI accidents.

The architecture should naturally support:

- interactive runtimes
- shared runtimes
- headless runtimes
- pinned runtimes
- ephemeral runtimes

A runtime should belong to a document or branch policy context, not to a random notebook tab.

## Zombie Kernel Philosophy

Zombie kernels are not just bugs.

They are evidence that runtime ownership is unclear.

The architecture should make zombie kernels rare by construction through:

- explicit runtime ownership
- explicit lease and attachment rules
- heartbeats and activity tracking
- headless continuation states
- reaping policy
- durable run bookkeeping

If a runtime dies, the system should say so clearly.
If a runtime lives on, the system should know why.

## File Compatibility Philosophy

`.ipynb` should remain a first-class compatibility format.

But it should not be forced to carry the full semantic weight of v2.

The architecture should be comfortable with this truth:

- `.ipynb` is useful and necessary
- `.ipynb` is not rich enough to be the whole product model

That means full-fidelity v2 state may require sidecar or core-owned persistence beyond the exported notebook file.

## Editor Philosophy

VS Code should be an excellent client, not the source of truth.

This is a major architectural choice.

It means:

- closing VS Code should not end the life of a document or runtime
- the extension should project and manipulate shared state, not invent it
- CLI-only operation should be first-class
- extension reloads should not be existential events for notebook integrity

## Branching Philosophy

Branches should be native collaboration tools, not awkward notebook copies.

They should exist because the architecture expects:

- risky edits
- agent-owned parallel work
- merge review
- exploration that should not immediately land in the main line

Branching should feel like a normal collaborative notebook primitive.

## Sub-Notebook Philosophy

Sub-notebooks should be possible because some work deserves its own scoped surface.

This supports:

- decomposition
- delegation
- agent task scoping
- reviewable intermediate work

The system should be able to express notebook-like structure recursively when useful.

## Continuity Model

The system should be resilient to ordinary interruptions.

The desired outcomes are:

- if VS Code closes, work persists
- if the CLI process exits, work persists
- if an agent restarts, it can resume
- if the daemon restarts, state is reconstructed and reconciled
- if a kernel dies, runs are marked explicitly and recoverably

The architecture should make “continue where we left off” a default property, not a heroic recovery maneuver.

## Interactive Feel

Even though the core is server-authoritative, the product should feel live and fluid.

It should feel like:

- edits appear promptly
- outputs stream in naturally
- presence is understandable
- ownership is visible
- long-running work is legible
- agent activity feels collaborative rather than spooky

## External References

These are the most relevant reference points for the architecture.

### Strong building-block references

- [jupyter-server-ydoc](https://pypi.org/project/jupyter-server-ydoc/)
- [jupyter-ydoc](https://pypi.org/project/jupyter-ydoc/)
- [jupyter-docprovider](https://pypi.org/project/jupyter-docprovider/)
- [pycrdt-websocket](https://pypi.org/project/pycrdt-websocket/)
- [pycrdt-store](https://pypi.org/project/pycrdt-store/)
- [nextgen-kernels-api](https://pypi.org/project/nextgen-kernels-api/)
- [jupytext](https://jupytext.readthedocs.io/en/latest/jupyterlab-extension.html)
- [nbdime](https://nbdime.readthedocs.io/)

### Product and UX references

- [Jupyter AI](https://jupyter-ai.readthedocs.io/en/latest/)
- [Jupyter AI Agents](https://jupyter-ai-agents.datalayer.tech/docs/agents/)
- [Notebook Intelligence](https://github.com/notebook-intelligence/notebook-intelligence)
- [VS Code notebook AI flows](https://code.visualstudio.com/docs/copilot/guides/notebooks-with-ai)
- [Deepnote Agent](https://deepnote.com/docs/deepnote-agent)
- [Datalore AI Assistant](https://www.jetbrains.com/help/datalore/ask-ai.html)

### Collaboration and governance references

- [Jupyter RTC docs](https://jupyterlab.readthedocs.io/en/4.4.x/user/rtc.html)
- [Jupyter RTC architecture](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/developer/architecture.html)
- [JupyterHub collaboration accounts](https://jupyterhub.readthedocs.io/en/4.x/tutorial/collaboration-users.html)

### Nearby internal design references

- [hypergraph architecture](/Users/giladrubin/python_workspace/hypergraph/dev/ARCHITECTURE.md)
- [hypster core](/Users/giladrubin/python_workspace/hypster/src/hypster/core.py)
- [agent-repl v2 plan](/Users/giladrubin/python_workspace/agent-repl/tmp/agent-repl-v2.md)

## Reference Posture

We should use outside systems in four different ways:

### Adopt

Use directly when a component cleanly solves part of the problem without undermining authority boundaries.

Likely examples:

- shared-model and persistence primitives from the `ydoc` and `pycrdt` family
- notebook diff and review support from `nbdime`
- text-friendly notebook pairing from `jupytext`

### Prototype Against

Spike against a component to learn whether it fits the architecture cleanly.

Likely examples:

- `nextgen-kernels-api`
- selected `ydoc`-family pieces

### Study

Use as product or architecture references, not as direct dependencies.

Likely examples:

- Jupyter AI
- Jupyter AI Agents
- Notebook Intelligence
- Deepnote Agent
- Datalore AI

### Avoid as Foundation

Do not outsource the system’s core identity to them.

This includes:

- VS Code notebook state
- raw `.ipynb` as live authority
- Deepnote as the v2 platform foundation

## Deepnote Position

Deepnote is valuable as a product benchmark and as a possible source of ideas around:

- richer notebook formats
- conversion
- local runtimes
- agent tooling

But it should not become the foundation of `agent-repl` v2.

The reasons are architectural:

- it does not cleanly map to the local `.ipynb` plus VS Code plus shared-runtime problem we are solving
- its strongest public surface is not the same thing as a fully open, self-hostable authority layer for our use case
- switching would be closer to a product rewrite than a clean platform substitution

## Architecture Smells

If we see any of these, we should treat them as warning signs:

- more than one live source of truth
- IDs that depend on which path read the notebook
- runtime truth inferred from editor behavior
- clients that can mutate canonical state without explicit operations
- file export format acting as the product model
- agent work that cannot be resumed or attributed
- notebook copies used as fake branches
- runtime cleanup that depends on UI tab closure

## What Good Looks Like

At maturity, the system should make these statements true:

- the same notebook state is visible from CLI, VS Code, and any future client
- `cat`, `edit`, and `exec` all operate against the same authority
- the user can close and reopen tools without corrupting notebook progress
- agents can work independently without silently trampling human work
- branching and sub-notebooks feel native
- Jupyter compatibility is strong without constraining the internal model
- the system is easier to reason about than v1, not more clever

## How We Should Evaluate Every Run

After each meaningful run, spike, or feature slice, compare the result against this document in four passes.

### Pass 1: Authority

Ask:

- what is the source of truth in this slice
- did we accidentally let the client or file become authoritative again
- are IDs and edits coherent across all entry points

### Pass 2: Collaboration

Ask:

- does this slice make human-agent collaboration more explicit and safer
- is attribution visible
- are branches, leases, or ownership clearer than before

### Pass 3: Runtime

Ask:

- is execution state more authoritative and less heuristic
- are restart and reconnect stories better
- are zombie-kernel outcomes clearer and more controlled

### Pass 4: Compatibility

Ask:

- did we preserve Jupyter and `.ipynb` usefulness
- did we avoid making compatibility layers the product center
- did we keep the architecture open to richer internal structure

## Interactive Comparison Scenarios

These scenarios are the recurring interactive checks we should use while evolving the system.

### Notebook lifecycle

- notebook created from CLI only
- notebook created from VS Code only
- notebook opened in VS Code
- notebook edited and then closed
- notebook saved
- notebook overwritten externally
- notebook exported to `.ipynb`
- notebook re-imported

Desired test question:

- does the canonical state remain coherent and explainable the whole time

### Client lifecycle

- VS Code opens
- VS Code closes
- extension reloads
- CLI connects
- CLI disconnects
- agent process restarts
- daemon restarts

Desired test question:

- can a later client continue confidently from the current state without hidden drift

### Mixed collaboration

- human edits while agent edits elsewhere
- human edits while agent edits the same logical region
- agent works in a branch
- second agent continues the first agent’s task
- human reviews and merges or rejects

Desired test question:

- is the collaboration model legible, attributable, and conflict-safe

### Execution lifecycle

- run one node
- run a linear range
- run a branch
- disconnect during execution
- reconnect during execution
- runtime restart
- runtime crash
- detached runtime cleanup

Desired test question:

- is execution state authoritative and recoverable, not guessed

### Recovery and resilience

- kill the client and reconnect
- kill the daemon and recover
- leave a runtime orphaned
- introduce an external file change
- leave work idle and come back later

Desired test question:

- can the system explain what happened and continue without ambiguity

## What We Are Optimizing For

The architecture should optimize for:

- clarity
- stability
- recoverability
- extensibility
- collaboration
- local-first ergonomics

It should not optimize for:

- preserving every v1 assumption
- minimizing conceptual change
- hiding authority boundaries under convenience APIs
- making VS Code feel magical by making the architecture brittle

## Final North-Star Statement

The end-state system we want is:

`agent-repl` as a local-first, Jupyter-compatible, server-authoritative notebook runtime where humans and agents collaborate on the same durable document model, through clients like VS Code and CLI, with explicit execution state, native branching and sub-notebooks, and compatibility layers that serve the product rather than constrain it.

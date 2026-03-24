# RTC Evaluation

This document captures the current recommendation for using Jupyter's RTC and
shared-model packages inside `agent-repl`.

It answers a narrow architecture question:

- how much of the Jupyter RTC stack should we reuse
- where it fits in the steady-state design
- what must remain owned by `agent-repl`

This is not an implementation plan. It is the architectural posture we should
hold while building true agent-human multiplayer.

## Steady-State Rule

`agent-repl` should not become "a bridge to somebody else's collaborative
document model."

The steady state is:

- `agent-repl` owns notebook authority
- `agent-repl` owns actor/session state
- `agent-repl` owns execution ordering and runtime policy
- editors subscribe as projection clients
- shared-model technology is allowed only as an internal primitive

That means:

- no editor-owned authority
- no hidden execution path switching
- no CRDT layer deciding runtime semantics
- no "fallback" design as a product contract

## What Exists Today

The current Jupyter collaboration stack is real and active.

- JupyterLab 4.5 documents RTC through the `jupyter_collaboration` extension.
- `jupyter-collaboration` 4.2.1 was released on February 5, 2026.
- `jupyter-server-ydoc` 2.2.1 was released on February 5, 2026.
- `jupyter-ydoc` 3.4.0 was released on February 6, 2026.
- `pycrdt-store` 0.1.3 was released on December 11, 2025.
- `nextgen-kernels-api` 0.11.0 was released on November 17, 2025.

These are not dead side-projects. They are current enough to be legitimate
inputs into our design.

## Core Architectural Reading

Jupyter's own architecture describes `jupyter_collaboration` as a bundle around:

- `jupyter-server-ydoc`
- `jupyter-docprovider`
- frontend collaboration UI

And it explicitly describes:

- `jupyter-server-ydoc` as the server extension managing shared models
- `pycrdt-websocket` as the document sync transport
- `pycrdt-store` as the persistent CRDT storage layer

That tells us something important:

- the Jupyter stack is strongest at shared-document synchronization
- it is not the whole answer for runtime authority, execution policy, or
  agent-specific semantics

## Per-Package Recommendation

### `jupyter-server-ydoc`

Recommendation:

- prototype against
- selectively adopt if we need its shared-model behavior directly

Why:

- it is the strongest current server-side notebook shared-model reference
- its own description says it is used for both RTC and server-side notebook
  execution
- it is maintained inside the Jupyter project

Why not make it the foundation:

- it is centered on collaborative shared models, not on our full
  actor/runtime/ledger contract
- if it becomes the authority boundary, `agent-repl` risks collapsing back into
  a server bridge rather than owning the product model

Steady-state fit:

- good candidate for projection-layer document sync
- not the owner of agent semantics or execution authority

### `jupyter-ydoc`

Recommendation:

- adopt selectively as a modeling primitive
- otherwise prototype against

Why:

- it gives us a concrete notebook shared-document structure
- it exposes a notebook document type directly
- it is the cleanest current reference for how a notebook CRDT model is shaped

Steady-state fit:

- good source of truth for how to represent collaborative notebook structure
- potentially good as the internal projection model for open-editor live sync
- should still live under `agent-repl` ownership if used

### `jupyter-collaboration`

Recommendation:

- reference only for product behavior
- prototype only at the edges
- avoid as the foundation

Why:

- it is a useful proof of what Jupyter considers production RTC UX
- it bundles the exact server and UI pieces we should study
- but it is a meta-package for JupyterLab and Notebook 7 collaboration, not a
  drop-in authority model for `agent-repl`

Steady-state fit:

- good for comparing visible collaboration behavior
- wrong as the center of our architecture

### `pycrdt-websocket`

Recommendation:

- prototype against
- selectively adopt for document projection sync if needed

Why:

- it exists exactly for synchronizing shared documents over WebSockets
- it maps closely to the "agent and human watching one notebook" problem

Steady-state fit:

- good for live projection updates
- not sufficient for runtime ownership, run queues, or branch policy

### `pycrdt-store`

Recommendation:

- prototype against
- selectively adopt if we want persisted document-history primitives

Why:

- it gives the Jupyter stack persistence and recovery for shared documents
- that is relevant to later-opened notebooks, reconnect, and auditability

Steady-state fit:

- good for persisted shared-document state
- not a replacement for the agent-repl run ledger or runtime ledger

### `nextgen-kernels-api`

Recommendation:

- prototype against
- keep as a possible future dependency

Why:

- it is the most relevant current work on decoupled multi-client kernel access
- its architecture centers on a shared kernel client managed by the kernel
  manager
- that maps closely to our desire for a runtime that outlives editor presence

Steady-state fit:

- very relevant to later-opened editor continuity
- very relevant to "human continues from live in-memory objects"
- still not something to adopt blindly until we validate the fit

### `Jupyter AI`

Recommendation:

- reference only

Why:

- useful for product affordances, settings, chat surfaces, and notebook AI UX
- not relevant as a core authority or runtime primitive

### `Jupyter AI Agents`

Recommendation:

- reference only

Why:

- useful to study interaction modes and deployment modes
- not the foundation for our notebook authority model

## What We Should Actually Reuse

If we reuse anything from Jupyter's RTC ecosystem, the most likely good reuse is:

1. notebook shared-document structure from `jupyter-ydoc`
2. server-side shared-model ideas from `jupyter-server-ydoc`
3. optional WebSocket document sync from `pycrdt-websocket`
4. optional document persistence ideas from `pycrdt-store`
5. kernel/client lessons from `nextgen-kernels-api`

That is the maximal reuse posture that still preserves the north star.

## What We Should Not Outsource

These must remain inside `agent-repl`:

- notebook and runtime authority
- actor identity
- session lifecycle
- run queues
- serialized shared-kernel execution policy
- branch and sub-notebook semantics
- persisted execution ledger
- CLI contract
- projection policy for humans

If an external package starts owning any of those, we are moving away from the
steady state.

## Recommended Architectural Shape

The clean target is:

- `agent-repl` core owns notebook and runtime state
- `agent-repl` publishes projection updates to editor clients
- open-editor live sync may use a YDoc/CRDT-shaped projection internally
- runtime semantics remain server-authored
- execution ordering remains server-authored

In practice this means:

- CRDT for collaborative notebook structure and text is acceptable
- CRDT for execution truth is not
- editors may subscribe to a shared document
- runtimes must still obey the `agent-repl` queue and ledger rules

## Decision

Our current posture should be:

- do not switch the architecture to Jupyter RTC
- do not adopt `jupyter-collaboration` as the product foundation
- do prototype `jupyter-server-ydoc`, `jupyter-ydoc`, and
  `nextgen-kernels-api`
- do consider `pycrdt-websocket` and `pycrdt-store` as internal building
  blocks for live projection and persisted document state

This lets us benefit from the strongest current Jupyter work without giving up
our own authority model.

## Sources

- [JupyterLab RTC docs](https://jupyterlab.readthedocs.io/en/stable/user/rtc.html)
- [Jupyter collaboration architecture](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/developer/architecture.html)
- [jupyter-collaboration on PyPI](https://pypi.org/project/jupyter-collaboration/)
- [jupyter-server-ydoc on PyPI](https://pypi.org/project/jupyter-server-ydoc/)
- [jupyter-ydoc on PyPI](https://pypi.org/project/jupyter-ydoc/)
- [pycrdt-websocket on PyPI](https://pypi.org/project/pycrdt-websocket/)
- [pycrdt-store on PyPI](https://pypi.org/project/pycrdt-store/)
- [nextgen-kernels-api on PyPI](https://pypi.org/project/nextgen-kernels-api/)
- [Jupyter AI docs](https://jupyter-ai.readthedocs.io/en/latest/)
- [Jupyter AI Agents docs](https://jupyter-ai-agents.datalayer.tech/docs/agents/)

# Reference Stack

This document captures the current external reference posture for `agent-repl`.

It describes which systems we should:

- adopt directly
- prototype against
- study as product references
- avoid using as the architectural foundation

This assessment was updated from the current research pass on 2026-03-24.

## Core Rule

Use external systems to strengthen the architecture, not to outsource the core authority model.

We should own:

- live document authority
- actor/session model
- operation history
- runtime ownership policy
- branch and sub-notebook semantics
- the CLI and VS Code adapter contract

## Adopt or Strongly Consider

### Shared-model building blocks

- [jupyter-server-ydoc](https://pypi.org/project/jupyter-server-ydoc/)
- [jupyter-ydoc](https://pypi.org/project/jupyter-ydoc/)
- [jupyter-docprovider](https://pypi.org/project/jupyter-docprovider/)
- [pycrdt-websocket](https://pypi.org/project/pycrdt-websocket/)
- [pycrdt-store](https://pypi.org/project/pycrdt-store/)

Why these matter:

- they are the strongest current reference path for notebook shared models and RTC-adjacent behavior
- they may help us avoid inventing low-level sync primitives from scratch
- they are useful only if we keep `agent-repl-core` as the system authority

### Compatibility and review adjuncts

- [jupytext](https://jupytext.readthedocs.io/en/latest/jupyterlab-extension.html)
- [nbdime](https://nbdime.readthedocs.io/)

Why these matter:

- they improve reviewability, mergeability, and Git friendliness
- they support notebook compatibility work without distorting the core architecture

## Prototype Against

### Kernel and session references

- [nextgen-kernels-api](https://pypi.org/project/nextgen-kernels-api/)

Why prototype:

- it may offer a cleaner model for multi-client kernel access and decoupled runtime ownership
- it is promising enough to test, but not something we should commit to blindly

### Jupyter RTC shape

- [Jupyter RTC docs](https://jupyterlab.readthedocs.io/en/4.4.x/user/rtc.html)
- [Jupyter RTC architecture](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/developer/architecture.html)

Why prototype:

- useful for understanding how Jupyter thinks about shared state
- useful for choosing where CRDT-style text sync helps
- not something we should let become the authority boundary by accident

## Study as Product References

### AI and notebook UX references

- [Jupyter AI](https://jupyter-ai.readthedocs.io/en/latest/)
- [Jupyter AI Agents](https://jupyter-ai-agents.datalayer.tech/docs/agents/)
- [Notebook Intelligence](https://github.com/notebook-intelligence/notebook-intelligence)
- [VS Code notebook AI flows](https://code.visualstudio.com/docs/copilot/guides/notebooks-with-ai)
- [Deepnote Agent](https://deepnote.com/docs/deepnote-agent)
- [Datalore AI Assistant](https://www.jetbrains.com/help/datalore/ask-ai.html)

What to learn from them:

- how notebook-native AI affordances should feel
- how cell-scoped versus notebook-scoped actions differ
- how plan visibility and user approval should work
- how humans and automation should share context without confusion

### Collaboration and governance references

- [JupyterHub collaboration accounts](https://jupyterhub.readthedocs.io/en/4.x/tutorial/collaboration-users.html)

What to learn from it:

- how collaboration and permissions interact
- how shared execution surfaces should avoid identity confusion

## Avoid as the Foundation

### VS Code notebook state

Do not treat VS Code as the source of truth.

It is a client surface, not the authority layer.

### Raw `.ipynb`

Do not treat `.ipynb` as the live product model.

It is an important compatibility format, not a sufficient architecture center.

### Deepnote as the base platform

Deepnote is a useful benchmark and a possible source of ideas around:

- richer notebook formats
- conversion
- local runtimes
- agent tooling

But it should not be the foundation.

Why not:

- it does not map cleanly to the local `.ipynb` plus VS Code plus shared-runtime problem we are solving
- the migration would be closer to a product rewrite than a clean platform substitution
- its strongest current public value is product inspiration, not a clear self-hosted authority layer for our use case

## Internal References

Nearby local design references:

- [/Users/giladrubin/python_workspace/hypergraph/dev/ARCHITECTURE.md](/Users/giladrubin/python_workspace/hypergraph/dev/ARCHITECTURE.md)
- [/Users/giladrubin/python_workspace/hypster/src/hypster/core.py](/Users/giladrubin/python_workspace/hypster/src/hypster/core.py)
- [/Users/giladrubin/python_workspace/agent-repl/tmp/agent-repl-v2.md](/Users/giladrubin/python_workspace/agent-repl/tmp/agent-repl-v2.md)

What we borrow from them:

- clear layer boundaries
- small stable conceptual contracts
- durability and inspection as product features

## Decision Summary

The current best stack posture is:

- own the core authority model ourselves
- selectively adopt shared-model and persistence primitives from the Jupyter ecosystem
- prototype kernel/session decoupling ideas before committing
- use commercial or polished notebook AI systems mainly as workflow references
- avoid switching the foundation to Deepnote or any client-owned notebook surface

For the detailed RTC posture, see [RTC Evaluation](rtc-evaluation.md).

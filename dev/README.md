# Development Docs

**Internal reference** - This folder explains how the current system is built and which design docs are aspirational rather than shipped.

**Not the public surface** - End-user guides live under `docs/` and should describe the supported workflow only.

**Runtime-first framing** - The product now centers on a shared workspace runtime. VS Code, Cursor, and the browser preview are clients of that runtime rather than the primary source of truth.

**Onboarding is now public CLI surface** - `agent-repl setup`, `agent-repl doctor`, `agent-repl editor configure --default-canvas`, and `agent-repl editor dev` are shipped commands, so internal docs should treat them as current product behavior rather than aspirational UX only.

## Read These First

- [Current Architecture](/Users/giladrubin/python_workspace/agent-repl/dev/current-architecture.md) - shipped topology, live module boundaries, runtime/session model, and preview/editor split
- [Browser Verification Guide](/Users/giladrubin/python_workspace/agent-repl/dev/browser-verification-guide.md) - how to QA browser preview properly, verify streamed cell output, and avoid stale-build false alarms
- [VS Code Jupyter Parity Checklist](/Users/giladrubin/python_workspace/agent-repl/dev/jupyter-parity-checklist.md) - integration gaps and parity work against native notebooks

## Design Docs

- [North-Star Design Set](/Users/giladrubin/python_workspace/agent-repl/dev/design/README.md) - aspirational UX and architecture direction, not a guarantee of current behavior
- [JupyterLab-Powered Notebook Surface](/Users/giladrubin/python_workspace/agent-repl/dev/design/jupyterlab-surface.md) - proposed path to stop owning the notebook UI while preserving runtime-first authority
- [Architecture Modernization Plan](/Users/giladrubin/python_workspace/agent-repl/dev/architecture-modernization-plan.md) - recommended path to reduce bespoke infrastructure while preserving current CLI, VS Code, and browser behavior
- [Architecture Modernization Rollout](/Users/giladrubin/python_workspace/agent-repl/dev/implementation-chain/architecture-modernization-rollout.md) - implementation slices with HLD, acceptance criteria, and test gates for the modernization chain
- [JupyterLab Surface Rollout](/Users/giladrubin/python_workspace/agent-repl-jupyterlab-surface/dev/implementation-chain/jupyterlab-surface-rollout.md) - phased plan for moving the notebook surface onto JupyterLab and YDoc-backed projection seams without giving up daemon authority
- [Behavior Locks](/Users/giladrubin/python_workspace/agent-repl/dev/behavior-locks/README.md) - preserved product behaviors and their regression-test anchors so modernization does not silently erase interaction decisions

## Historical Notes

- [Early v2 plan](/Users/giladrubin/python_workspace/agent-repl/dev/history/agent-repl-v2.md)
- [Early v2 architecture draft](/Users/giladrubin/python_workspace/agent-repl/dev/history/agent-repl-v2-architecture.md)

## Working Rules

- When a feature, workflow, architecture note, or developer loop changes, update the affected durable docs in the same change: `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/`.
- Update this folder when the runtime surface, preview/editor topology, or extension build loop changes in a user-visible way
- When onboarding or install flows change, update the developer-facing explanation here as well as the public docs
- Prefer [Current Architecture](/Users/giladrubin/python_workspace/agent-repl/dev/current-architecture.md) for shipped details and keep design docs clearly labeled as targets
- When preview and installed VS Code behavior differ, document which asset source is authoritative and how to verify sync
- Treat Extension Development Host as the preferred integration loop; installed-extension workflows are secondary and should surface drift warnings

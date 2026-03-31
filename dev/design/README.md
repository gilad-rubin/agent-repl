# Design Docs

This folder contains the durable architecture docs for `agent-repl`.

These documents describe the system we want to build, the boundaries we want to preserve, and the reference systems we want to compare against. They are not implementation plans.

## Documents

- [North Star](north-star.md) — the desired end-state architecture
- [Agreed V1 Architecture](agreed-v1-architecture.md) — the concrete first architecture we currently agree to build, with human-facing and backend-facing acceptance criteria
- [Founding Architecture](founding-architecture.md) — the most opinionated clean-slate architecture statement for the project
- [Greenfield Architecture](greenfield-architecture.md) — the clean-slate architecture we would choose if rebuilding from zero today
- [Interaction Contract](interaction-contract.md) — the agreed human/agent notebook behavior and UX contract
- [Joint Canvas Spec](joint-canvas-spec.md) — concrete product behavior for live human-agent collaboration, reopen, continuity, and ephemeral work
- [Joint Canvas Rollout](joint-canvas-rollout.md) — the concrete implementation sequence and ClickUp-backed rollout plan for the joint-canvas work
- [Interaction Implementation Plan](interaction-implementation-plan.md) — the workstreams and acceptance gates needed to satisfy that contract
- [Core Authority and Sessions](core-authority.md) — what the core owns, how actors connect, and how continuity works
- [Runtime and Execution](runtime-and-execution.md) — runtime ownership, run state, and zombie-kernel philosophy
- [File Compatibility and Sync](file-compatibility.md) — how `.ipynb`, richer v2 state, and external file changes should coexist
- [Collaboration, Branching, and Sub-Notebooks](collaboration.md) — how humans and agents should work safely in parallel
- [Reference Stack](reference-stack.md) — external technologies to adopt, prototype against, study, or avoid
- [RTC Evaluation](rtc-evaluation.md) — how much of Jupyter's RTC/shared-model stack we should reuse without giving up core authority
- [Review Rubric](review-rubric.md) — the recurring architecture checks to run after each meaningful slice
- [JupyterLab-Powered Notebook Surface](jupyterlab-surface.md) — why the notebook UI should move to a JupyterLab surface while the daemon remains authoritative

## How to Use These Docs

Use these docs to answer:

- what should be authoritative
- what compatibility layers should remain non-authoritative
- how humans and agents should collaborate
- how to judge whether a slice moves us toward or away from the north star

These docs should stay stable enough that we can compare every serious change against them without rewriting the target each time.

## Relationship to the Current Codebase

The current shipped implementation is documented in [../current-architecture.md](../current-architecture.md).

That document explains how `agent-repl` works today.

The docs in this folder explain where `agent-repl` should go next.

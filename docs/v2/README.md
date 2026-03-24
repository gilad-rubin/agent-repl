# v2 Design Docs

This folder contains the durable architecture docs for `agent-repl` v2.

These documents describe the system we want to build, the boundaries we want to preserve, and the reference systems we want to compare against. They are not implementation plans.

## Documents

- [North Star](north-star.md) — the desired end-state architecture
- [Core Authority and Sessions](core-authority.md) — what the core owns, how actors connect, and how continuity works
- [Runtime and Execution](runtime-and-execution.md) — runtime ownership, run state, and zombie-kernel philosophy
- [File Compatibility and Sync](file-compatibility.md) — how `.ipynb`, richer v2 state, and external file changes should coexist
- [Reference Stack](reference-stack.md) — external technologies to adopt, prototype against, study, or avoid
- [Review Rubric](review-rubric.md) — the recurring architecture checks to run after each meaningful slice

## How to Use These Docs

Use these docs to answer:

- what should be authoritative
- what compatibility layers should remain non-authoritative
- how humans and agents should collaborate
- how to judge whether a slice moves us toward or away from v2

These docs should stay stable enough that we can compare every serious change against them without rewriting the target each time.

## Relationship to v1

The current shipped system is documented in [../architecture.md](../architecture.md).

That document explains how `agent-repl` works today.

The docs in this folder explain where `agent-repl` should go next.

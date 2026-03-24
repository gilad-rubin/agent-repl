# Review Rubric

This document defines the recurring review loop for `agent-repl` work.

Use it after every meaningful spike, refactor, or feature slice.

The goal is not to ask whether a slice works in isolation.

The goal is to ask whether it moves the system closer to the architecture we want.

## Core Review Passes

Every serious slice should be reviewed through four passes.

### Pass 1: Authority

Ask:

- what is the source of truth in this slice
- did we accidentally let the editor or the file become authoritative again
- are IDs coherent across all entry points
- do `cat`, `edit`, and `exec` conceptually target the same live state

Good signs:

- one canonical authority
- durable IDs
- explicit ownership boundaries

Bad signs:

- fallback identities
- path-dependent behavior
- client-local truth

### Pass 2: Collaboration

Ask:

- does this slice make human-agent collaboration more explicit and safer
- is actor attribution visible
- are branches, leases, or ownership clearer
- does risky work become more reviewable

Good signs:

- explicit actor model
- legible ownership
- safe parallel work

Bad signs:

- silent stomping
- anonymous changes
- collaboration that only works when humans and agents stay out of each other's way

### Pass 3: Runtime

Ask:

- is execution state more authoritative and less heuristic
- are reconnect and restart stories clearer
- are runtime lifecycles more understandable
- are zombie-kernel outcomes more controlled

Good signs:

- explicit run state
- clear runtime ownership
- durable recovery semantics

Bad signs:

- runtime truth inferred from editor events
- ambiguous detached kernels
- reconnect logic that depends on luck

### Pass 4: Compatibility

Ask:

- did we preserve Jupyter usefulness
- did we preserve `.ipynb` usefulness
- did we avoid making compatibility layers the product center
- did we keep room for richer internal semantics

Good signs:

- strong compatibility
- clear projection boundaries
- no forced flattening of the internal model

Bad signs:

- `.ipynb` treated as the live authority
- compatibility concerns dictating the whole product model

## Interactive Scenario Suite

Each slice should be checked against these recurring scenarios.

## Notebook lifecycle

- notebook created from CLI only
- notebook created from editor only
- notebook opened and edited
- notebook closed and reopened
- notebook exported to `.ipynb`
- notebook overwritten externally

Key question:

- does canonical state remain coherent and explainable through all of this

## Client lifecycle

- VS Code opens
- VS Code closes
- extension reloads
- CLI attaches
- CLI exits
- agent process restarts
- core service restarts

Key question:

- can a later client continue confidently without hidden drift

## Mixed collaboration

- human edits while agent edits elsewhere
- human edits while agent edits overlapping work
- agent works in a branch
- second agent continues the first agent's work
- human reviews and merges or rejects

Key question:

- is collaboration legible, attributable, and conflict-safe

## Execution lifecycle

- run a single node
- run a range
- run a branch
- disconnect during execution
- reconnect during execution
- runtime restart
- runtime crash
- detached runtime cleanup

Key question:

- is execution state authoritative and recoverable instead of guessed

## Recovery and resilience

- kill a client and reconnect
- kill the core service and recover
- leave a runtime orphaned
- introduce an external file change
- leave work idle and return later

Key question:

- can the system explain what happened and continue without ambiguity

## Architecture Smells

Treat these as immediate warning signs:

- more than one live source of truth
- IDs that depend on which path read the notebook
- runtime truth inferred from editor behavior
- file export format treated as product authority
- notebook copies used as fake branches
- agent work that cannot be resumed or attributed
- runtime cleanup that depends on UI closure

## Success Signals

At a high level, a slice is moving in the right direction when:

- the architecture becomes easier to explain
- the boundaries become cleaner rather than cleverer
- restart and reconnect stories become less scary
- human and agent work become more visible and safer
- compatibility remains strong without dominating the design

## Canonical References

Review each slice against:

- [North Star](north-star.md)
- [Reference Stack](reference-stack.md)
- [Current v1 Architecture](../architecture.md)

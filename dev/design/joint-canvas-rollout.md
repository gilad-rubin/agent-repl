# Joint Canvas Rollout

This document tracks the concrete implementation slices for the joint-canvas design and mirrors the ClickUp rollout under `Agent-repl runtime lifecycle rollout`.

Primary specs:

- [Joint Canvas Spec](joint-canvas-spec.md)
- [Runtime and Execution](runtime-and-execution.md)
- [Collaboration, Branching, and Sub-Notebooks](collaboration.md)
- [Core Authority and Sessions](core-authority.md)

## Current Status

Shipped foundations:

- runtime continuity and reattach policy
- real ephemeral runtimes with TTL and discard semantics
- explicit runtime recovery and kernel generation tracking
- live presence and activity polling
- lease-based collision handling for headless and editor-backed work
- branch-backed review request / resolve primitives with branch handoff hints on lease conflicts
- incremental notebook ops for inserted cells, source updates, and output updates, with snapshot fallback for reset-style changes
- persisted recent activity history plus explicit runtime state transitions and transition events
- explicit ephemeral promote/discard commands with collaboration events and terminal discard behavior

Still open at a product level:

- richer branch-local draft surfaces and merge UX
- finer-grained typing deltas beyond cell-level source updates
- longer-lived review/audit history beyond the bounded recent activity window
- richer promote/review UX around ephemeral work in the editor surface

## ClickUp Sequence

Parent task:

- `869cmruk9` Agent-repl runtime lifecycle rollout

Completed:

1. `869cmrun1` Runtime continuity and reattach policy
2. `869cmrun8` Real ephemeral runtime mode
3. `869cmrup0` Runtime recovery and continuity follow-up
4. `869cmt3dw` Live activity and presence stream for joint canvas
5. `869cmt5e1` Cell leases and collision handling for joint canvas
6. `869cmta85` Branch-backed draft and review handoff for joint canvas
7. `869cmtb35` Incremental projection and live typing operation stream
8. `869cmtb4m` Durable activity ledger and runtime state machine enforcement
9. `869cmtb5z` Ephemeral promotion and discard workflow

## Slice Design

### Slice 6: Branch-Backed Draft and Review Handoff

Goal:

- turn risky overlap into explicit draft/review flow instead of plain conflict errors

Scope:

- review state attached to branches
- explicit request-review / resolve-review actions
- conflict payloads that can point to a branch-backed escape hatch
- activity events for review lifecycle

Acceptance:

- a risky or overlapping change can be moved into a reviewable branch flow
- review state is attributable to sessions
- tests cover request, resolution, and branch ownership

Shipped in this slice:

- branch review metadata on `BranchRecord`
- `branch-review-request` and `branch-review-resolve` APIs / CLI
- notebook activity events for review lifecycle
- lease conflicts that suggest a branch-backed handoff target

### Slice 7: Incremental Projection and Live Typing

Goal:

- make the notebook feel collaborative instead of periodically replaced

Scope:

- operation-aware projection for insert, delete, move, and source delta paths
- event stream extensions for text deltas and output append
- reduced reliance on full `replaceCells` recovery

Acceptance:

- humans can watch agent edits evolve live
- output can append without full snapshot replacement
- snapshot replacement remains a fallback, not the primary path

Shipped in this slice:

- cell-level activity payloads for insert, remove, source update, outputs update, and output append
- incremental application in the extension for inserted cells and cell updates
- live output append handling for running executions
- explicit `notebook-reset-needed` fallback for complex or full-projection changes

### Slice 8: Durable Activity Ledger and Runtime State Enforcement

Goal:

- make collaboration and runtime behavior explainable after reconnect or restart

Scope:

- persist enough activity/review state for later inspection
- tighten runtime transition handling to better match the documented state model
- improve restart/recovery semantics and tests

Acceptance:

- later clients can inspect what happened, not only current state
- runtime status transitions are explicit and validated
- restart/recovery behavior is covered by tests

Shipped in this slice:

- recent activity history now persists in core state and survives reload
- runtime state transitions emit explicit `runtime-state-changed` activity events
- headless runtime startup, shutdown, expiry, and run boundaries now respect a shared transition helper
- degraded runtimes now reject new runs until recovery

### Slice 9: Ephemeral Promotion and Discard Workflow

Goal:

- finish the collaboration half of ephemeral mode

Scope:

- explicit promote/discard operations
- activity events for promotion and expiry
- safe promotion into persistent/shared work
- tests for promoted and discarded ephemeral work

Acceptance:

- ephemeral work can be reviewed, then promoted or discarded explicitly
- promotion is visible as a collaboration event
- discard does not pollute the main notebook state

Shipped in this slice:

- `runtime-promote` converts notebook-bound ephemeral runtimes into `shared` or `pinned` mode and clears their TTL
- `runtime-discard` ends ephemeral runtimes terminally and prevents silent revival
- promotion/discard now emit explicit collaboration events for later inspection
- tests cover promoted runtimes, discarded runtimes, CLI wiring, and client API wiring

## Working Rule

Implementation should proceed in order.

When a slice is finished:

1. update this document
2. sync the ClickUp task to `complete`
3. queue the next slice if needed
4. begin the next slice without waiting for another prompt

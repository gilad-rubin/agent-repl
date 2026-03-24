# Interaction Implementation Plan

This document translates the interaction contract into concrete workstreams and acceptance gates.

Primary reference:

- [Interaction Contract](interaction-contract.md)

Supporting architecture references:

- [North Star](north-star.md)
- [Core Authority and Sessions](core-authority.md)
- [Runtime and Execution](runtime-and-execution.md)
- [File Compatibility and Sync](file-compatibility.md)
- [Collaboration, Branching, and Sub-Notebooks](collaboration.md)

## Goal

Make `agent-repl` satisfy the agreed behavior contract for:

- minimal agent workflows
- background-safe notebook operations
- persisted notebook visibility
- live editor projection
- serialized shared-kernel execution
- eventual closed-editor operation

## Workstream 1: Minimal Happy-Path UX

Target:

- `new` is enough to create and prepare a notebook
- `ix` is enough to execute and get results
- `status` and `cat` are optional diagnostics, not mandatory ritual

Acceptance:

- a new notebook can be created and immediately executed without extra kernel-selection steps
- `ix` returns the result directly
- existing notebooks can be edited and executed without requiring `cat` or `status` in the happy path

## Workstream 2: Default Runtime and Kernel Semantics

Target:

- workspace `.venv` is always the default kernel/runtime choice when present
- non-default runtimes are explicit

Acceptance:

- new notebook creation defaults to the workspace `.venv`
- existing notebook attach defaults to the workspace `.venv`
- absence of a workspace `.venv` produces a clear explicit-selection path
- no implicit UI-based kernel choice is required

## Workstream 3: Background-Safe Command Surface

Target:

- normal notebook commands are background-safe
- commands fail explicitly rather than prompting humans

Scope:

- `new`
- `select-kernel`
- `ix`
- `exec`
- `edit`
- `run-all`
- `restart`
- `restart-run-all`

Acceptance:

- no focus steal
- no notebook forced to foreground
- no modal restart or kernel dialogs
- no user clicks required

## Workstream 4: Shared-Live Notebook Projection

Target:

- when the notebook is open, the human sees the agent’s work live

Acceptance:

- inserted cells appear live
- edited source updates live
- running state is visible
- outputs appear live
- source and outputs remain consistent after re-execution

## Workstream 5: Shared-Kernel Execution Serialization

Target:

- the default shared-kernel path is safe and unsurprising

Rules to enforce:

- edits can happen concurrently
- execution is serialized
- agent work queues behind active human execution

Acceptance:

- human-running + agent-add works
- human-running + agent-execute queues instead of interrupting
- no hidden kernel restart
- no mixed outputs

## Workstream 6: Persisted Notebook Guarantees

Target:

- opening later always shows what happened

Acceptance:

- persisted cells and outputs are visible after reopening
- edited source and persisted outputs match
- runtime state is not required for notebook visibility correctness

## Workstream 7: Live Runtime Continuity

Target:

- if the runtime is still alive, humans can continue from live in-memory state

Acceptance:

- after headless agent work, opening the notebook while the runtime is alive preserves usable in-memory objects
- the editor attaches to the existing runtime rather than creating a conflicting session

## Workstream 8: Closed-Notebook Operation

Target:

- notebook visibility is optional

Acceptance:

- agent can create, attach, edit, and execute against notebooks that are not visibly open
- later opening the notebook shows the updated state cleanly

## Workstream 9: Closed-Editor Operation

Target:

- the editor window itself is optional for agent work

This is the biggest remaining gap.

Acceptance:

- if no VS Code/Cursor window is open for the workspace, `agent-repl` can still start or resume a headless runtime
- the normal workflow (`new`, `ix`, `edit`, `exec`) still works
- later opening the editor attaches to that runtime as a projection client

## Recommended Delivery Order

1. Minimal happy-path UX
2. Default runtime and kernel semantics
3. Background-safe command surface
4. Shared-live notebook projection
5. Shared-kernel execution serialization
6. Persisted notebook guarantees
7. Closed-notebook operation
8. Closed-editor operation
9. Live runtime continuity polish

In practice, some of these can overlap, but closed-editor operation is likely the inflection point that turns the current bridge-centric system into the intended runtime-centric system.

## Regression Strategy

Each workstream should add or update regressions at the layer where the guarantee lives:

- CLI contract tests for minimal workflow and public surface
- extension route tests for no-popup / no-focus behavior while a bridge is running
- runtime/core tests for authority, queueing, continuity, and persistence
- end-to-end validation scenarios for:
  - editor open
  - notebook closed
  - editor closed

## Validation Scenarios to Keep Running

Minimal new notebook:

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

Minimal existing notebook:

```bash
agent-repl edit notebooks/demo.ipynb ...
agent-repl ix notebooks/demo.ipynb -s 'x + 1'
```

Shared-live projection:

- human has notebook open
- agent inserts and runs a cell
- human sees source, running state, and outputs live

Shared-kernel serialization:

- human starts a long-running cell
- agent inserts a new cell
- agent requests execution
- agent execution queues and runs only after human execution completes

Closed-editor:

- no matching bridge window is open
- agent runs the minimal workflow successfully through a headless runtime

## Exit Condition

This plan is complete when the simple validation prompt can be run by an agent with:

- no editor pre-open requirement
- no extra kernel-selection rituals
- no required `status` or `cat` in the happy path
- no modal dialogs
- no focus steal
- correct persisted notebook state
- correct live shared behavior when the human later opens the notebook

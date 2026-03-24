# Interaction Contract

This document captures the concrete product behavior agreed for `agent-repl`.

It is not an implementation plan. It is the contract we should compare the system against as we build.

## Core Principle

`agent-repl` should feel like one shared notebook runtime with two kinds of participants:

- agents operating through the CLI
- humans operating through VS Code or Cursor

The runtime is authoritative. The editor is a projection client.

## Minimal Agent Workflow

The normal happy path should be very small.

For a brand-new notebook:

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s '...'
```

For an existing notebook:

```bash
agent-repl edit notebooks/demo.ipynb ...
agent-repl ix notebooks/demo.ipynb -s '...'
```

Or, when re-running a known cell:

```bash
agent-repl exec notebooks/demo.ipynb --cell-id <id>
```

These commands should be enough for the common path.

## What `new` Must Do

`agent-repl new` should do the full preparation step automatically.

It should:

- create the notebook file
- create or attach the runtime and kernel needed for that notebook
- default to the workspace `.venv` when it exists
- return a notebook that is ready for immediate `ix`, `edit`, or `exec`

It should not require a follow-up kernel-selection dance in the normal case.

## Kernel Defaulting Rule

The default kernel rule is strict:

- if a workspace `.venv` exists, it is the default kernel/runtime choice
- if no workspace `.venv` exists, the agent or human must choose explicitly
- non-default kernels should be explicit, not inferred through UI

This rule should hold for both new notebooks and existing notebooks.

## What `ix` Must Provide

`agent-repl ix` is the default execution primitive.

In the happy path it should:

- insert a cell
- execute it
- return the result directly

The agent should not need `cat` just to see the result of a normal `ix`.

## What `status` and `cat` Are For

`status` and `cat` are diagnostic and inspection commands.

They should be used when:

- execution is long-running
- something timed out
- the agent needs cell IDs or full structure
- the runtime appears stuck or uncertain

They should not be required in the normal path for basic notebook work.

## Human Opens the Notebook Later

If a human opens the notebook after the agent has worked headlessly, they should immediately see:

- the created or edited cells
- the latest source
- the persisted outputs

This persisted notebook state should always be visible.

If the runtime is still alive, the human should ideally also inherit live in-memory continuity:

- previously created variables remain available
- the next manual cell can continue naturally from the agent's work

## Human Already Has the Notebook Open

If the notebook is already open while the agent works, the notebook should behave like a shared live document.

The human should see:

- new cells appear in the right place
- edited cell source update in place
- running state appear for executed cells
- outputs appear or stream when execution completes

The human should not see:

- focus stolen
- a notebook tab forced to the foreground
- modal popups
- kernel pickers
- restart confirmations
- stale outputs left next to edited source

## Shared-Kernel Execution Rule

For the default shared-kernel case:

- document edits may happen concurrently
- execution is serialized

If a human is already running a cell:

- the agent may add a cell
- the agent may edit cells
- the agent may request another execution
- that execution must queue behind the currently running work

What must not happen:

- the human's cell is interrupted
- the kernel is restarted
- executions race invisibly
- outputs from separate runs become mixed

## Closed Notebook Behavior

The notebook does not need to be visibly open in the editor for the agent to operate on it.

`agent-repl` should support:

- creating notebooks in the background
- opening or attaching to existing notebooks in the background
- editing and executing against a notebook that is not currently visible

If the notebook later becomes visible, the human should see the already-updated state.

## Closed Editor Behavior

The stronger target is that the editor window itself should not be required.

If the editor is closed:

- the agent should still be able to run the normal workflow
- the necessary runtime should be started or resumed headlessly
- no VS Code window should need to be opened just to satisfy agent work

When the editor opens later, it should attach to the already-running shared runtime as a projection client.

## No-Popup / No-Focus Contract

The following are product bugs in the normal workflow:

- notebook tab opening itself in the foreground
- any kernel picker appearing unexpectedly
- any restart confirmation dialog
- any request for the human to click buttons to continue agent work
- any focus steal caused by `new`, `ix`, `edit`, `exec`, `run-all`, `restart`, or kernel attachment

If the system cannot perform an operation safely in the background, it should fail explicitly in the CLI rather than prompting the human.

## Persistence and Runtime Continuity

Two guarantees matter:

1. Persisted notebook state
- cells, source, outputs, and relevant metadata are on disk and visible later

2. Live runtime continuity
- if the runtime is still alive, later work can continue with the same in-memory objects

Persisted notebook state is mandatory.
Live runtime continuity is the ideal when the runtime still exists.

## Acceptance Scenarios

The system should eventually pass these scenarios cleanly:

1. Editor closed, create a new notebook, run cells, edit cells, inspect outputs.
2. Editor closed, operate on an existing notebook without opening a VS Code window.
3. Editor open, agent inserts and runs cells while the human watches live updates.
4. Human runs a cell, agent adds a cell during that run, and the agent's execution queues cleanly.
5. Human opens the notebook later and sees persisted results.
6. Human opens the notebook while the runtime is still alive and can continue with live in-memory state.

## What This Contract Implies

This contract implies that:

- the runtime must be authoritative
- editor commands cannot be the only execution/control path
- notebook operations must be available through a headless runtime
- VS Code should be optional for the agent workflow, not a prerequisite

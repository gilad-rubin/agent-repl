# Joint Canvas Spec

This document defines the concrete product behavior for `agent-repl` as a shared notebook canvas for humans and agents.

It sits between the higher-level design docs and the implementation plan.

It answers:

- what the notebook should feel like when humans and agents share it
- how persistent and ephemeral runtime modes should behave
- what happens when the notebook or editor is opened later
- how collisions, live activity, and review should work

It is not the low-level implementation plan for each subsystem.

Primary references:

- [North Star](north-star.md)
- [Interaction Contract](interaction-contract.md)
- [Collaboration, Branching, and Sub-Notebooks](collaboration.md)
- [Runtime and Execution](runtime-and-execution.md)
- [RTC Evaluation](rtc-evaluation.md)

## Product Goal

`agent-repl` should feel like one durable shared canvas.

That means:

- the agent can work headlessly with no editor open
- the runtime and live objects can outlive the editor session
- a human can open the notebook before, during, or after agent work
- the human sees a coherent shared state rather than a stale file plus repair rituals
- no one is surprised by silent overwrite, revert prompts, or conflicting kernel sessions

## Problems to Eliminate

The current experience should move away from:

- opening a finished notebook and being asked to pick a kernel again
- reopening a notebook and losing the ability to continue from live in-memory state
- seeing notebook projection stop because the local editor is dirty
- whole-notebook snapshot replacement feeling like overwrite instead of collaboration
- unclear ownership when a human and agent both touch nearby cells

## User-Facing Promise

The core product promise is:

- if the notebook has persisted state, opening it later always shows that state
- if a compatible runtime is still alive, opening it later attaches to that runtime automatically
- if an agent is actively working, the notebook feels live and attributable
- if a human and agent overlap, the system coordinates instead of forcing overwrite or rerun rituals

## Runtime Modes

Runtime modes are product concepts, not only internal flags.

## Scope of Long-Lived State

Long-lived state exists at multiple levels.

We should be explicit about which layer we mean.

### Computer Level

At the computer level, "long-lived" means the shared work survives normal client disconnects while the host machine remains alive.

It does not imply surviving machine shutdown or reboot by default.

### Workspace Level

At the workspace or project level, "long-lived" means the project has durable runtime records and policy about which runtimes should be resumed, expired, pinned, or promoted.

This is where the main shared runtime versus ephemeral runtime distinction lives.

### Notebook Level

At the notebook level, "long-lived" means a notebook can reopen into the same shared execution context when a compatible runtime still exists.

This is what gives the human the feeling of continuity instead of "open file, pick kernel, rerun everything."

### Kernel Level

At the kernel level, "long-lived" means the actual execution backend is still alive and still bound to the runtime.

This is necessary for live memory continuity, but the kernel should still be treated as subordinate to the runtime in product semantics.

### Persistent Shared Runtime

This is the default mode for normal notebook collaboration.

It should:

- bind a notebook or branch to a durable runtime identity
- preserve in-memory objects across notebook and editor close/reopen
- automatically accept later human re-attach when policy allows
- be the mode used by the normal happy path for collaborative work

This is the mode that should satisfy:

- agent works with editor closed
- human opens later and continues naturally
- human opens during execution and sees live state

The persistent shared runtime is the thing that should feel long-lived to the user.

In practice that means:

- the notebook may close without ending the runtime
- the editor may close without ending the runtime
- the agent process may exit without ending the runtime
- the runtime should remain resumable until policy says to drain, stop, or reap it

### Ephemeral Runtime

Ephemeral mode is for exploratory, risky, or disposable work.

It should:

- start quickly with a short-lived runtime identity
- support full notebook interaction while it exists
- isolate work from the main line by default
- expire after a TTL or explicit discard
- offer explicit promotion into a persistent runtime, branch, or merged mainline result

Ephemeral mode is appropriate for:

- agent experiments
- delegated sub-tasks
- one-off debugging sessions
- trials that should not silently become the shared main runtime

Ephemeral mode should still be a real runtime and kernel while active.

The difference is not "fake runtime" versus "real runtime."

The difference is lifecycle policy:

- shorter default lifetime
- isolated by default
- explicit promotion or discard path

### Pinned Runtime

Pinned mode is the explicit "keep this alive" runtime for longer-running or expensive work.

It should:

- survive normal idle cleanup
- be clearly visible in runtime lists and notebook status
- make takeover and re-attach behavior explicit

## Notebook Open and Reopen Rules

Opening and closing a notebook should not be confused with starting and stopping a runtime.

Product rules:

- opening a notebook attaches a projection client
- closing a notebook detaches a projection client
- starting a runtime creates or resumes execution authority
- stopping a runtime ends resumable live continuity
- restarting a kernel preserves the runtime identity but breaks live memory continuity

### Human Opens After Agent Finished

When a human opens the notebook after headless agent work:

- the notebook opens directly to the latest persisted shared state
- if a matching runtime is alive, the editor auto-attaches to it
- no kernel picker should appear in the normal case
- the next cell should continue from the shared runtime if continuity is available
- if continuity is not available, the notebook should say so explicitly instead of failing implicitly

### Human Opens While Agent Is Working

When a human opens during active work:

- the notebook should attach as a projection client
- the human should see the agent's current activity
- running cells and streaming outputs should appear live
- current ownership and activity should be attributable to the agent session
- the human should be able to follow the agent, observe passively, or take over intentionally

### Human Opens Before Agent Starts

When the human already has the notebook open:

- the human editor remains a client, not the authority
- the agent can still attach headlessly and operate on the same canvas
- new cells, edits, execution state, and outputs should appear incrementally
- the system should avoid full-document replacement as the primary user-visible behavior

## Joint Canvas Experience

The notebook should feel closer to a collaborative canvas than a background sync target.

The human should be able to see:

- who is attached
- which session is active
- which cell or region the agent is working in
- whether the agent is planning, editing, executing, waiting, or blocked
- what changed in the current run
- whether a change is committed, pending review, or draft-only

The system should support:

- follow-agent
- jump to current activity
- per-run summaries
- per-cell attribution
- lightweight review before risky changes land

## Live Activity Model

The system should publish activity as durable operations and live events.

Minimum event families:

- `session_attached`
- `session_detached`
- `presence_updated`
- `node_inserted`
- `node_deleted`
- `node_moved`
- `node_edit_started`
- `node_text_delta`
- `node_edit_committed`
- `node_edit_canceled`
- `run_queued`
- `run_started`
- `run_output_appended`
- `run_finished`
- `lease_acquired`
- `lease_released`
- `review_requested`
- `review_resolved`
- `runtime_promoted`
- `runtime_expired`

The important product rule is that the human should see intent and progress, not only eventual notebook state.

## Presence Model

Presence should be first-class and session-based.

Each attached session should expose:

- actor type
- session label
- current document or branch
- current node or region when relevant
- current activity state
- last heartbeat
- runtime binding

Presence is not only cosmetic.

It should power:

- attribution
- ownership hints
- collision avoidance
- takeover and review flows

## Ownership and Collision Rules

The system should optimize for "safe concurrency" rather than hard locking.

### Default Rule

- humans and agents may work concurrently
- execution is serialized for shared-kernel safety
- edits should be scoped and attributable

### Cell and Region Leases

The default collision control should use lightweight leases, not global notebook locks.

Leases should:

- be scoped to a node or region
- be visible in the UI
- expire automatically if a session disappears
- allow observation without forcing edit access

Stage 4 default: leases are advisory for text edits and stronger for structural edits.

That means:

- concurrent text overlap should prefer visible warning plus draft or review paths
- destructive structural edits may be blocked or redirected into reviewable draft work

Minimum lease semantics:

- leases have a TTL and must be renewed by heartbeat
- expired leases do not discard already-authored work automatically
- lease ownership is attached to durable node identity, not raw index position
- structural moves must preserve or remap lease ownership through node identity
- deadlock should resolve by timeout, takeover, or branch-local draft, not by indefinite mutual blocking

### No Silent Overwrite Rule

The system must not silently replace human-visible notebook content when another actor is actively editing that same region.

If overlap occurs, the system should prefer one of these behaviors:

- queue the second write
- create a draft or branch-local edit
- ask for takeover
- ask for review before merge

It should not:

- revert someone else's recent change
- replace a dirty editor buffer with a remote snapshot
- hide a conflict behind a later file write

### Same-Cell Overlap

If a human and agent want to edit the same cell at the same time:

- one actor becomes the active editor for that cell
- the other actor sees the lease and can wait, fork, or request takeover
- the notebook should surface the state as collaboration, not corruption

### Structural Edits

Structural edits need stricter rules than text deltas.

Operations like:

- move cell
- delete cell
- split or merge sections
- clear outputs

These operations should be treated as higher-risk and may require:

- stronger leases
- branch-local execution
- explicit review

## Projection Failure and Recovery

Incremental projection is the desired path, but the product needs a visible recovery path.

Fallback to snapshot recovery is acceptable when:

- the client missed too much event history
- operation replay diverged from canonical state
- a client reconnects after a long absence
- recovery after restart needs a fresh full projection

When snapshot recovery happens:

- it should be explicit in diagnostics
- it must not silently overwrite active local editing in the same region
- the client should reconcile or defer until the conflicting local work is resolved

Snapshot recovery is a correctness tool, not a normal collaboration experience.

## Review and Safety

The shared canvas should still support review.

Review should be natural for:

- agent-written code
- broad notebook rewrites
- structural edits
- deletions
- promotions from ephemeral to persistent work

The human should be able to:

- inspect a per-run diff
- accept or reject a risky change set
- promote draft work into mainline
- discard an ephemeral run without polluting the main notebook

## Persistence Model

Three layers of persistence matter:

### Persisted Notebook State

This is mandatory.

It includes:

- nodes
- source
- outputs
- stable IDs
- enough metadata to reconstruct the visible notebook faithfully

### Runtime Continuity

This is best-effort but highly valuable.

It includes:

- in-memory Python objects
- imported modules and stateful tools
- widget or execution context when supportable

### Activity and Review History

This should be durable enough to explain what happened.

It includes:

- who changed what
- what run produced which outputs
- which changes were reviewed, promoted, rejected, or expired

## Open-Later Continuation Rule

This is the most important concrete behavior target.

If a notebook is opened later and a compatible persistent runtime still exists:

- attach automatically
- use the existing runtime identity
- restore live continuity without requiring rerun
- skip kernel selection in the normal case

If no compatible runtime exists:

- present the notebook faithfully from persisted state
- explain that live runtime continuity is unavailable
- offer an explicit resume or rebuild action

The product should never force a confusing middle state where the notebook looks completed but cannot naturally continue.

## Projection Strategy

Projection should become operation-aware rather than whole-notebook replacement by default.

Good projection behavior:

- incremental cell insertion
- incremental text deltas
- live running indicators
- streaming outputs
- attribution badges

Fallback behavior:

- full snapshot replacement only when incremental replay is impossible

Full snapshot replacement should be treated as a recovery path, not the normal collaboration UX.

## Suggested Command and UI Semantics

The CLI and UI should converge on the same concepts.

Likely command-level concepts:

- `agent-repl new --runtime persistent`
- `agent-repl new --runtime ephemeral`
- `agent-repl attach`
- `agent-repl promote-runtime`
- `agent-repl expire-runtime`
- `agent-repl review`

Likely UI affordances:

- runtime badge
- active session list
- follow-agent button
- live activity rail
- per-cell owner badge
- promote or discard controls for ephemeral work

These names are illustrative, not final API commitments.

## Acceptance Examples

### Example 1: Headless Agent, Human Opens Later

1. The agent creates a notebook and runs several cells with the editor closed.
2. The runtime remains alive in persistent shared mode.
3. The human opens the notebook thirty minutes later.
4. The notebook opens with current cells and outputs already present.
5. The editor auto-attaches to the existing runtime.
6. The human runs a new cell and can use objects the agent created earlier.

### Example 2: Human Watches the Agent Live

1. A human has the notebook open.
2. The agent attaches from the CLI.
3. The human sees the agent's presence badge.
4. A new code cell appears with agent attribution.
5. The cell fills in incrementally, then executes.
6. Output streams appear in place.
7. The run summary shows what the agent changed.

### Example 3: Same-Cell Collision

1. The human starts editing a code cell.
2. The agent tries to rewrite that same cell.
3. The system detects the overlap.
4. The agent write becomes a queued draft or branch-local proposal.
5. The human sees the pending proposal and can merge or reject it.
6. No silent overwrite occurs.

### Example 4: Ephemeral Investigation

1. The human asks the agent to try a risky refactor or debugging pass.
2. The agent starts an ephemeral runtime and branch-local surface.
3. The human can watch the work live.
4. The agent produces a run summary and diff.
5. The human promotes the result into persistent shared mode or discards it.

## Staged Rollout

### Stage 1: Reopen and Continue

Focus:

- automatic runtime re-attach
- no kernel picker on normal reopen
- explicit continuity status
- correct persisted notebook restore

Success condition:

- a completed notebook can be reopened and continued naturally when the runtime is still alive

### Stage 2: Live Presence and Activity

Focus:

- session presence
- agent activity states
- live run progress
- per-cell attribution

Success condition:

- a human opening during agent work can tell what is happening immediately

### Stage 3: Incremental Projection

Focus:

- operation replay instead of full snapshot replacement
- text deltas for active edits
- live output append events

Success condition:

- the notebook feels collaborative instead of periodically overwritten

### Stage 4: Collision Safety

Focus:

- lightweight leases
- same-cell conflict handling
- reviewable drafts for risky overlap

Success condition:

- human and agent can overlap safely without revert or corruption behavior

### Stage 5: Ephemeral Mode

Focus:

- runtime TTL
- promotion and discard
- branch-local review

Success condition:

- risky or exploratory work has a natural safe home outside the main shared runtime

## Architectural Consequences

This spec implies:

- runtime attachment must become explicit and durable
- reopen behavior must be runtime-aware, not file-only
- projection must evolve from coarse snapshot sync toward operation-aware sync
- presence and activity belong in core state, not just client memory
- ephemeral mode is not a small flag; it is part of the collaboration model

## Decision

The target user experience for `agent-repl` is not only "headless notebook execution."

It is:

- durable shared runtime continuity
- visible human-agent activity
- safe overlap without silent overwrite
- explicit promotion of risky or ephemeral work
- a notebook that feels like a joint canvas before, during, and after agent interaction

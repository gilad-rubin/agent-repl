# Runtime and Execution

This document defines the target execution architecture for `agent-repl`.

It focuses on authority, lifecycle, and recoverability rather than on specific implementation choices.

## Goal

Execution should become a first-class part of the shared runtime instead of something inferred from editor behavior.

The system should be able to answer clearly:

- what is running
- why it is running
- who started it
- what runtime owns it
- whether it can be resumed, canceled, or cleaned up

## Core Position

`agent-repl-core` owns execution intent and run state.

The runtime layer owns actual execution.

Jupyter kernels remain the primary execution backend.

## Lifecycle Vocabulary

We should use these terms consistently.

### Computer

This is the host machine or OS process environment where `agent-repl` runs.

At this layer, things can end because:

- the machine shuts down
- the OS reaps processes
- the user logs out
- the workspace moves or the environment disappears

A "long-lived" runtime does not mean "survives a powered-off machine."

It means:

- it survives normal client disconnect
- it survives editor close
- it survives notebook close
- it survives long enough to be meaningfully resumed within the same machine lifecycle

### Workspace

The workspace is the trust and policy boundary.

It owns:

- the core state
- session records
- runtime records
- branch policy
- environment compatibility rules

The workspace is what the user informally means by "the project."

### Document

The document is the notebook or branch-local notebook-like surface.

It owns:

- shared notebook content
- node identity
- outputs as shared state
- attachment to a runtime policy context

### Runtime

A runtime is the durable execution container tracked by `agent-repl`.

It is a policy object and lifecycle object, not just a raw process.

It answers:

- what document or branch this execution context belongs to
- what environment it uses
- whether it is shared, ephemeral, pinned, or headless
- whether it should be resumed, drained, expired, or reaped

### Kernel

A kernel is the actual execution backend process or session used by the runtime.

In normal notebook language, this is what people mean by "the notebook kernel."

Architecturally, the kernel should be treated as a child resource of a runtime.

That means:

- users may still say "kernel"
- but the product should make lifetime decisions at the runtime level
- a runtime may eventually support kernel restart, kernel replacement, or richer backends without changing the higher-level contract

## What "Open" and "Close" Mean

We need explicit lifecycle verbs.

### Open Notebook

Opening a notebook means a client attaches a projection to a document.

It should not imply:

- creating a new runtime
- picking a new kernel
- taking authority away from the core

### Close Notebook

Closing a notebook means the client projection disappears.

It should not imply:

- stopping the runtime
- killing the kernel
- losing the shared state

### Open Editor

Opening the editor means one client surface becomes available.

It should not define whether the runtime exists.

### Close Editor

Closing the editor means the projection client detaches.

It should not destroy long-lived work by itself.

### Start Runtime

Starting a runtime means:

- create or resume the execution context for a document or branch
- bind runtime identity and policy
- create or attach the underlying kernel

### Stop Runtime

Stopping a runtime means:

- mark the execution context as no longer resumable in place
- detach or terminate the underlying kernel
- preserve enough state for later inspection and recovery

This is the meaningful "close the kernel" action at the product level.

### Restart Kernel

Restarting the kernel means:

- the runtime identity survives
- the execution backend is replaced
- live in-memory objects are lost
- the system records that continuity was broken

This distinction matters because "same runtime" and "same memory" are not identical guarantees.

## Run Model

Runs should be explicit architectural objects.

They should exist independently of any particular client process and should be attributable to:

- an actor
- a session
- a document or branch context
- a runtime

This is what lets execution continue or be inspected after the initiating client disappears.

## Runtime Ownership

Runtimes should belong to document or branch policy contexts, not to notebook tabs or bridge lifetimes.

This should make it natural to support:

- interactive runtimes
- shared runtimes
- headless runtimes
- pinned runtimes
- ephemeral runtimes

## Runtime Scope

The lifecycle is true at several scopes, but not in the same way.

### Computer Scope

At computer scope, the question is:

- can the runtime survive normal user behavior without special babysitting

Examples:

- editor closes
- notebook closes
- the agent process exits

The desired answer is yes, as long as the host machine and workspace remain valid.

### Workspace Scope

At workspace scope, the question is:

- which runtimes should exist for this project and under what policy

Examples:

- one shared runtime for the main notebook line
- one ephemeral runtime for an agent experiment
- one pinned runtime for a long-running background computation

### Document Scope

At document scope, the question is:

- which runtime should this notebook or branch attach to
- whether the document is allowed to reuse an existing runtime
- whether continuity is available for this notebook specifically

### Kernel Scope

At kernel scope, the question is:

- is the actual execution backend alive, healthy, and attached to the runtime

This is the most concrete layer, but not the right layer for user-facing ownership policy by itself.

## Execution Lifecycle

The runtime architecture should make these states legible:

- queued
- starting
- running
- completed
- interrupted
- failed
- stale

The important part is that execution state is explicit, durable, and explainable.

## Runtime State Machine

The runtime lifecycle needs an explicit shared model before implementation.

These are the primary runtime states:

- `provisioning`
- `idle`
- `busy`
- `degraded`
- `detached`
- `draining`
- `stopped`
- `failed`
- `reaped`

These are the allowed meanings:

### `provisioning`

- the runtime record exists
- attach or start is in progress
- the backing kernel may still be starting
- no new runs should begin until promotion to `idle` or `busy`

### `idle`

- the runtime is healthy enough to accept work
- no run is currently executing
- reattach is allowed when policy permits

### `busy`

- the runtime is healthy enough to continue current work
- one or more runs are active or the execution queue is non-empty
- reattach for observation is allowed
- execution rights may still be restricted by policy or active ownership

### `degraded`

- the runtime is still alive
- continuity may still exist
- health checks say it is risky for new work without operator awareness
- reattach is allowed, but new execution should usually require confirmation or recovery action

### `detached`

- no client is currently attached
- the runtime is still resumable
- the runtime is a reap candidate once policy thresholds are met

### `draining`

- the runtime will not accept new work
- existing work may finish or be canceled
- this is the normal pre-stop state for explicit shutdown or policy cleanup

### `stopped`

- the runtime has ended cleanly
- live continuity is gone
- history and persisted outputs remain available

### `failed`

- the runtime or kernel died unexpectedly or became unusable
- automatic reattach for continuity is no longer valid
- history remains inspectable
- recovery must re-provision the runtime record with a replacement kernel or attach a replacement runtime behind the same runtime identity

### `reaped`

- the runtime record is terminal and cleaned up by policy
- only history and audit information remain

## Runtime Transitions

The minimum valid transitions are:

- `provisioning -> idle`
- `provisioning -> busy`
- `provisioning -> failed`
- `idle -> busy`
- `busy -> idle`
- `idle -> detached`
- `busy -> detached`
- `detached -> idle`
- `detached -> busy`
- `idle -> degraded`
- `busy -> degraded`
- `degraded -> idle`
- `degraded -> draining`
- `idle -> draining`
- `busy -> draining`
- `draining -> stopped`
- `draining -> failed`
- `idle -> failed`
- `busy -> failed`
- `detached -> failed`
- `stopped -> provisioning`
- `failed -> provisioning`
- `stopped -> reaped`
- `failed -> reaped`

Invalid examples:

- `reaped -> idle`
- `stopped -> busy`
- `failed -> busy`

Those should only happen through a new provisioning step.

## Mode Transitions

Runtime mode and runtime state are different dimensions.

Runtime modes may transition under explicit policy:

- `ephemeral -> shared`
- `ephemeral -> pinned`
- `shared -> pinned`
- `pinned -> shared`

Runtime modes should not silently transition because of incidental UI actions.

Examples:

- opening a notebook must not convert `ephemeral` to `shared`
- closing an editor must not convert `shared` to `ephemeral`

Promotion from `ephemeral` to `shared` is a user-visible collaboration event, not an implementation detail.

## Streaming and Visibility

Outputs should be visible as part of the shared state model.

That means:

- output belongs to runs and nodes
- later clients can inspect prior run state
- streaming output does not depend on one UI staying connected

## Headless and Shared Execution

The architecture should allow:

- a run started from the CLI to continue with no editor open
- a human to later inspect that run from VS Code
- multiple clients to observe the same runtime safely

This is one of the clearest places where v2 must depart from v1 behavior.

## Server-Managed Kernel Continuity

The current prototype direction should be:

- `agent-repl` starts and owns the kernel from the server side
- the kernel stays alive even when no editor is open
- later opening the notebook should attach the editor to that same live runtime
- human follow-up execution should continue from the same in-memory state when the runtime still exists

This is the concrete behavior we want from editor continuity.

The internal implementation may evolve, including through experiments with
packages such as `nextgen-kernels-api`, but the product contract should remain
the same:

- one runtime authority
- later-opened editors attach to it
- live memory continuity is preserved when possible

## Reattach Policy

Reattach should follow explicit decision rules.

The system should evaluate, in order:

1. document or branch match
2. runtime mode policy
3. environment compatibility
4. runtime health and state
5. active ownership and execution status

The default policy table should be:

| Condition | Action |
| --- | --- |
| One matching runtime, healthy, same document or branch, same environment | Auto-attach |
| One matching runtime, healthy, same document or branch, env drift detected but runtime still self-consistent | Attach with continuity warning; new execution may require confirm |
| Matching runtime in `busy` state and owned by another active session | Attach as observer; queue or restrict new execution by policy |
| Matching runtime in `degraded` state | Show degraded status; allow inspect; require explicit recovery for new execution |
| Matching runtime in `detached` state and still within reuse window | Auto-attach and mark resumed |
| Multiple matching runtimes, one pinned and others not pinned | Prefer pinned runtime |
| Multiple matching runtimes with one exact branch match and others workspace-only | Prefer exact branch match |
| Multiple equally valid runtimes | Do not guess silently; require explicit selection |
| No matching live runtime, persisted notebook exists | Open persisted state and offer explicit resume with a new runtime |
| No matching live runtime and no useful persisted state | Fresh start |

Environment compatibility should include at minimum:

- interpreter identity
- workspace root
- branch or document binding
- declared environment hash when available

The system should prefer correctness over reuse.

If reuse is ambiguous, it should not auto-attach.

## Kernel Restart Contract

Kernel restart needs an explicit contract because it breaks memory continuity while preserving higher-level identity.

When a kernel restart occurs:

- the runtime keeps the same runtime identity
- the kernel generation increments
- all in-flight runs move to `interrupted`
- queued runs move to `queued` or `blocked-restart` based on policy
- persisted outputs from earlier generations remain visible
- those outputs should be attributable to their run and kernel generation

After restart:

- new runs execute against the new kernel generation
- the system must show that live continuity was broken
- an observing human can still inspect old outputs without confusing them for current memory state

Agent recovery path:

- if the initiating agent session is still attached, it receives an explicit interruption event
- it may choose to rerun, repair, or abandon
- it must not silently assume old variables still exist

## Runtime Reuse

Runtime reuse should be policy-driven.

It should depend on:

- workspace trust boundary
- environment compatibility
- document or branch compatibility
- ownership and safety policy

It should not depend on whichever notebook happened to be in focus.

## Long-Lived Means

For `agent-repl`, a long-lived runtime should mean:

- it outlives any one CLI invocation
- it outlives notebook close
- it outlives editor close
- it can be reopened and reattached later
- it is visible in workspace runtime state
- it has an explicit cleanup or expiry policy

It does not necessarily mean:

- it survives machine reboot
- it survives environment deletion
- it keeps memory forever

The important product property is resumable continuity inside a normal workspace lifecycle, not magical immortality.

## Health and Resource Model

Long-lived runtimes need an explicit health model.

At minimum, health should consider:

- heartbeat freshness
- execution responsiveness
- kernel liveness
- memory pressure when observable
- repeated execution failures

The default health tiers should be:

- `healthy`
- `degraded`
- `failed`

Suggested default reactions:

| Health Condition | Action |
| --- | --- |
| Healthy | normal attach and execute |
| Degraded but alive | allow inspect and explicit continue; consider drain or restart recommendation |
| Failed | continuity unavailable; create or attach replacement runtime |

The system should not promise infinite healthy continuity.

Live kernel continuity is best-effort.

Reliable fallback is:

- persisted notebook state
- explicit runtime replacement
- durable run and output history

## Zombie Kernel Philosophy

Zombie kernels indicate unclear runtime ownership.

The architecture should make them rare by construction through:

- explicit runtime ownership
- runtime heartbeats
- activity tracking
- detached and draining states
- reaping policy
- durable run records

If a runtime remains alive, the system should know why.
If it dies, the system should say so clearly.

## Recovery

The system should recover execution state cleanly across:

- client disconnect
- core restart
- runtime crash
- reconnect to a still-live kernel

Recovery should mean reconciliation against known state, not inference from scattered signals.

## Relationship to Files and Clients

The execution model should remain independent of:

- whether the notebook file has been exported recently
- whether VS Code is currently open
- whether the initiating client still exists

Files and clients should reflect runtime truth, not define it.

## What Good Looks Like

The runtime model is healthy when:

- execution is explicit and attributable
- reconnect feels natural
- headless work is normal
- stale runtime cleanup is understandable
- zombie kernels become policy failures instead of mysterious background artifacts

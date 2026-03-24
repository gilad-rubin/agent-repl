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

## Execution Lifecycle

The runtime architecture should make these states legible:

- queued
- starting
- running
- completed
- interrupted
- failed
- stale

The important part is not the exact state machine.

The important part is that execution state is explicit, durable, and explainable.

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

## Runtime Reuse

Runtime reuse should be policy-driven.

It should depend on:

- workspace trust boundary
- environment compatibility
- document or branch compatibility
- ownership and safety policy

It should not depend on whichever notebook happened to be in focus.

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

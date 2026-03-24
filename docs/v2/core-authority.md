# v2 Core Authority and Sessions

This document goes one level deeper on the `agent-repl-core` authority model.

It is still an architecture document, not an implementation plan.

## Goal

The goal is to define the center of the system clearly enough that:

- every client knows what it is talking to
- every actor can be identified and resumed
- every meaningful action has a durable home
- we stop leaking authority into editor state or file state

## Core Authority

`agent-repl-core` should own the canonical live state for:

- documents
- nodes
- branches
- sessions
- runs
- actor attribution
- leases and ownership
- presence

If a client view, exported file, or kernel state disagrees with the core, the core wins and reconciliation happens outward from there.

## Session Model

A session should represent a live actor attachment to the workspace.

The important architectural properties are:

- it is attributable
- it is resumable
- it has explicit capabilities
- it can disconnect and reconnect without invalidating system state

Session kinds should include:

- human editor session
- human CLI session
- agent session
- system worker session

## Actor Model

The system should attribute actions to actors, not just to tools.

That means the runtime should distinguish:

- a human editing from VS Code
- a human launching from the CLI
- a background agent acting through automation
- a recovery or sync worker making system changes

This matters for:

- trust
- review
- visibility
- merge semantics
- ownership rules

## Presence

Presence should be a first-class architectural concept, not a UI afterthought.

The system should be able to express:

- who is attached
- where they are working
- whether they are active or idle
- what they appear to own or be reviewing

Agents do not need cursor presence in the same way humans do, but they do need visible work presence.

## Leases and Ownership

The architecture should make ownership explicit without turning it into a locking nightmare.

The desired behavior is:

- humans and agents can work in parallel safely
- mainline edits are protected from silent trampling
- risky work naturally moves into branches or scoped regions
- ownership is visible and reviewable

Leases should exist to clarify intent and protect collaboration, not to permanently block work.

## Continuity

A key design goal is that continuity belongs to the system, not to whichever client happened to be connected.

That means:

- client disconnect does not destroy the document state
- agent restart does not erase prior work context
- human reconnect should reveal current truth clearly
- daemon restart should restore enough state to continue safely

## Recovery Semantics

Recovery should not mean guessing.

The system should be able to recover from:

- client disconnect
- process restart
- daemon restart
- runtime death
- external file changes

And it should do so with explicit state transitions rather than silent fallback behavior.

## Branches and Sessions

Branches should not just be alternative document content.

They should also express collaboration intent:

- isolate risky work
- make review easier
- provide safe parallelism
- make agent ownership legible

Sessions and branches should work together rather than being separate concepts bolted on later.

## Canonical Relationship to Clients

The relationship should be:

- clients attach to the core
- clients consume projections
- clients emit explicit operations
- clients do not own the canonical live state

This should be equally true for:

- VS Code
- CLI
- future browser clients
- long-running agent workers

## Failure Mode Avoidance

This architecture is specifically meant to prevent:

- read and write paths seeing different identities
- editor-open versus editor-closed behavior changing the meaning of operations
- actor ambiguity
- disconnected agents losing their place
- branch semantics collapsing into duplicated files

## What Good Looks Like

The authority/session model is working when:

- every meaningful action can be attributed
- reconnect feels like continuation, not rediscovery
- the same document truth is visible from every client surface
- risky work naturally acquires an ownership or branch story
- collaboration semantics are easier to explain than in v1

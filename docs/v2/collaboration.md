# Collaboration, Branching, and Sub-Notebooks

This document describes how `agent-repl` should support safe, legible collaboration between humans and agents.

It focuses on the collaboration model, not the implementation mechanics.

## Goal

The goal is to make parallel notebook work feel natural instead of dangerous.

The system should make it easy to:

- divide work
- see who owns what
- isolate risky changes
- review before merge
- continue work across humans and agents

## Collaboration Model

Humans and agents should collaborate through the same shared runtime.

The architecture should assume:

- concurrent work will happen
- some of that work will overlap in time
- some of that work should not land directly in the main line

The system should help structure this safely rather than pretending serialization will always happen by accident.

## Actor Visibility

Collaboration becomes much safer when the system can say clearly:

- who is attached
- who changed what
- who appears to own or review a region
- who started a run
- which work is human-authored versus agent-authored

This visibility is essential for trust and review.

## Branches as Native Collaboration Tools

Branches should be a first-class collaboration primitive.

They should exist because the architecture expects:

- risky edits
- parallel work
- agent experiments
- staged review
- merge and rejection workflows

Branches should not feel like notebook file duplication with extra bookkeeping layered on afterward.

## Mainline Safety

The architecture should make it hard to silently trample important work in the main line.

That means:

- broad or risky agent work should naturally move to a branch
- human-owned mainline work should not be silently overwritten
- merges should be explicit and reviewable

## Leases and Ownership

Ownership should be visible and lightweight.

The system should support:

- explicit ownership signals
- scoped work regions
- temporary leases
- safe parallel editing

Ownership should help coordination.

It should not become a rigid locking system that makes collaborative work miserable.

## Review as a First-Class Step

Review should be built into the collaboration model, not treated as an optional extra.

The architecture should make it straightforward to review:

- branch-local changes
- agent-generated changes
- structural notebook changes
- risky runtime-affecting changes

This is especially important for human-agent collaboration, where trust comes from visibility and control.

## Sub-Notebooks

Some work deserves its own scoped notebook surface.

Sub-notebooks should support:

- delegated agent tasks
- isolated exploratory work
- reviewable intermediate outputs
- recursive notebook-like structure when useful

The system should be comfortable with notebook-like units nested or referenced within a larger document context.

## Agent Task Structure

Agent work should not just be invisible mutations against the main notebook.

The architecture should make it possible to represent:

- agent-owned tasks
- scoped work surfaces
- resumable delegated work
- clear handoff back to a human or another agent

This is one of the clearest differences between a notebook tool with automation and an actually agent-native notebook runtime.

## Parallel Work

Parallel work should be a normal condition.

The system should support:

- human and agent working in different regions
- two agents working on different branches
- a human reviewing while an agent continues somewhere else

Parallelism should not require copy-pasting notebooks or avoiding collaboration features.

## Merge Philosophy

Merge should be explicit, legible, and reviewable.

The architecture should assume:

- not every branch should merge
- not every conflict should be auto-resolved
- merge is a collaboration event, not just a structural operation

## Conflict Philosophy

Conflicts should be treated as understandable collaboration states, not mysterious corruption.

The system should be able to tell the difference between:

- harmless parallel work
- risky overlap
- mainline ownership conflicts
- runtime-sensitive conflicts

The architecture should make those distinctions clearer over time, not blurrier.

## Relationship to Sessions

Sessions and collaboration semantics should reinforce each other.

That means:

- ownership is attributable to an actor and session context
- review can trace back to who did what
- resumed work still carries intelligible context

## What Good Looks Like

The collaboration model is healthy when:

- human and agent work is visible and attributable
- branches feel normal rather than exceptional
- risky work naturally becomes reviewable
- parallel work feels safer than in v1
- sub-notebooks and delegated work have a natural place in the system

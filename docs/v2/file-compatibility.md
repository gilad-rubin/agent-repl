# v2 File Compatibility and Sync

This document defines the desired architectural relationship between `agent-repl` v2 and file-based notebook representations.

The goal is to keep compatibility strong without letting file format semantics become the center of the product.

## Core Position

`.ipynb` remains a first-class compatibility format.

It is not the live authority.

The live authority belongs to `agent-repl-core`.

## Why This Matters

v1’s roughest edges come from treating different file and editor paths as if they were interchangeable views of the same truth.

v2 should remove that ambiguity.

The system should be able to distinguish clearly between:

- canonical live state
- exported compatibility state
- imported external state
- external mutations that happen outside the live runtime

## Compatibility Philosophy

The architecture should preserve all of these truths at once:

- `.ipynb` is important and worth preserving
- `.ipynb` is useful for interoperability and user trust
- `.ipynb` is not rich enough to carry every v2 semantic cleanly
- richer internal semantics should not be flattened away just to satisfy the export format

## Projection, Not Authority

The file layer should work through projections.

That means:

- export is a projection from canonical state into `.ipynb`
- import is a projection into canonical state
- neither direction should silently redefine the internal architecture

## Full-Fidelity State

The architecture should allow richer v2 concepts to exist even when they do not map neatly to plain notebook structure.

Examples include:

- branches
- leases and ownership
- agent task structures
- richer review or merge state
- sub-notebook references

The system should preserve these concepts without forcing `.ipynb` to pretend it can express them natively.

## External File Changes

External changes to bound files should be treated as explicit events, not as invisible corruption.

The system should be able to say:

- a file changed on disk
- whether that change matches or diverges from live state
- whether it can be imported safely
- whether it requires user review or branch isolation

## Export Philosophy

Export should stay useful and trustworthy.

Users should continue to feel that:

- their notebooks remain portable
- standard tooling still matters
- they are not trapped in a private internal representation

At the same time, the export path should not force the internal model to stay linear or simplistic.

## Import Philosophy

Import should be safe, legible, and explicit.

The system should avoid pretending that an imported file is always a full description of the internal state.

Import should answer:

- what became canonical nodes
- what was preserved as compatibility-only structure
- what could not be represented fully without richer internal semantics

## Sidecar or Extended Persistence

The architecture should remain comfortable with the idea that full-fidelity restore may require data beyond the raw `.ipynb` file.

Whether that lives in a sidecar or another core-owned persistence boundary is an implementation question.

The architectural point is simpler:

- exported notebooks should remain useful on their own
- full v2 fidelity may require richer persistence than the compatibility format

## Relationship to Clients

Clients should not need to guess whether they are looking at:

- live canonical state
- exported file state
- stale external state

That distinction should be explicit in the architecture and visible in the product.

## Failure Mode Avoidance

This file model is specifically meant to prevent:

- disk state and live state disagreeing silently
- editor-open and editor-closed paths fabricating different identities
- imports and exports mutating the system through side effects instead of explicit transitions
- file overwrites becoming mysterious notebook corruption

## What Good Looks Like

The file compatibility model is working when:

- `.ipynb` remains useful and trustworthy
- richer v2 state survives without being forced into notebook-file contortions
- external file changes are understandable
- import and export are explicit compatibility boundaries rather than hidden authorities

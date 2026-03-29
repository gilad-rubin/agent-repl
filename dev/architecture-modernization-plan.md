# Architecture Modernization Plan

## Status

Proposed revision — 2026-03-29

Reviewed against:

- the shipped runtime/collaboration rollout in this repo
- the current shared canvas + browser preview worktree
- local collaboration-heavy tests run during this review
- current upstream guidance from Jupyter, Yjs, VS Code, CodeMirror, MCP/FastMCP, and jupyterlab-lsp

## Goal

Preserve the current CLI, notebook, VS Code, and browser functionality while replacing bespoke infrastructure with common open-source libraries and clearer boundaries where that meaningfully reduces code, drift, and maintenance cost.

This plan is intentionally pragmatic:

- keep what is already paying rent
- replace custom infrastructure where the ecosystem is clearly stronger
- avoid rewrites that mostly swap one custom pile for another
- migrate behind stable user-facing behavior and stable tests

## Non-Goals

- Rebuild the frontend around JupyterLab widgets or Lumino just to be “more Jupyter”
- Delete collaboration features that users already rely on just because CRDTs exist
- Big-bang rewrite of the daemon, extension, and browser preview together
- Trade the current shared-canvas direction for separate UI implementations per surface

## What We Should Keep

The repo already has several good architectural moves. Modernization should build on these, not undo them.

Keep:

- the shared runtime in `src/agent_repl/core/` as the product authority
- the shared React canvas bundle in `extension/webview-src/`
- VS Code and browser preview as clients of the same notebook surface
- Jupyter kernels as the execution backend
- projection/activity as the mechanism for editor continuity
- the newer pure shared UI logic modules in `extension/src/shared/`

In other words: the main opportunity is not “replace everything”. It is “reduce duplicated orchestration and replace custom infrastructure under the current shape”.

## Current Design Smells

These are the highest-value problems to remove first.

### 1. Repeated session-selection policy

The preferred-human-session ranking logic currently exists in three places:

- `src/agent_repl/cli.py`
- `extension/src/session.ts`
- `extension/scripts/standalone-server.mjs`

That is classic duplicate policy drift. Session reuse should be decided once by the core, not re-implemented by every client.

### 2. Multiple sources of truth for execution state

Execution truth is still split across:

- server runtime/activity state
- extension execution glue
- webview/browser local state

This is the root cause of status drift, especially for queued, running, paused, and “completed but not live” behavior.

### 3. A very large core god object

`src/agent_repl/core/server.py` currently owns routing, state, persistence, execution orchestration, collaboration policy, and transport details. It is doing too much, which makes even good functionality hard to evolve.

### 4. `hasattr(...)` feature detection in the CLI

The public CLI currently branches on client capability using `hasattr(...)` checks to decide whether it is talking to the core client or the bridge client. That is a broken abstraction boundary and an avoidable source of shotgun surgery.

### 5. Collaboration workflow and concurrency control are coupled together

Leases, presence, branches, and review handoff are all intertwined. Some of that is concurrency machinery, and some of it is actual product workflow. Those should not be treated as the same thing.

Important consequence:

- CRDT adoption should remove edit-collision mechanics where CRDTs cover them
- CRDT adoption should not automatically delete draft/review workflow features

### 6. Host-specific duplication around notebook plumbing

Kernel discovery, workspace tree walking, auth-aware HTTP helpers, and notebook-open logic are repeated across the extension routes, standalone browser server, and client helpers.

### 7. Custom LSP glue is spread across multiple owners

The notebook-to-Python virtual-document/LSP bridge is important enough to standardize, but not important enough to keep reinventing separately per surface.

### 8. The same notebook commands are implemented in multiple hosts

The command family around edit, execute, restart, run-all, kernel selection, runtime refresh, and status refresh is currently implemented separately in:

- the public CLI
- the VS Code editor proxy
- the standalone browser host

That is the core maintainability problem. The main architecture win is to move this behavior behind one application service layer with thin adapters, not to add one more transport.

## Recommended Target Stack

This is the pragmatic stack to converge toward.

| Concern | Recommended stack | What this means for `agent-repl` |
|---|---|---|
| Notebook structure + collaborative source state | [`jupyter_ydoc`](https://jupyter-ydoc.readthedocs.io/en/latest/overview.html), [`@jupyter/ydoc`](https://jupyter-ydoc.readthedocs.io/en/latest/api/index.html), [`jupyter_server_ydoc` / Jupyter collaboration architecture](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/developer/architecture.html), `pycrdt-websocket` | Reuse Jupyter’s YDoc notebook schema instead of inventing a custom CRDT schema |
| Presence | [Yjs Awareness](https://docs.yjs.dev/api/about-awareness) | Replace custom live-presence transport for collaborative editing, but keep `agent-repl` sessions for attribution/audit/ownership |
| Application service layer | Internal typed service objects + shared contracts | One owner for notebook commands, execution orchestration, session policy, and runtime inspection |
| ASGI host shell | ASGI app with typed request/response models | Replace the raw daemon handler with a host that can mount multiple adapters cleanly |
| Execution state + live updates | Core-owned execution ledger + event stream reused by adapters | Execution stays server-owned; UI surfaces subscribe to the server instead of guessing |
| External agent protocol | [MCP](https://modelcontextprotocol.io/docs/learn/architecture) tools/resources/prompts over [Streamable HTTP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) using [FastMCP](https://gofastmcp.com/deployment/http) | Expose `agent-repl` to external agent clients without inventing another bespoke remote-control API |
| UI HTTP surface | Optional thin HTTP adapter for browser/editor-specific needs | Keep only what the product UI actually needs beyond MCP; do not duplicate business logic |
| Operational persistence | SQLite via stdlib `sqlite3` | Sessions, runtimes, runs, execution records, activity ledger in a durable local DB |
| CRDT persistence | `pycrdt-store` or equivalent YDoc-backed persistence | Do not invent a second custom persistence format for collaborative documents if upstream already provides one |
| Notebook diff/migration verification | [`nbdime`](https://nbdime.readthedocs.io/) | Semantic notebook diffs for migration and round-trip parity checks |
| LSP virtual document model | [jupyterlab-lsp virtual document pattern](https://jupyterlab.readthedocs.io/en/stable/api/modules/lsp.VirtualDocument.html) + [jupyterlab-lsp extension guidance](https://jupyterlab-lsp.readthedocs.io/en/latest/Extending.html) + `vscode-languageclient` where it fits | Reuse the established notebook-to-virtual-document mapping model rather than growing a one-off mapper forever |
| Editor surface | [VS Code Custom Editor API](https://code.visualstudio.com/api/extension-guides/custom-editors), shared React canvas, [CodeMirror 6](https://codemirror.net/docs/guide) | Keep the current shared-canvas direction; do not replace it with JupyterLab UI packages |

## Transport Choices

Use different transports for different jobs instead of forcing one transport to do everything.

### Recommended default split

- `Streamable HTTP` for MCP exposure and any request/stream pattern that naturally fits MCP
- optional UI-specific HTTP or SSE only where the browser/editor clients need non-MCP ergonomics
- `WebSocket` for collaborative document sync if and when YDoc lands
- `stdio` for local MCP integrations and spawned local tools

### Why

`MCP` now standardizes `stdio` and `Streamable HTTP` as its standard transports. Streamable HTTP can optionally use SSE for server-to-client streaming, but `WebSocket` is not a standard MCP transport and should be treated as a custom transport only if we have a very specific reason to add it.

For `agent-repl`, that means:

- use `MCP Streamable HTTP` as the first default network protocol
- keep a thin non-MCP UI adapter only if the product clients are materially simpler that way
- use `WebSocket` only where bidirectional collaborative document sync actually benefits from it, such as a Yjs provider

This keeps the agent-facing API aligned with the MCP ecosystem while still using the right tool for high-frequency collaborative sync.

Important clarification:

- `Streamable HTTP` already supports JSON responses and SSE streams on the same endpoint
- `FastAPI + SSE` is therefore not a goal by itself
- the real goal is an `ASGI` host with typed contracts and thin adapters
- if plain MCP streams are sufficient for the browser/editor clients, do not add a separate SSE layer just because it is familiar

## Important Upstream Reuse Decisions

### Reuse FastMCP as the MCP adapter layer, not just the transport spec

The ecosystem has already solved a meaningful amount of MCP plumbing:

- `FastMCP` can run over HTTP, expose an ASGI app, and be mounted into a larger application
- it supports `stdio` and `Streamable HTTP`
- it can generate a typed CLI from MCP tool schemas for admin or power-user workflows

That makes it a better fit than inventing another custom agent adapter, and a better fit than treating MCP as just a wire format.

Recommended stance:

- use `FastMCP` as the agent-facing adapter layer
- keep `agent-repl` business logic below it in application services
- keep the human-oriented `agent-repl` CLI handcrafted where that UX matters
- consider generated or thin-wrapper CLI commands for admin or MCP-native workflows where schema-driven generation reduces maintenance

### Adopt Jupyter collaboration data models, not JupyterLab frontend UI

The best upstream reuse target is the collaborative document model, not the JupyterLab renderer stack.

Use:

- `jupyter_ydoc`
- `@jupyter/ydoc`
- `jupyter_server_ydoc`
- Yjs Awareness

Do not try to:

- replace the current shared React canvas with JupyterLab notebook widgets
- pull Lumino-heavy frontend infrastructure into the VS Code webview just to reduce a small amount of UI code

That would increase coupling without clearly reducing product complexity.

### Keep outputs and execution state outside the CRDT

This is the biggest design correction relative to simplistic “make the whole notebook collaborative” plans.

Use the CRDT for:

- cell order
- cell existence
- cell source
- user presence metadata relevant to editing

Keep server-owned and out of the YDoc:

- execution queue state
- running/queued/paused/failed status
- execution ownership
- kernel generation
- output streaming authority

Reason:

- outputs are produced by the runtime, not by collaborative text editing
- execution status needs server ownership anyway
- the current product already relies on ownership, runtime, and activity semantics that are richer than simple shared document state

### Do not delete branches just because CRDTs reduce edit conflicts

The current branch/review primitives are partly conflict machinery and partly product workflow.

After CRDT adoption:

- edit leases for cell source/structure should likely disappear
- structural locks should likely disappear
- branch/draft/review flows should be re-evaluated as workflow features, not auto-removed

If branch review remains valuable for agent experiments or handoff, it should survive as an explicit workflow concept.

## Recommended Migration Sequence

This order is optimized for highest payoff with the least risky churn.

### Phase 0: Contract Consolidation Before Big Swaps

Do this first.

Goals:

- remove duplicated policy
- define stable internal contracts
- make later migrations smaller

Changes:

1. Define typed request/response models for runtime, notebook, activity, execution, session, and projection payloads.
2. Replace CLI `hasattr(...)` feature detection with an explicit client protocol and adapters.
3. Move “preferred reusable human session” selection into the core so clients ask for reuse instead of implementing ranking.
4. Consolidate kernel-discovery and workspace-tree logic so it has one owner.
5. Continue the `extension/src/shared/` direction: pure reducers/helpers for command routing, activity application, execution transitions, and status derivation.

Why this phase exists:

- it removes duplicated logic without changing product behavior
- it makes later ASGI/MCP/YDoc changes much less invasive

### Phase 1: Build One Application Service Layer

Do this before transport work.

Goals:

- stop implementing notebook commands separately per host
- make CLI, MCP, VS Code, and browser adapters all call the same business logic
- define clear seams between domain services and transport adapters

Changes:

1. Introduce application services for:
   - notebooks
   - execution
   - sessions/presence
   - runtimes
   - activity/projection
2. Move notebook command orchestration out of:
   - the CLI
   - `editor/proxy.ts`
   - the standalone browser host
3. Make `BridgeClient` and `CoreClient` converge on one transport/client foundation.
4. Keep host-specific code limited to:
   - UI messaging
   - auth boundary concerns
   - local environment integration
5. Define one canonical request/response/event contract and make all adapters depend on it.

Why this phase exists:

- it directly attacks the current duplicate-command problem
- it shrinks the amount of code that later transport changes have to touch
- it is the most important consolidation step for maintainability

### Phase 2: Unify Execution Truth

This is still the highest-payoff technical migration.

Changes:

1. Add a core execution ledger with explicit `queued`, `running`, `finished`, `failed`, `paused`, `canceled` states.
2. Add execution IDs, source hashes, notebook revision tracking, and kernel-generation checks.
3. Move queue ownership to the core so clients never infer queue position from UI state.
4. Add async execution endpoints that return an execution record immediately.
5. Use one UI model:
   - `pending`: local request in flight only
   - `queued/running/finished/...`: server-confirmed only
6. Treat the existing runtime state machine work as part of this phase, whether via a small declarative library or an internal typed transition layer.

Do not:

- keep optimistic running state once the execution ledger exists
- keep separate queue semantics in `queue.ts`, `editor/proxy.ts`, and browser host code

### Phase 3: Move to an ASGI Host Shell

Do this after contracts, service consolidation, and most execution truth work.

Changes:

1. Replace the raw `ThreadingHTTPServer` routing layer with an ASGI host shell.
2. Keep the loopback + token-auth boundary.
3. Keep the standalone browser proxy so browser JS never receives the daemon token.
4. Preserve existing public JSON response shapes wherever possible.
5. Mount the MCP adapter into the same ASGI host.
6. Add a UI-specific stream endpoint only if the product clients still need one after MCP is in place.

Why the ASGI host is worth it here:

- typed models and middleware pay off immediately
- one host can mount MCP and any remaining product routes together
- it shrinks the “god handler” problem even before deeper collaboration work lands

MCP note:

- add MCP as an adapter layer, not as the internal protocol between the UI and the core
- expose stable notebook/runtime tools, resources, and prompts through MCP
- keep `agent-repl` collaboration sessions distinct from MCP transport sessions

### Phase 4: Split Persistence Responsibilities

Changes:

1. Move operational state to SQLite:
   - sessions
   - runtimes
   - runs
   - execution records
   - activity events
2. Keep the schema focused on actual operational state, not speculative future abstractions.
3. If collaborative docs move to YDoc, store them using the YDoc persistence layer rather than inventing a parallel JSON blob format.

Important rule:

- operational state and collaborative-document state are related, but they are not the same storage problem

### Phase 5: Add FastMCP and Rationalize CLI/MCP Boundaries

Do this once the ASGI shell and application services exist.

Changes:

1. Mount `FastMCP` as the MCP adapter over the shared application service layer.
2. Expose stable notebook/runtime capabilities as tools/resources/prompts.
3. Keep `stdio` available for local and spawned use cases.
4. Decide which CLI flows should stay handcrafted and which can be schema-driven or thin wrappers over MCP-compatible services.
5. Ensure there is no duplicated business logic between CLI commands and MCP tool handlers.

Decision rule:

- if a behavior is product-facing and ergonomics-heavy, keep a handcrafted CLI command
- if a behavior is admin-like, schema-driven, or naturally tool-shaped, reuse MCP contracts and code paths

### Phase 6: YDoc Spike

Do a real spike before promising full CRDT migration.

Spike questions:

1. Can `jupyter_ydoc` model the notebook structure you actually ship without lossy conversions?
2. Does `jupyter_server_ydoc` fit as embedded infrastructure, or do you need a thinner adapter around `jupyter_ydoc` + `pycrdt-websocket`?
3. Can the CLI continue to mutate notebooks via plain HTTP commands while the server owns YDoc mutation?
4. What is the clean mapping from `agent-repl` session identity to Awareness presence state?
5. What remains a workflow concept after edit conflicts no longer require leases?

Spike deliverables:

- two-client collaborative edit prototype
- notebook round-trip tests on real fixtures
- migration/no-migration decision for branches and review flows
- measured decision on whether to embed `jupyter_server_ydoc` vs. use the underlying pieces directly

### Phase 7: Migrate Collaborative Editing

Contingent on the spike.

Move to YDoc for:

- cell insert/delete/move
- source edits
- collaborative presence for editing

Keep separate for now:

- execution state
- runtime ownership
- execution/output events
- review/draft workflow until explicitly redesigned

Expected deletions after parity:

- source-edit cell leases
- structure leases
- bespoke source-delta transport where YDoc already covers it

Expected survivors:

- session records
- activity/audit events
- runtime ownership model
- some workflow-level review/draft concept if it still serves users

### Phase 6: Standardize Notebook LSP

Do this after execution truth and transport are stable.

Changes:

1. Align the notebook virtual-document model with jupyterlab-lsp patterns.
2. Keep one notebook-to-virtual-document mapping model for all surfaces.
3. Use `vscode-languageclient` in the extension host where that reduces custom client glue.
4. Keep Pyright as the backend unless a different server is needed for capability reasons.

Pragmatic note:

- this is an important cleanup, but it is not the first modernization win

### Phase 7: Delete Compatibility Debt

Only after the new path is stable and test-covered.

Delete:

- duplicated session-selection implementations
- raw HTTP routing code
- leftover queue/status logic that is no longer authoritative
- obsolete lease paths for edited cell/source structure
- stale compatibility shims that only existed to bridge old and new execution models

## What Not To Rewrite

These changes are low-value or actively harmful right now.

Do not prioritize:

- replacing `marked` + `DOMPurify` unless you have a concrete markdown feature gap
- replacing the shared React canvas with a different frontend stack
- rewriting all CodeMirror code just to use a different React wrapper
- moving to a distributed task queue before the local execution model is clean
- replacing `sqlite3` with a heavier ORM before the schema stabilizes
- replacing the core HTTP/UI transport with pure MCP before the product APIs are cleanly separated

## Regression Strategy

This modernization only succeeds if parity is proved continuously.

Regression coverage is part of done for every phase.

Behavior-lock coverage is also part of done for every phase:

- if a modernization slice touches a user-visible workflow decision that is easy to lose in a rewrite, update the relevant entry under `dev/behavior-locks/`
- if the behavior-lock entry does not point at an automated test yet, add or strengthen that test in the same slice
- treat those behavior-lock entries as product contract, not as optional commentary

Behavior-lock docs are also part of done for UX-sensitive changes:

- if a user-visible behavior is easy to lose during consolidation, capture it in `dev/behavior-locks/`
- each behavior lock should point to the regression test that preserves it
- if a behavior intentionally changes, update the behavior lock in the same change

Behavior-lock inventory is also part of done for every phase that touches user-visible behavior. If a subtle UX or workflow decision is important enough to preserve, it should appear under `dev/behavior-locks/` with links to the proving tests.

In addition:

- preserve a behavior-lock inventory for user-visible decisions that are easy to lose during rewrites
- every non-obvious behavior in that inventory should map to at least one automated test

### Baseline From This Review

These local checks were run during the 2026-03-29 review:

- `uv run pytest tests/test_agent_repl.py -q -k 'session or projection or activity or lease or branch or open or restart_run_all or execute_visible'`
  - result: `102 passed`
- `cd extension && node --test tests/editor-proxy.test.js tests/session-auto-attach.test.js tests/standalone-server.test.js tests/routes-background.test.js tests/editor-webview.test.js tests/notebook-command-controller.test.js tests/execution-state.test.js tests/cell-status.test.js`
  - result: `60 passed`
- `cd extension && node --test tests/preview-webview.smoke.js`
  - result: `21 passed`, `1 failed`
  - current known failure: `running that reused trailing cell creates the next trailing cell too`

That last browser failure is important. Fix it or explicitly bless it as a known bug before using the preview smoke suite as a migration gate.

### Required Long-Lived Test Layers

Keep these layers green throughout the migration:

1. Python core/runtime tests
2. CLI contract tests
3. Extension unit tests
4. Browser preview smoke tests
5. Behavior-lock inventories linked to executable tests
6. Cross-surface parity tests
7. Migration/round-trip tests
7. Behavior-lock inventory coverage for hidden product decisions

### Behavior-Lock Requirement

Not every important product decision is currently described in architecture docs.

Some are only visible in:

- keyboard shortcut handlers
- notebook command routing
- focus-management flows
- trailing-cell behavior
- session reuse and auto-attach rules

Treat those as product contract too.

Rule:

- if a user-visible behavior matters and is easy to accidentally change during a refactor, give it a named behavior-lock entry and back it with an automated test

### Minimum Regression Matrix

Every phase should preserve the following behaviors.

#### CLI and Public Contract

- `new`, `open`, `ix`, `edit`, `exec`, `run-all`, `restart`, `restart-run-all`, `status`, and `cat` keep stable success/error JSON shapes
- default session reuse prefers an attached human editor session before creating a new human CLI session
- explicit `--session-id` always wins over automatic reuse
- `new --open` and `open` preserve `canvas` default behavior
- browser-target opens the standalone preview URL with the correct encoded notebook path
- hidden `core ...` commands keep their machine-readable output stable enough for the extension and browser helper processes

#### Session, Presence, and Projection

- VS Code auto-attach reuses the preferred human session
- browser standalone attach reuses the preferred human session
- CLI notebook commands join the same reusable human session when appropriate
- session heartbeat/touch updates do not create duplicate sessions
- presence upsert/clear works across CLI, VS Code, and browser clients
- activity cursor handling does not skip same-timestamp events
- projection attaches to an already-running headless runtime
- dirty notebooks do not get overwritten by remote snapshots
- inserted-cell, deleted-cell, source-update, and output-append activity apply incrementally where expected
- deleting a currently executing cell clears active execution state correctly

#### Execution and Runtime

- queued cell shows queued until the server marks it running
- running cell transitions to completed/failed/paused based on server truth, not persisted notebook metadata
- persisted outputs do not appear as fake live completion on initial load
- run-all preserves execution order
- restart increments kernel-generation/continuity state
- restart-and-run-all preserves session ownership
- interrupt clears execution state correctly
- degraded runtimes reject new runs until recovered
- source divergence after submission is surfaced explicitly instead of silently overwriting history
- deleting or mutating a cell during execution follows a deliberate stale-output policy

#### Browser Preview and Shared Canvas UX

- command mode vs edit mode routing stays correct
- `a`, `b`, `dd`, `m`, `y`, `Enter`, `Shift+Enter`, arrow keys, and `Escape` preserve parity expectations
- `Cmd/Ctrl+B` toggles the browser explorer and never inserts a cell
- workspace preview chooses a notebook and can switch notebooks from the explorer
- output copy controls preserve line breaks and chunking semantics
- `Shift+Enter` uses the latest in-editor source without a stale draft race
- running the last code cell inserts the next trailing cell inline
- trailing-cell reuse after restart keeps working
- browser preview and VS Code canvas show the same notebook state for the same runtime

#### Native-Notebook and Background-Operation Compatibility

- background insert/execute/restart flows do not steal focus or force notebook UI open
- open route defaults to the custom canvas editor
- browser open route uses the standalone preview instead of the VS Code editor
- native notebook compatibility path remains thin and delegates to the core execution model once migrated

#### Collaboration Workflow

- self-conflicts continue to fall back to owned-cell execution where intended
- review request / resolve behavior remains covered as long as branch workflow exists
- branch-backed handoff hints remain attributable to sessions
- if branches are redesigned, replacement workflow tests must exist before branch code is deleted

#### Persistence and Migration

- JSON-state to SQLite migration preserves sessions, runtimes, runs, and activity data
- notebook/YDoc round trips preserve notebook semantics on real fixture notebooks
- migration tests compare notebooks semantically, not just by raw JSON ordering
- daemon restart after partial persistence failure recovers cleanly or fails loudly with diagnostics
- activity retention and cleanup rules are test-covered

#### LSP

- cross-cell imports and diagnostics map back to the correct cell and range
- completions and diagnostics stay stable after insert/delete/move operations
- browser preview and VS Code canvas show equivalent diagnostics for the same notebook content
- restarting or reconnecting the LSP bridge does not orphan stale diagnostics

### Test Additions I Would Require Before or During Migration

Add these if they do not already exist:

- golden notebook fixtures for:
  - pure code notebooks
  - markdown + code notebooks
  - notebooks with streamed output
  - notebooks with error/traceback output
  - notebooks with rich output payloads
  - notebooks that start with blank trailing cells
- semantic notebook round-trip tests using `nbdime`
- property-style edit-operation tests that randomize insert/delete/move/replace sequences and assert valid notebook structure and stable cell IDs
- Streamable HTTP resumability tests with `Last-Event-ID`
- UI stream fallback tests if a dedicated browser/editor stream endpoint still exists
- multi-client collaborative-edit tests once YDoc is introduced
- crash/restart recovery tests for execution ledger persistence
- parity tests that exercise the same notebook scenario through:
  - CLI
  - VS Code custom editor
  - browser preview

## Success Criteria

This modernization is successful when:

1. the same user-visible workflows still work from CLI, VS Code, and browser
2. less logic is duplicated across clients
3. execution state comes from one authoritative model
4. collaborative document state uses established upstream document models instead of custom schema work
5. the core becomes easier to read because transport, persistence, execution, and collaboration concerns are separated
6. the regression suite gets stronger as the codebase gets smaller

## External References

These were the most useful upstream references for this revision:

- [Jupyter YDoc Overview](https://jupyter-ydoc.readthedocs.io/en/latest/overview.html)
- [Jupyter Collaboration Architecture](https://jupyterlab-realtime-collaboration.readthedocs.io/en/latest/developer/architecture.html)
- [Yjs Awareness](https://docs.yjs.dev/api/about-awareness)
- [VS Code Custom Editor API](https://code.visualstudio.com/api/extension-guides/custom-editors)
- [CodeMirror 6 Guide](https://codemirror.net/docs/guide)
- [MCP Architecture Overview](https://modelcontextprotocol.io/docs/learn/architecture)
- [MCP Transport Specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [FastMCP HTTP Deployment](https://gofastmcp.com/deployment/http)
- [FastMCP Generate CLI](https://gofastmcp.com/cli/generate-cli)
- [JupyterLab LSP VirtualDocument](https://jupyterlab.readthedocs.io/en/stable/api/modules/lsp.VirtualDocument.html)
- [jupyterlab-lsp Extension Guidance](https://jupyterlab-lsp.readthedocs.io/en/latest/Extending.html)

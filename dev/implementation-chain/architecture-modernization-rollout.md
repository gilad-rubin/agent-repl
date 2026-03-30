# Architecture Modernization Rollout

This document breaks the modernization plan into implementation pieces that are small enough to ship incrementally while keeping CLI, VS Code, and browser behavior stable.

Primary references:

- [Architecture Modernization Plan](/Users/giladrubin/python_workspace/agent-repl/dev/architecture-modernization-plan.md)
- [Current Architecture](/Users/giladrubin/python_workspace/agent-repl/dev/current-architecture.md)
- [VS Code Jupyter Parity Checklist](/Users/giladrubin/python_workspace/agent-repl/dev/jupyter-parity-checklist.md)

## ClickUp Chain

- Parent task: [`869cp5w9u` Architecture modernization rollout chain v2](https://app.clickup.com/t/869cp5w9u)
- Superseded prior chain: [`869cp5gfn` Architecture modernization rollout chain](https://app.clickup.com/t/869cp5gfn)
- Prior completed dependency: [`869cmruk9` Agent-repl runtime lifecycle rollout](https://app.clickup.com/t/869cmruk9)

## Chain Rules

- Each piece should land with code, tests, and doc updates together.
- Each piece should preserve the current public CLI JSON contract unless the piece explicitly scopes a contract change.
- Each piece should update the behavior-lock inventory when it touches hidden UX or workflow decisions that are currently encoded mostly in code/tests.
- Each piece should leave the next piece easier, not harder.
- The last step of each implementation task is:
  - open the next task in the chain
  - load its context and acceptance criteria
  - begin implementation immediately unless blocked

## Known Baseline Risk

At the time this chain was revised, the targeted Python and extension collaboration suites were green, but the browser preview smoke suite still had one failing case around trailing-cell reuse after restart. Treat that as a live regression to fix or explicitly carry forward before using the preview smoke suite as a hard regression gate.

## Piece 0: Shared Contracts and Duplicate Policy Removal

ClickUp: [`869cp5wg9`](https://app.clickup.com/t/869cp5wg9)

### Goal

Remove duplicated policy and create typed internal contracts so later service, transport, persistence, and collaboration changes stop rippling across CLI, extension, and browser code.

### HLD

- Define explicit internal request/response/event models for sessions, notebook contents, activity, projection, runtime status, and execution responses.
- Replace CLI `hasattr(...)` feature detection with a clean client protocol or adapter boundary.
- Move preferred-human-session selection into one authoritative core helper/API.
- Consolidate notebook-open, workspace-root, and kernel-discovery policy so clients stop re-implementing it.
- Expand the `extension/src/shared/` pattern for pure reducers/helpers that can be tested without VS Code or browser hosts.
- Start or update the behavior-lock inventory under `dev/behavior-locks/` for subtle user-visible decisions that are currently encoded in code/tests.

### Acceptance Criteria

- Session reuse policy has exactly one owner.
- CLI no longer chooses code paths via `hasattr(...)`.
- Shared payloads have typed definitions and no longer drift between clients.
- VS Code and browser hosts consume shared helpers for status and command behavior where practical.
- No public behavior regression for `new`, `open`, `edit`, `exec`, `run-all`, `restart`, `restart-run-all`, `status`, and `cat`.

### Tests

- Add or update unit tests for the new client protocol and adapter layer.
- Add regression tests proving preferred-human-session reuse works identically across CLI, VS Code, and browser helper code.
- Add tests for shared payload model serialization and deserialization.
- Add or update behavior-lock entries for every subtle user-visible decision touched by the phase, with links to proving tests.
- Keep targeted Python core collaboration tests green.
- Keep extension tests for `session-auto-attach`, `standalone-server`, `routes-background`, and shared notebook command logic green.

## Piece 1: Shared Application Service Layer and Client Consolidation

ClickUp: [`869cp5wgq`](https://app.clickup.com/t/869cp5wgq)

### Goal

Stop implementing notebook and runtime commands separately per host by introducing one application service layer that every adapter calls.

### HLD

- Introduce application services for notebooks, execution orchestration, sessions, runtimes, and activity.
- Refactor CLI handlers, editor proxy flows, and standalone browser flows to call the same service layer instead of each owning notebook command logic.
- Converge `BridgeClient` and `CoreClient` on one transport/client foundation.
- Keep host-specific code limited to UI messaging, auth boundary concerns, and local environment integration.
- Define one canonical request/response/event contract and make all adapters depend on it.

### Acceptance Criteria

- The command family around edit, execute, execute-all, restart, restart-and-run-all, select-kernel, runtime refresh, and status refresh has one owner.
- CLI, editor proxy, and standalone browser host no longer contain independent orchestration logic for the same notebook operation.
- `BridgeClient` and `CoreClient` share a clear foundation instead of drifting in parallel.
- Adding a new notebook command no longer requires parallel edits in multiple host-specific implementations.

### Tests

- Add shared service-layer tests for notebook command orchestration.
- Add regression tests proving CLI, editor proxy, and standalone browser invoke the same underlying behavior for the same scenario.
- Add tests covering shared client error handling and polling behavior.
- Keep browser preview smoke and editor proxy tests green while removing duplicated host logic.

## Piece 2: Core Execution Ledger and Authoritative Status

ClickUp: [`869cp5wj7`](https://app.clickup.com/t/869cp5wj7)

### Goal

Make execution truth server-owned so queued, running, completed, paused, canceled, and failed state no longer depends on client-side inference.

### HLD

- Add explicit execution records with `execution_id`, `cell_id`, `status`, queue position, owner session, source hash, notebook revision, and kernel generation.
- Introduce async execute endpoints that return an execution record immediately.
- Move queue ownership into the core.
- Keep a local-only `pending` concept in UI surfaces, but derive `queued` and `running` from server truth only.
- Define stale-output policy for deleted or changed cells and restarted kernels.

### Acceptance Criteria

- A second submitted cell is visible as queued before it starts.
- Server activity and runtime responses can explain every state transition.
- Persisted notebook metadata never rehydrates as fake live completion.
- Deleted or changed-cell execution outcomes follow a deliberate policy instead of silently overwriting outputs.
- Extension and browser clients no longer need their own truth model for queue position.

### Tests

- Add core tests for queued-to-running-to-finished transitions.
- Add tests for queue ordering and queue position.
- Add tests for source divergence, deleted cell, and kernel generation mismatch.
- Add extension/browser tests for pending vs queued vs running status handling.
- Keep `editor-proxy`, execution-state, cell-status, and targeted activity/projection tests green.

## Piece 3: ASGI Host Shell and Unified Transport Surface

ClickUp: [`869cp5wjw`](https://app.clickup.com/t/869cp5wjw)

### Goal

Replace the raw daemon routing layer with a typed ASGI host that can mount MCP and any remaining UI/product routes without duplicating business logic.

### HLD

- Replace the raw `ThreadingHTTPServer` handler with an ASGI application shell.
- Use typed request and response models plus shared middleware for auth, diagnostics, and error shaping.
- Keep the standalone browser proxy as the auth boundary so browser JS never gets daemon credentials.
- Mount the MCP adapter into the same ASGI host.
- Add a dedicated UI stream endpoint only if browser/editor clients still need one after MCP-backed flows exist.

### Acceptance Criteria

- Existing CLI and extension flows continue to work through the new host.
- The raw god-handler shape in `core/server.py` is materially reduced.
- MCP and any remaining product routes share the same host and middleware stack.
- Error responses are typed and more diagnosable than the current hand-rolled JSON failures.

### Tests

- Add service-host tests for request validation and auth middleware.
- Add stream/resumability tests for whichever event path survives in the final host.
- Run targeted CLI and extension contract tests against the updated host.
- Keep browser standalone auth-boundary behavior covered.

## Piece 4: SQLite Operational Persistence

ClickUp: [`869cp5wkk`](https://app.clickup.com/t/869cp5wkk)

### Goal

Keep operational state in a durable, queryable local database.

### HLD

- Store sessions, runtimes, runs, execution records, and activity events in SQLite.
- Use WAL mode for read/write concurrency.
- Create the required operational tables on open.
- Keep operational state separate from future collaborative document persistence.

### Acceptance Criteria

- Restarting the daemon preserves operational state without JSON corruption risk.
- Activity history and execution records survive restart.
- Existing status and inspection commands still return the expected information.
- Recovery from partial writes is diagnosable and safe.

### Tests

- Add restart-safety tests for SQLite persistence.
- Add crash-safety/restart tests around partially written state.
- Add tests for current table creation and reload behavior.
- Keep `core status`, activity, runtime, and session tests green.

## Piece 5: FastMCP Adapter and CLI/MCP Convergence

ClickUp: [`869cp5wm7`](https://app.clickup.com/t/869cp5wm7)

### Goal

Expose `agent-repl` as a first-class agent platform through FastMCP while reusing the same application service layer and reducing duplicated CLI/MCP behavior.

Current shipped baseline before the remaining work:

- public MCP onboarding exists through `agent-repl mcp setup|status|config|smoke-test`
- public CLI onboarding helpers exist through `agent-repl setup`, `agent-repl doctor`, and `agent-repl editor configure --default-canvas`
- the canonical MCP endpoint is `/mcp`, with `/mcp/mcp` retained as a compatibility alias

### HLD

- Mount a FastMCP server on top of the shared application service layer.
- Use Streamable HTTP as the networked MCP transport.
- Keep the networked MCP transport focused on Streamable HTTP.
- Model stable notebook/runtime capabilities as MCP tools/resources/prompts.
- Decide which CLI flows stay handcrafted for human UX and which become thin wrappers over shared MCP-compatible services.

### Acceptance Criteria

- External MCP clients can create/open/edit/execute notebooks and inspect runtime/activity state through MCP.
- MCP shares the same application-service authority as CLI and UI surfaces.
- No duplication of business logic exists between MCP handlers and current public commands.
- CLI and MCP boundaries are documented clearly enough that future features know where to land.

### Tests

- Add MCP smoke tests for tool invocation and resource reads.
- Add tests for auth/session separation between MCP and collaboration sessions.
- Add regression tests proving MCP uses the same core side effects as CLI for the same operations.
- Add at least one CLI regression test proving a converged CLI/MCP path still preserves human-facing output expectations.

## Piece 6: YDoc Collaboration Spike

ClickUp: [`869cp5wme`](https://app.clickup.com/t/869cp5wme)

### Goal

Prove or disprove that Jupyter’s YDoc stack is the right replacement for custom notebook edit concurrency.

### HLD

- Prototype notebook structure/source syncing with `jupyter_ydoc`, `@jupyter/ydoc`, and a Python-side YDoc transport/persistence stack.
- Evaluate whether to embed `jupyter_server_ydoc` or compose directly from `jupyter_ydoc` plus WebSocket/provider pieces.
- Map `agent-repl` session identity to collaborative presence/Awareness.
- Keep execution/output state server-owned and outside the CRDT.

### Acceptance Criteria

- Two clients can edit the same notebook source/structure without lease conflicts.
- Notebook round-trips remain semantically stable on real fixtures.
- The spike results in a clear go/no-go and implementation recommendation.
- The team understands which current branch/review behaviors are workflow features versus edit-conflict machinery.

### Tests

- Add two-client collaborative-edit spike tests.
- Add semantic notebook round-trip tests using notebook fixtures and `nbdime`.
- Add presence/Awareness mapping tests.
- Add benchmark notes where they materially affect the decision.

## Piece 7: YDoc Migration for Notebook Editing

ClickUp: [`869cp5wmx`](https://app.clickup.com/t/869cp5wmx)

### Goal

Replace custom source/structure concurrency logic with YDoc-backed notebook editing while preserving the product’s higher-level workflow semantics.

### HLD

- Move cell insert/delete/move and source editing to YDoc mutation.
- Replace custom presence updates for editing with Yjs Awareness where appropriate.
- Remove source-edit and structure leases once parity is reached.
- Preserve execution, outputs, sessions, and activity as core-owned concerns.
- Re-evaluate branch/review flows and keep them only if they still serve product workflow needs.

### Acceptance Criteria

- Notebook editing parity holds across CLI, VS Code, and browser.
- Source and structural conflicts no longer require custom lease workflows.
- Activity/projection still works with the new document model.
- Workflow features that survive have explicit rationale and tests.

### Tests

- Add parity tests for edit operations through CLI, VS Code canvas, and browser preview.
- Add multi-client collaboration tests for insert/delete/move/replace sequences.
- Add property-style edit-sequence tests that preserve notebook validity and cell identity.
- Remove obsolete lease tests only after equivalent YDoc-based coverage exists.

## Piece 8: Notebook LSP Standardization

ClickUp: [`869cp5wn2`](https://app.clickup.com/t/869cp5wn2)

### Goal

Replace the current custom notebook/Pyright glue shape with a notebook virtual-document model aligned to jupyterlab-lsp patterns.

### HLD

- Introduce a single notebook-to-virtual-document mapping model.
- Reuse jupyterlab-lsp virtual-document concepts for position mapping and document identity.
- Use `vscode-languageclient` in the extension host where it reduces custom protocol glue.
- Keep diagnostics/completions consistent across browser preview and VS Code canvas.

### Acceptance Criteria

- Diagnostics and completions map back to the correct cell/range after notebook edits.
- Browser and VS Code surfaces present equivalent diagnostic state for the same notebook.
- Restart/reconnect behavior does not leave stale diagnostics behind.

### Tests

- Add mapping tests for cross-cell imports and edits.
- Add diagnostic translation tests after insert/delete/move operations.
- Add extension/browser parity tests for diagnostics and completions.

## Piece 9: Cleanup, Deletion, and Parity Hardening

ClickUp: [`869cp5wnd`](https://app.clickup.com/t/869cp5wnd)

### Goal

Delete superseded code, tighten docs, and leave the project easier to understand than it is today.

### HLD

- Remove raw routing code, obsolete queue/status code, duplicated policy, retired lease paths, and transport scaffolding that no longer pays rent.
- Reduce the core/server “god object” surface by keeping clear service boundaries.
- Update durable docs to reflect the shipped result, not the plan draft.
- Close the loop on known parity gaps, including the current preview trailing-cell regression.

### Acceptance Criteria

- All deleted code is covered by replacement tests.
- Docs describe the final shipped architecture clearly.
- Known parity bugs are either fixed or explicitly documented.
- The final code layout is simpler to explain by module responsibility.

### Tests

- Run the full Python test suite.
- Run the full extension test suite.
- Run the browser preview smoke suite and clear the known failing case.
- Add any final parity or regression tests revealed during cleanup.

## Suggested Chain Order

1. Piece 0: Shared Contracts and Duplicate Policy Removal
2. Piece 1: Shared Application Service Layer and Client Consolidation
3. Piece 2: Core Execution Ledger and Authoritative Status
4. Piece 3: ASGI Host Shell and Unified Transport Surface
5. Piece 4: SQLite Operational Persistence
6. Piece 5: FastMCP Adapter and CLI/MCP Convergence
7. Piece 6: YDoc Collaboration Spike
8. Piece 7: YDoc Migration for Notebook Editing
9. Piece 8: Notebook LSP Standardization
10. Piece 9: Cleanup, Deletion, and Parity Hardening

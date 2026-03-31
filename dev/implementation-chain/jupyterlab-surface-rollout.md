# JupyterLab Surface Rollout

This document turns the JupyterLab surface direction into an ordered delivery plan.

It is the implementation companion to:

- [VS Code Jupyter Parity Checklist](/Users/giladrubin/python_workspace/agent-repl-jupyterlab-surface/dev/jupyter-parity-checklist.md)
- [JupyterLab-Powered Notebook Surface](/Users/giladrubin/python_workspace/agent-repl-jupyterlab-surface/dev/design/jupyterlab-surface.md)
- [Reference Stack](/Users/giladrubin/python_workspace/agent-repl-jupyterlab-surface/dev/design/reference-stack.md)
- [Architecture Modernization Rollout](/Users/giladrubin/python_workspace/agent-repl-jupyterlab-surface/dev/implementation-chain/architecture-modernization-rollout.md)

## Goal

Reach a point where the human-facing notebook surface behaves like JupyterLab by default, while `agent-repl-core` remains the authority for:

- notebook truth
- runtime lifecycle
- execution queue and activity
- sessions, presence, and attach/reopen behavior
- headless agent work

In short:

- JupyterLab should own notebook semantics.
- `agent-repl` should keep owning runtime semantics.

## Non-Goals

This rollout does not aim to:

- hand notebook authority back to Jupyter Server or VS Code
- make notebook execution depend on an open editor
- adopt the stock VS Code notebook surface
- replace the daemon with Jupyter collaboration services

## Current Status

Already true in the preview worktree:

- the visible notebook surface is a real JupyterLab `Notebook` widget
- code and markdown cells use JupyterLab editors and renderers
- command/edit mode basics are working, including `Cmd/Ctrl+A` select-all and `z` notebook-structure undo in command mode
- daemon-backed structural edits now cover `change-cell-type` as well as insert/delete/move, so JupyterLab command actions no longer depend on stale custom fallbacks
- the preview runs real notebooks through the daemon-backed runtime
- sibling-project notebooks can execute with the correct workspace root
- trusted iframe-backed HTML outputs render after explicit notebook trust
- saved ipywidget outputs render through the Jupyter widget manager when notebook metadata includes widget state

Still custom today:

- notebook model hydration and refresh are hand-rolled around `/contents` polling
- toolbar chrome and notebook framing are mostly ours
- live comm-driven widget execution is still incomplete
- completion and diagnostics are not yet aligned to `jupyterlab-lsp` patterns
- browser and future VS Code integration still rely on custom transport glue

## Acceptance Bar

Use [VS Code Jupyter Parity Checklist](/Users/giladrubin/python_workspace/agent-repl-jupyterlab-surface/dev/jupyter-parity-checklist.md) as the minimum interaction bar for notebook behavior changes.

This rollout is complete only when all of the following are true:

- the browser preview behaves like a JupyterLab notebook for code cells, markdown cells, outputs, and command/edit flows
- trusted rich outputs render with JupyterLab semantics rather than custom approximations
- notebook state projection is driven by the YDoc/shared-model layer rather than bespoke notebook JSON patching
- diagnostics and completions use one notebook-aware virtual-document model across browser and VS Code hosts
- deleting the old custom canvas/editor code makes the codebase simpler rather than moving the same behavior to a new file

## Phase Plan

### Phase 1: YDoc-backed notebook projection

Move the preview off disk-snapshot `/contents` refreshes and onto a versioned YDoc-backed projection API.

Why first:

- it reduces stale-refresh races
- it moves the surface toward the Jupyter shared-model stack we already keep on the server
- it creates a stable seam for richer trust/widget/LSP work later

Deliverables:

- a core read API that returns notebook cells from the YDoc shadow plus a monotonic document version
- standalone proxy support for that API
- the JupyterLab preview consuming the YDoc-backed projection path instead of blind `/contents` polling
- regression coverage that stale or out-of-order refreshes do not overwrite newer local state

### Phase 2: Trust and renderer parity

Complete the JupyterLab rendering stack for trusted notebook content.

Deliverables:

- explicit trust-sensitive rendering policy
- support for trusted iframe-backed HTML outputs
- widget-manager integration for notebook widget outputs
- browser verification for real notebook outputs that previously required custom exceptions

Current note:

- trusted HTML and saved widget-output rendering are now in place for persisted notebooks
- live widget execution still needs comm-aware runtime transport before this phase is fully done

### Phase 3: Notebook chrome and command parity

Shrink custom notebook chrome and prefer JupyterLab primitives where possible.

Deliverables:

- reduce custom toolbar and shell code to host-only responsibilities
- preserve only agent-specific controls that JupyterLab does not already provide well
- keep browser and VS Code hosts thin around the same notebook implementation

Current note:

- command-mode select-all and notebook-level structure undo are now wired through the JupyterLab surface
- focus handoff after `Escape` is improved, but browser automation still needs an explicit ready/focus contract instead of relying on startup timing heuristics
- the active implementation tasks in this phase are:
  - expose one deterministic preview readiness contract after bootstrap, widget restore, and notebook attach complete
  - retry transient bootstrap failures instead of surfacing first-load fetch races as hard errors
  - centralize browser test helpers for JupyterLab-ready and command-mode notebook focus
  - keep a single strong `z` undo regression instead of multiple slightly different flaky versions of the same flow

### Phase 4: Notebook-aware completions and diagnostics

Replace the current custom notebook LSP glue with one virtual-document model aligned to `jupyterlab-lsp` concepts.

Deliverables:

- stable notebook-to-virtual-document mapping across insert/delete/move
- one completion and diagnostics projection model for browser and VS Code
- reconnect/restart behavior that clears stale diagnostics correctly

### Phase 5: Deletion and parity hardening

Delete or sharply shrink the superseded custom notebook surface.

Deliverables:

- retire old notebook rendering and command-routing code that no longer pays rent
- retire duplicated output-selection logic where JupyterLab now owns that behavior
- document the final host-shell responsibilities clearly

## Keep vs Replace

Keep:

- `agent-repl-core` authority
- server-owned runtime and execution state
- session reuse and attach semantics
- headless execution and background-safe behavior
- YDoc as a shared-model seam under core authority

Replace or shrink:

- custom notebook hydration/polling loops
- custom output rendering where JupyterLab already has a renderer
- custom markdown/code cell wrappers
- bespoke notebook keyboard routing that exists only because the old canvas owned the surface
- custom notebook-LSP glue that does not align to notebook virtual-document standards

## Verification Per Phase

Every phase should report:

- changed behavior
- checks run
- residual risks

At minimum keep these checks green as the rollout proceeds:

- focused browser smoke coverage for the JupyterLab preview
- standalone-server routing and proxy tests
- core notebook HTTP route tests
- YDoc service tests

## Sequencing Notes

- Do not attempt widget parity before the notebook projection path is stabilized.
- Do not standardize LSP before notebook identity and source mapping are settled on the shared-model side.
- Do not delete the old surface until replacement behavior is covered by tests in the JupyterLab path.

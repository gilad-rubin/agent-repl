# JupyterLab-Powered Notebook Surface

This document defines the target frontend shape for `agent-repl` notebook UX.

It is a design document, not an implementation plan.

Primary references:

- [Current Architecture](../current-architecture.md)
- [Architecture Modernization Plan](../architecture-modernization-plan.md)
- [North Star](north-star.md)
- [Interaction Contract](interaction-contract.md)
- [Joint Canvas Spec](joint-canvas-spec.md)
- [Core Authority and Sessions](core-authority.md)
- [Runtime and Execution](runtime-and-execution.md)

## Purpose

`agent-repl` already solved the hard product problems that stock notebook frontends do not solve cleanly for us:

- headless agent work with no editor open
- notebook execution that does not steal focus
- attach and reopen semantics while work is already in progress
- daemon-owned execution, runtime, session, and activity truth

Those are the differentiated parts of the product.

The recurring maintenance burden is the notebook surface itself:

- code and markdown editing behavior
- output rendering parity with JupyterLab
- trust and MIME handling
- HTML, iframe, and widget edge cases
- keyboard and command-mode behavior
- keeping browser preview and VS Code behavior aligned with notebook expectations

The right split is therefore:

- keep `agent-repl`'s runtime and collaboration model
- stop owning the commodity notebook UI where JupyterLab is already stronger

## Decision

Adopt a JupyterLab-powered notebook surface for human-facing notebook editing and rendering while preserving `agent-repl` as the authority for:

- notebook truth
- runtime lifecycle
- execution queue and activity
- session attachment, presence, and leases
- headless agent workflows
- late attach, reopen, and recovery

In practical terms:

1. `agent-repl-core` remains the system of record.
2. VS Code continues to host a custom editor/webview rather than the stock VS Code notebook surface.
3. The notebook pane inside that webview becomes a real JupyterLab notebook surface.
4. Browser preview should host the same JupyterLab-backed notebook surface when feasible so we still have one notebook implementation across human-facing surfaces.

This changes one explicit stance in [Architecture Modernization Plan](../architecture-modernization-plan.md): that document correctly argued against adopting JupyterLab UI when the custom canvas still looked cheaper to maintain. The current maintenance burden and parity failures now make that tradeoff wrong. The daemon/runtime/session direction remains correct. The custom notebook surface direction does not.

## Why This Changes Now

Recent output work made the tradeoff concrete.

We improved rich MIME handling for:

- `text/html`
- `text/markdown`
- images
- JSON
- live `IPython.display(...)` objects

That helped, but it also clarified the limit of the current strategy: every improvement still requires us to manually rediscover notebook semantics that JupyterLab already owns.

The `graph.visualize()` failure is the clearest example. The notebook already contains valid `text/html`, but the payload is an iframe-based representation. Our custom renderer sanitizes `text/html`, strips the iframe, and loses the visualization. JupyterLab does not have this problem because notebook trust and MIME rendering are already part of its core frontend model.

If we keep reimplementing notebook semantics ourselves, we should expect more of these cases:

- trusted iframe-backed HTML
- widget-rich outputs
- renderer-specific copy behavior
- markdown and HTML interoperability
- future MIME renderer drift

## Non-Goals

This decision does not mean:

- giving notebook authority back to JupyterLab
- giving runtime ownership back to VS Code
- requiring a visible notebook tab for agent work
- replacing the daemon with Jupyter server as product authority
- adopting the stock VS Code notebook editor as our main surface
- big-bang rewriting the daemon, CLI, and extension at once

## The Core Constraint

The product still has to satisfy the same interaction contract:

- notebook actions must stay background-safe
- agents must work with no notebook open
- the editor itself must be optional for normal agent work
- a human must be able to open a notebook midway through agent execution and attach to current truth
- persisted notebook state must remain visible even when live runtime continuity is gone

These constraints come from [Interaction Contract](interaction-contract.md) and remain binding.

They are execution and session constraints, not renderer constraints.

## Frontend Posture

The right posture is:

- JupyterLab owns notebook semantics
- `agent-repl` owns notebook authority
- VS Code owns the outer host shell

This means the notebook pane should feel like JupyterLab, while the surrounding product shell remains ours.

The notebook itself should inherit JupyterLab behavior for:

- code cell editing
- markdown cell editing and rendering
- output rendering
- notebook keyboard semantics
- trust-sensitive HTML and widget behavior
- the usual notebook selection and command/edit flows

The surrounding host shell remains responsible for:

- daemon attach and detach
- notebook selection and opening
- agent-specific chrome
- presence and collaboration affordances
- save, run, restart, and kernel actions routed through the daemon
- background-safe integration with VS Code-only capabilities

## Why Not Use VS Code's Native Notebook UI

The stock VS Code notebook surface is not the right long-term host.

The original reasons for building `agent-repl` custom infrastructure were:

1. notebook actions could yank focus and disrupt the user's work
2. agents could not work safely headless
3. opening and closing notebooks affected active agent work in ways we did not control

Those are lifecycle and ownership problems. Re-entering the stock VS Code notebook surface would move too much notebook/runtime coupling back under VS Code behavior.

The safer split is:

- keep using a VS Code custom editor and webview as our host
- mount a JupyterLab notebook surface inside that host
- continue routing all meaningful notebook and runtime actions through the daemon

That gives us JupyterLab notebook behavior without giving up lifecycle control.

## Why Not Stop At `OutputArea` Only

Using JupyterLab `OutputArea` alone is a valid migration step, but it is not the end state.

`OutputArea` would reduce pain around:

- MIME rendering
- trust behavior
- iframe and widget outputs

But it would still leave us owning:

- code editing
- markdown editing
- command vs edit mode behavior
- cell selection and structure semantics
- notebook keyboard flows

If the goal is to stop carrying notebook-frontend maintenance, the target should be the full notebook surface, not just the output renderer.

## Architecture

### Authority Split

The authority split remains:

- `agent-repl-core` owns document truth, execution truth, sessions, activity, and runtime lifecycle
- Jupyter kernels remain the execution backend
- JupyterLab owns notebook presentation and editing behavior
- VS Code owns the outer shell and editor integration affordances

### Client Model

JupyterLab should be treated as a projection client of the daemon, not as a new authority.

That means:

- notebook open means attach a projection client
- notebook close means detach a projection client
- start runtime and stop runtime remain daemon concepts
- run records and queue state remain daemon concepts
- reconnect means rehydrate current truth rather than reconstructing it heuristically

### Data Flow

The expected human-facing data flow is:

1. VS Code opens the custom editor webview.
2. The webview boots a JupyterLab notebook surface.
3. The notebook surface is hydrated from daemon notebook contents and live status.
4. User edits and notebook commands become explicit daemon mutations.
5. Daemon activity and projection updates reconcile back into the visible notebook model.
6. Closing the view removes only the projection client, not the runtime or shared notebook truth.

The browser preview should follow the same shape where possible:

1. standalone host boots the same JupyterLab-backed notebook surface
2. preview attaches to daemon-backed or simulated notebook state through the host bridge
3. notebook behavior stays consistent across browser and VS Code because the notebook surface is shared

## Keep

These are already the right foundations and should survive the surface swap.

- [server.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/server.py): `CoreState` remains the durable workspace authority.
- [notebook_execution_service.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/notebook_execution_service.py): headless execution remains a daemon feature.
- [execution_ledger_service.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/execution_ledger_service.py): queued, running, and completed state remain server-owned.
- [collaboration_service.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/collaboration_service.py): sessions, presence, and ownership stay daemon concerns.
- [ydoc_service.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/ydoc_service.py): collaborative notebook state remains a strong seam if the surface becomes just another client.
- [notebook_read_service.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/notebook_read_service.py): read and projection APIs remain the notebook hydration seam.
- [notebook_write_service.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/notebook_write_service.py): mutations continue to route through daemon APIs.
- [session.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/session.ts): auto-attach and projection sync remain core product behavior.
- [provider.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/editor/provider.ts): the custom editor remains the VS Code host entry point.
- [webview.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/editor/webview.ts): the host still needs to mount the notebook surface and inject host assets and config.
- [proxy.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/editor/proxy.ts): message transport between webview and daemon remains the highest-signal bridge seam.
- [standalone-host.ts](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/standalone-host.ts): browser preview still needs a shell-to-surface adapter if it remains a first-class development surface.

## Likely Replace Or Shrink

These are the places where we are currently paying the notebook-surface maintenance tax.

- [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx): currently owns most notebook rendering, interaction, and output semantics.
- [codemirror-cell.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/codemirror-cell.tsx): bespoke code-cell editor wrapper should largely disappear once JupyterLab owns editing.
- [styles.css](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/styles.css): notebook-specific surface styling should shrink to host-shell styling.
- [notebookOutputRender.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/shared/notebookOutputRender.ts): MIME selection is no longer our long-term job if JupyterLab owns output rendering.
- [execution/queue.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/execution/queue.ts): any logic duplicating daemon execution truth should narrow sharply.
- [editor/lsp.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/editor/lsp.ts): current custom editor glue may become transitional once JupyterLab editors own the visible notebook surface.

## Host Shell Responsibilities

The host shell should remain intentionally thin.

VS Code host shell:

- open and track the custom editor
- keep the webview attached to daemon and session state
- expose VS Code-only integration points
- preserve background-safe behavior for notebook actions

Browser preview shell:

- mount the same notebook surface in a standalone context
- provide a compatible host bridge
- preserve preview-specific chrome such as explorer or diagnostics only if they remain product-relevant

The shell should not keep re-implementing notebook behavior that JupyterLab already owns.

## Notebook Surface Responsibilities

The notebook surface should be a real JupyterLab notebook implementation.

That surface should own:

- code and markdown editors
- notebook command/edit behavior
- rich outputs and copy semantics where JupyterLab already has clear behavior
- notebook trust-sensitive rendering
- the normal notebook DOM and interaction model

The shell should avoid shadow implementations of those concerns.

## Migration Options

### Option 1: JupyterLab `OutputArea` only

Benefits:

- smallest frontend risk
- immediate output parity gains

Limits:

- we still own notebook editing and interaction semantics
- does not actually remove the main maintenance burden

### Option 2: Full JupyterLab notebook surface inside the existing host

Benefits:

- strongest notebook parity
- lets us stop owning most notebook UI behavior
- preserves our daemon-owned runtime and session model
- works in both VS Code custom editor and browser preview hosts

Limits:

- larger adapter effort
- bigger webview bundle
- LSP integration needs deliberate design

### Option 3: Stock VS Code notebook UI plus daemon bridges

Benefits:

- less custom host UI

Limits:

- puts notebook lifecycle back under a host we explicitly moved away from
- reintroduces exactly the ownership and focus risks we were trying to escape

### Recommendation

The recommended end state is Option 2.

Option 1 is a valid migration step.

Option 3 should not be the target architecture.

## Acceptance Criteria

This direction is successful when all of the following are true:

- a notebook opened in the custom editor visibly behaves like a JupyterLab notebook surface for code cells, markdown cells, outputs, and standard notebook interactions
- `display(Markdown(...))`, `display(HTML(...))`, iframe-backed trusted HTML, JSON, images, and similar rich outputs render with JupyterLab semantics rather than custom approximations
- opening, closing, or switching notebook views does not change runtime ownership or interrupt active agent work
- agents can still create, edit, and execute notebooks headlessly with no visible notebook surface
- a human can open a notebook midway through active agent work and attach to current daemon truth without focus steal or restart prompts
- browser preview and VS Code keep sharing the same notebook surface implementation, or any deliberate divergence is documented as a host-level choice rather than silent drift
- execution queue and activity remain daemon-owned, not reconstructed from frontend heuristics
- the architecture removes notebook-surface code from the list of recurring parity chores rather than merely moving it around

## Risks And Open Questions

- JupyterLab packages are heavier than the current bundle and may stress VS Code webview constraints.
- We need a clear adapter between daemon-owned notebook state and JupyterLab's notebook model.
- We must decide how much current virtual-document and LSP glue remains valuable versus being replaced by JupyterLab-compatible editor integration.
- Some current browser-canvas behavior locks are product requirements, while others are implementation details of the custom canvas and should be retired deliberately rather than accidentally.
- We need a clear trust model for persisted notebooks opened through the daemon-backed JupyterLab surface.

## Summary

`agent-repl` should keep owning the parts that make it different:

- daemon authority
- headless execution
- session and attach semantics
- background-safe collaboration

It should stop owning the parts that keep dragging it into Jupyter parity work:

- notebook editing semantics
- output rendering semantics
- trust-sensitive frontend behavior

The clean target is a JupyterLab notebook surface hosted inside our existing VS Code custom editor and browser shell, both projecting daemon-owned notebook truth rather than becoming new authorities themselves.

# Extension Development Guide

## Module Map

- `extension.ts` — VS Code activation, command registration
- `server.ts` — extension HTTP server for CLI bridge
- `routes.ts` — extension API surface for editor-backed features and bridge routes
- `session.ts` — session auto-attach, heartbeat, headless notebook projection
- `execution/queue.ts` — output helpers (iopub-to-Jupyter, display-id updates) and Jupyter API cache; execution queue state is daemon-owned
- `editor/provider.ts` — canvas custom editor host
- `editor/webview.ts` — mounts the shared React bundle into the custom editor
- `editor/proxy.ts` — proxies runtime traffic between canvas and daemon
- `editor/lsp.ts` — Pyright LSP integration (virtual document, diagnostics, completions)
- `scripts/standalone-lsp.mjs` — headless LSP for browser preview
- `src/shared/` — pure helpers shared across proxy and standalone host

## Ownership Split

The extension now has a deliberate split between host-shell/product behavior and notebook semantics.

- `agent-repl` still owns:
  - runtime/session authority
  - daemon routing and workspace resolution
  - attach/detach behavior across browser, VS Code, CLI, and agents
  - product shell concerns such as explorer framing, toolbar integration, recovery UX, and host messaging
- JupyterLab now owns, or should increasingly own:
  - code and markdown cell editing behavior
  - command/edit mode semantics and notebook keyboard flows
  - rich output rendering, trust-aware notebook presentation, and widget-compatible rendering
  - notebook-local interaction defaults where upstream already has a strong answer

Current practical boundary:

- `extension/webview-src/jupyterlab-preview.tsx` is the active notebook-surface path for browser preview and the reference direction for the eventual VS Code custom-editor notebook surface.
- `extension/webview-src/main.tsx` still contains older custom notebook surface behavior and host-shell code. Treat notebook-rendering and notebook-editing logic there as transitional unless it is clearly host-specific or product-specific.
- `editor/webview.ts`, `editor/provider.ts`, and `editor/proxy.ts` remain durable host glue. They should mount and synchronize the notebook surface, not re-encode notebook semantics.
- `editor/lsp.ts` and `src/shared/notebookVirtualDocument.ts` remain important, but their current custom notebook mapping is transitional until it is aligned with a `jupyterlab-lsp`-style virtual-document model.

## Shared Modules (`extension/src/shared/`)

| Module | Purpose |
|--------|---------|
| `wsClient.ts` | `DaemonWebSocket` — shared WebSocket client for proxy and browser standalone host. Nonce auth, auto-reconnect with exponential backoff, per-path subscriptions, cursor-based replay |
| `runtimeSnapshot.ts` | Build runtime/activity snapshots from daemon payloads |
| `executionState.ts` | Pure reducers for execution bucket transitions |
| `notebookActivity.ts` | Shared activity interpreter (WebSocket events and poll fallback) |
| `notebookCommandFlow.ts` | Shared command orchestration pattern |
| `notebookEditPayload.ts` | Shared edit operation builders |
| `postCommandRefresh.ts` | Post-command refresh policy per command |
| `notebookVirtualDocument.ts` | Notebook-to-virtual-document mapping for LSP |

## Dev Loops

- **Preferred integration loop**: `uv run agent-repl editor dev --editor vscode` — compiles the repo extension and opens an Extension Development Host so VS Code runs the workspace checkout directly.
- **Canvas UI / JupyterLab preview**: `cd extension && npm run preview:webview` — serves the browser shell and the current JupyterLab-backed notebook surface. Fastest loop for renderer work.
- **Extension host**: `cd extension && npm run compile` then `Agent REPL: Reload` — hot-reloads routes/modules without reinstalling VSIX.
- **Full rebuild**: Changes to `extension.ts` or `server.ts` require full window reload.
- **Installed extension**: Recompiling does NOT update `~/.vscode/extensions/` — reinstall the `.vsix` or use Extension Development Host.

## Execution Model

All notebook execution routes through the daemon via `POST /api/notebooks/execute-cell`. There are no native VS Code execution paths (`notebook.cell.execute`, `kernel.executeCode`) used for workspace notebooks.

- `routes.ts` execution routes are thin daemon HTTP pass-throughs via `daemonPost` from `session.ts`
- `execution/queue.ts` contains only output helpers and Jupyter API cache — no local queue state
- `HeadlessNotebookProjection.executeCells` in `session.ts` uses the same daemon HTTP path
- Queue status and running state are derived from daemon responses and WebSocket events via `shared/executionState.ts`
- The execution monitor (`initExecutionMonitor`) is a no-op — execution state comes from daemon, not VS Code document change events

## Sync Model

The extension uses push-based WebSocket sync instead of HTTP polling:

- `editor/proxy.ts` creates a `DaemonWebSocket` for each canvas webview, subscribing to the active notebook path
- `session.ts` (`HeadlessNotebookProjection`) creates a `DaemonWebSocket` for projection sync
- `webview-src/jupyterlab-preview.tsx` and `standalone-host.ts` use `DaemonWebSocket` directly from the browser
- WebSocket events are wrapped into the same envelope format as the old poll results via `buildActivityPollResult`
- Reconnection uses exponential backoff (500ms base, 30s max, 30% jitter) with automatic resubscription

## Key Rules

- Execution paths must stay background-safe. If a path steals focus or surfaces UI, that's a product bug.
- Browser preview does not exercise VS Code messaging, kernel attach, or custom-editor lifecycle. Verify integration-sensitive changes in Extension Development Host.
- Browser preview does exercise the standalone session-selection path. If preview and VS Code disagree on lease behavior, inspect session reuse first.
- Prefer replacing bespoke notebook editing/rendering behavior with JupyterLab primitives rather than growing new notebook-specific code in `main.tsx`.
- Keep custom extension work focused on host-shell concerns, daemon synchronization, and product-specific affordances. If a behavior is fundamentally notebook-local and JupyterLab already implements it well, treat JupyterLab as the default owner.
- `browserCanvasUrl` lets the installed extension prefer preview-served assets on loopback. If preview and installed UI disagree, suspect asset drift.
- `agent-repl doctor` and `agent-repl reload --pretty` now report repo-vs-installed build drift when the workspace contains an `extension/` checkout.
- Dirty-draft behavior around execute-all, restart-and-run-all, and notebook switching is high-risk. Add regression coverage when touching those flows.

## Canvas Icons

Prefer `@carbon/icons-react` for native-feeling icons. Discover variants:
```bash
node -e "const icons=require('@carbon/icons-react'); console.log(Object.keys(icons).filter((name) => /Play|Chevron|Caret|Add/i.test(name)))"
```

## Error Handling

Never write `catch { continue; }` without capturing diagnostics:
```typescript
const diagnostics: AttachDiagnostic[] = [];
try {
    await riskyOperation();
} catch (err: any) {
    diagnostics.push({ method: 'operationName', detail: err?.message ?? String(err) });
}
```

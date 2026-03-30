# Extension Development Guide

## Module Map

- `extension.ts` — VS Code activation, command registration
- `server.ts` — extension HTTP server for CLI bridge
- `routes.ts` — extension API surface for editor-backed features and bridge routes
- `session.ts` — session auto-attach, heartbeat, headless notebook projection
- `execution/queue.ts` — most complex module; read fully before modifying
- `editor/provider.ts` — canvas custom editor host
- `editor/webview.ts` — mounts the shared React bundle into the custom editor
- `editor/proxy.ts` — proxies runtime traffic between canvas and daemon
- `editor/lsp.ts` — Pyright LSP integration (virtual document, diagnostics, completions)
- `scripts/standalone-lsp.mjs` — headless LSP for browser preview
- `src/shared/` — pure helpers shared across proxy and standalone host

## Shared Modules (`extension/src/shared/`)

| Module | Purpose |
|--------|---------|
| `runtimeSnapshot.ts` | Build runtime/activity snapshots from daemon payloads |
| `executionState.ts` | Pure reducers for execution bucket transitions |
| `notebookActivity.ts` | Shared activity poll interpreter |
| `notebookCommandFlow.ts` | Shared command orchestration pattern |
| `notebookEditPayload.ts` | Shared edit operation builders |
| `postCommandRefresh.ts` | Post-command refresh policy per command |
| `notebookVirtualDocument.ts` | Notebook-to-virtual-document mapping for LSP |

## Dev Loops

- **Canvas UI**: `cd extension && npm run preview:webview` — serves real canvas in browser with simulated runtime. Fastest loop for renderer work.
- **Extension host**: `cd extension && npm run compile` then `Agent REPL: Reload` — hot-reloads routes/modules without reinstalling VSIX.
- **Full rebuild**: Changes to `extension.ts` or `server.ts` require full window reload.
- **Installed extension**: Recompiling does NOT update `~/.vscode/extensions/` — reinstall the `.vsix` or use Extension Development Host.

## Key Rules

- Execution paths must stay background-safe. If a path steals focus or surfaces UI, that's a product bug.
- Browser preview does not exercise VS Code messaging, kernel attach, or custom-editor lifecycle. Verify integration-sensitive changes in Extension Development Host.
- Browser preview does exercise the standalone session-selection path. If preview and VS Code disagree on lease behavior, inspect session reuse first.
- `browserCanvasUrl` lets the installed extension prefer preview-served assets on loopback. If preview and installed UI disagree, suspect asset drift.
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

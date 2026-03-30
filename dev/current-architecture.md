# Architecture

This document describes the current shipped architecture used by `agent-repl` today.

For the north-star target architecture, see:

- [Design Docs](design/README.md)
- [North Star](design/north-star.md)
- [Review Rubric](design/review-rubric.md)

## One-Screen Summary

- The shared runtime in `src/agent_repl/core/` is now the primary notebook authority for public CLI flows
- The VS Code extension is a projection and editor-integration layer, not the only execution backend
- The browser preview and the VS Code canvas render the same bundled UI from `extension/webview-src/main.tsx`
- Some editor-assisted features still live on bridge routes: prompt cells, reload, kernel inspection, and notebook APIs that require VS Code

## Topology

```
Human or Agent
    ↕
CLI / VS Code Canvas / Browser Preview
    ↕
Shared Runtime (`src/agent_repl/core/`)
    ↕
Notebook files + headless kernels
```

There is still a second transport in the system:

```
CLI editor-backed commands
    ↕
VS Code extension bridge (`extension/src/routes.ts`)
    ↕
VS Code notebook APIs
```

That bridge remains necessary for editor-specific capabilities, but it is no longer the product's center of gravity.

## Shared Runtime (`src/agent_repl/core/`)

The shared runtime owns the headless workflow. It is a workspace-scoped daemon that can outlive any given editor window.

It is responsible for:

1. Starting on demand for the current workspace
2. Creating and reusing headless kernels
3. Reading, editing, and executing notebooks without the editor
4. Tracking collaboration/session state for projected notebooks
5. Serving runtime-owned notebook projections back to editor clients

Key modules:

| Module | Purpose |
|--------|---------|
| `core/server.py` | Workspace daemon, notebook authority, runtime/session coordination |
| `core/client.py` | Runtime discovery and HTTP client |
| `cli.py` | Public command routing plus hidden `core` diagnostics surface |

### Runtime Model

The runtime surface is broader than simple notebook execution. The hidden `agent-repl core ...` subcommands expose the current collaboration model:

- `sessions`, `session-start`, `session-touch`, `session-detach`, `session-end`
- `session-presence-upsert`, `session-presence-clear`
- `documents`, `document-open`, `document-refresh`, `document-rebind`
- `notebook-runtime`, `notebook-projection`, `notebook-activity`
- `cell-lease-acquire`, `cell-lease-release`
- `branches`, `branch-start`, `branch-finish`, `branch-review-*`
- `runtimes`, `runtime-*`
- `runs`, `run-*`

In practice, that means the runtime now tracks:

- who is attached to the workspace
- which notebook is visible in which client
- which cells are temporarily leased for editing/execution
- which notebook runtimes are live, shared, pinned, or recoverable
- which long-running runs or review branches are active

## VS Code Extension (`extension/src/`)

The extension is primarily a projection and editor-integration layer. It:

1. Starts an authenticated HTTP bridge on a random or configured port
2. Writes a bridge discovery file to the Jupyter runtime directory
3. Auto-attaches the editor window to the matching shared runtime
4. Projects runtime-owned notebook state into open custom-editor canvases
5. Still handles VS Code-specific features such as prompt cells, reload, and kernel-facing notebook APIs

Key modules:

| Module | Purpose |
|--------|---------|
| `extension.ts` | Activation, command registration, provider wiring |
| `server.ts` | HTTP server, route dispatch, bearer-token auth |
| `routes.ts` | Bridge API handlers and editor integration endpoints |
| `session.ts` | Shared-runtime auto-attach, heartbeats, projection sync |
| `editor/provider.ts` | Custom `.ipynb` canvas provider and open-canvas tracking |
| `editor/proxy.ts` | Webview/runtime message bridge and presence updates |
| `editor/webview.ts` | HTML shell that loads the shared canvas bundle |
| `execution/queue.ts` | VS Code-backed execution queue and kernel state tracking |
| `notebook/*` | Resolver, edit operations, output conversion, identity helpers |

### Canvas UI

There is one shipped canvas implementation:

- source: `extension/webview-src/main.tsx`
- standalone/preview host glue: `extension/webview-src/standalone-host.ts`
- built assets: `extension/media/canvas.js` and `extension/media/canvas.css`

Both of these use the same bundle:

- browser preview: `extension/preview.html` + `extension/scripts/preview-webview.mjs`
- VS Code custom editor: `editor/provider.ts` + `editor/webview.ts`

In browser mode, the shared bundle now renders a minimal VS Code-like shell around the notebook canvas: a thin activity rail plus a collapsible explorer for workspace `*.ipynb` files. The explorer is browser-only, but it is still driven by the same `main.tsx` bundle as the VS Code canvas instead of a separate preview UI.

The installed extension can optionally prefer preview-served assets through the `agent-repl.browserCanvasUrl` setting, but only from loopback origins and only with a packaged-asset fallback. If preview and VS Code diverge visually, the most likely cause is bundle drift rather than a separate UI codepath.

For day-to-day development, the preferred integration path is now `agent-repl editor dev --editor vscode`, which compiles the workspace extension and launches an Extension Development Host from the repo checkout. Installed-extension comparisons remain supported, but `agent-repl doctor` and `agent-repl reload --pretty` should warn when the installed build drifts from the workspace repo build.

## CLI (`src/agent_repl/`)

The CLI now has two surfaces:

1. Public commands, which usually target the shared runtime
2. Editor-backed commands, which still use the extension bridge when needed

Key files:

| File | Purpose |
|------|---------|
| `cli.py` | Argparse definitions and command handlers |
| `core/client.py` | Shared-runtime calls for runtime-first flows |
| `client.py` | Extension bridge discovery and editor-backed endpoints |

### Public Commands

The shipped public command set is:

- `reload`
- `setup`
- `doctor`
- `cat`
- `status`
- `edit`
- `exec`
- `ix`
- `run-all`
- `restart`
- `restart-run-all`
- `new`
- `open`
- `kernels`
- `select-kernel`
- `editor`
- `prompts`
- `respond`
- `mcp`

Notable behavior:

- `setup` can configure workspace editor defaults, run public MCP onboarding, and execute a notebook smoke test
- `doctor` reports install method, workspace kernel readiness, editor default status, and optional MCP endpoint health
- `new` and `open` can target either `vscode` or `browser`
- `new` and `open` can prefer either the custom `canvas` editor or native `jupyter`
- `editor configure --default-canvas` writes workspace `.vscode/settings.json` to prefer `agent-repl.canvasEditor` for `*.ipynb`
- `mcp` is the public MCP onboarding surface with `setup`, `status`, `config`, and `smoke-test`
- `reload` hot-reloads extension routes/modules in place and reports the active `extension_root` and `routes_module`

## Discovery and Connection Files

### Extension bridge

The extension writes a discovery file like:

```json
{
  "port": 54321,
  "token": "random-bearer-token",
  "version": "0.3.0",
  "workspace_folders": ["/Users/you/project"]
}
```

Location:

- macOS: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
- Linux: `~/.local/share/jupyter/runtime/agent-repl-bridge-<pid>.json`

`BridgeClient.discover()` scans these files, sorts by freshness, pings `/api/health`, and chooses a healthy bridge whose workspace matches the command context.

### Shared runtime

The runtime has its own workspace-scoped discovery/metadata files under the Jupyter runtime directory as well. When the runtime looks healthy but editor projection is not, use `agent-repl core status` and `agent-repl reload --pretty` to separate runtime problems from installed-extension drift.

## Bridge API Surface

The most important bridge endpoints today are:

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Bridge status, live module info, and open notebooks |
| GET | `/api/notebook/contents` | Cell sources + agent-cleaned outputs |
| GET | `/api/notebook/status` | Kernel state + execution queue |
| GET | `/api/notebook/execution` | Poll execution result by ID |
| POST | `/api/notebook/edit` | Batch cell operations |
| POST | `/api/notebook/execute-cell` | Run an existing cell |
| POST | `/api/notebook/insert-and-execute` | Insert and run a new cell |
| POST | `/api/notebook/execute-all` | Run all cells |
| POST | `/api/notebook/restart-kernel` | Restart kernel |
| POST | `/api/notebook/restart-and-run-all` | Restart and run all |
| GET | `/api/notebook/kernels` | List kernels known to VS Code |
| POST | `/api/notebook/select-kernel` | Select kernel or open the picker |
| POST | `/api/notebook/create` | Create a notebook and open/project it |
| POST | `/api/notebook/open` | Open an existing notebook |
| POST | `/api/notebook/prompt` | Create a prompt cell |
| POST | `/api/notebook/prompt-status` | Update prompt metadata |
| POST | `/api/reload` | Hot-reload routes/modules |

All POST request bodies and responses are JSON. Auth uses `Authorization: token <bearer-token>`.

## Notebook Identity and Outputs

### Cell identity

Each notebook cell gets a stable UUID in `metadata.custom.agent-repl.cell_id`. Use cell IDs over indices whenever a workflow can tolerate structural edits.

### Output surfaces

There are two output views:

- notebook persistence keeps full Jupyter outputs
- agent-facing APIs strip rich media down to safe summaries or text placeholders
- canvas/browser rendering prefers rich notebook mime bundles in a JupyterLab-like order: `text/html`, `text/markdown`, SVG/raster images, JSON, then plain text

`toJupyter()` and `toVSCode()` bridge between VS Code notebook output objects and standard Jupyter structures.

## Execution Paths

### Headless runtime path

This is the preferred path for public notebook commands:

- works without an editor
- owns kernel lifecycle in the workspace daemon
- persists outputs directly into the notebook file

### VS Code execution path

This path is still used for editor-backed execution and bridge routes:

- execution is serialized per notebook in `execution/queue.ts`
- live kernel state is reconciled against notebook events before reporting idle/busy
- open notebooks can stream visible progress through VS Code if the projection client is attached

## Open vs Closed Notebooks

- Runtime-first commands can operate on notebooks that are not open in the editor
- Bridge status for a closed notebook reports `not_open` and empty execution queues
- Some editor-backed routes will open the notebook document if a VS Code notebook API requires it
- Custom canvas panels are tracked explicitly by the provider so bridge health can report canvas-open notebooks, not only native notebook editors

## Settings That Matter

The settings with the biggest architectural impact are:

- `agent-repl.autoStart`
- `agent-repl.sessionAutoAttach`
- `agent-repl.cliCommand`
- `agent-repl.pyrightCommand`
- `agent-repl.browserCanvasUrl`
- `agent-repl.executionMode`

`executionMode` still controls whether the extension prefers a lower-focus background path (`no-yank`) or native VS Code execution (`native`) for editor-backed runs.

Canvas Python IDE features are powered by a virtual notebook document plus a generated shadow file under the workspace-local `.agent-repl/pyright/` tree. That keeps Pyright's on-disk scratch state out of the notebook directories while preserving notebook-relative analysis semantics.

## Build and Reload Loops

Use the smallest loop that matches the work:

- renderer-only canvas change: `cd extension && npm run build:webview`
- extension TS or canvas change: `cd extension && npm run compile`
- fast installed-extension refresh: `agent-repl reload --pretty`
- renderer validation in browser: `cd extension && npm run preview:webview` (`/preview.html` roots to the launched workspace; `?mock=1` forces the mock host)
- integration validation: Extension Development Host or installed extension

Recompiling the repo does not update an already installed extension under `~/.vscode/extensions/` or `~/.cursor/extensions/`. Reinstall the VSIX or use the Extension Development Host when you need the installed editor to match the repo build exactly.

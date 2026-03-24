# Architecture

This document describes the current shipped architecture used by `agent-repl` today.

For the north-star target architecture, see:

- [Design Docs](design/README.md)
- [North Star](design/north-star.md)
- [Review Rubric](design/review-rubric.md)

The current system is mixed, but it is no longer purely bridge-driven:

- the public notebook commands now prefer the shared headless runtime in `src/agent_repl/core/`
- the VS Code extension still matters for live editor projection, prompt-cell UX, kernel discovery, and extension reload
- the extension still hosts bridge routes for editor-backed and compatibility features

## Components

```
Human (VS Code / Cursor, optional)
    ↕
Projection Extension
    ↕
agent-repl Runtime
    ↕
Agent CLI
```

### Shared Runtime (`src/agent_repl/core/`)

The shared runtime owns the headless notebook path. It:

1. Starts a workspace-scoped daemon on demand
2. Creates and manages headless Jupyter kernels
3. Reads, edits, and executes notebooks without requiring the editor
4. Serves runtime-owned notebook projections back to editor clients

Key modules:

| Module | Purpose |
|--------|---------|
| `core/server.py` | Workspace daemon, notebook authority, headless kernel ownership |
| `core/client.py` | Runtime discovery and HTTP client |
| `cli.py` | Public command routing; notebook commands prefer the shared runtime |

### VS Code Extension (`extension/src/`)

The extension is now primarily a projection and editor-integration layer. It:

1. Starts an HTTP server on a random port (or configured port)
2. Writes a connection file to `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
3. Auto-attaches the editor window to the matching shared runtime
4. Projects runtime-owned notebook state into open notebook documents
5. Still handles editor-backed features such as prompt cells, reload, kernel inspection, and compatibility routes

Key modules:

| Module | Purpose |
|--------|---------|
| `server.ts` | HTTP server, route dispatch, bearer token auth |
| `routes.ts` | API endpoint handlers for all operations |
| `discovery.ts` | Connection file creation, token generation |
| `notebook/identity.ts` | Stable UUID cell IDs via metadata |
| `notebook/operations.ts` | Cell edit operations (insert, delete, move, replace) |
| `notebook/resolver.ts` | Find notebooks by path, resolve cells by ID/index |
| `notebook/outputs.ts` | Output format conversion + agent-facing media stripping |
| `execution/queue.ts` | Per-notebook execution queues, kernel state tracking |
| `session.ts` | Auto-attach and runtime projection into open notebook documents |

### CLI (`src/agent_repl/`)

The CLI now has two roles:

1. public notebook commands route to the shared runtime first
2. editor-backed utility commands still talk to the extension bridge

Two files:

| File | Purpose |
|------|---------|
| `cli.py` | Argparse command definitions, handler functions |
| `client.py` | Extension bridge discovery and editor-backed endpoints |

## Connection Discovery

The extension writes a JSON connection file on startup:

```json
{
  "port": 54321,
  "token": "random-bearer-token",
  "version": "0.3.0",
  "workspace_folders": ["/Users/you/project"]
}
```

Location: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json` (macOS) or `~/.local/share/jupyter/runtime/` (Linux).

The CLI scans this directory, sorts by modification time (newest first), and pings `GET /api/health` on each. It only selects a bridge whose workspace matches the current command context (or already has the target notebook open), so it does not silently fall back to a different VS Code window.

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Bridge status + open notebooks |
| GET | `/api/notebook/contents` | Cell sources + outputs (agent-cleaned) |
| GET | `/api/notebook/status` | Kernel state + execution queue |
| GET | `/api/notebook/execution` | Poll execution result by ID |
| POST | `/api/notebook/edit` | Batch cell operations |
| POST | `/api/notebook/execute-cell` | Run existing cell |
| POST | `/api/notebook/insert-and-execute` | Insert + run (fire-and-forget) |
| POST | `/api/notebook/execute-all` | Run all cells |
| POST | `/api/notebook/restart-kernel` | Restart kernel |
| POST | `/api/notebook/restart-and-run-all` | Restart + run all |
| GET | `/api/notebook/kernels` | List available kernels |
| POST | `/api/notebook/select-kernel` | Select kernel (by ID or interactive picker) |
| POST | `/api/notebook/create` | Create notebook file (auto-selects `.venv` kernel) |
| POST | `/api/notebook/open` | Open existing notebook |
| POST | `/api/notebook/prompt` | Create prompt cell |
| POST | `/api/notebook/prompt-status` | Update prompt status |
| POST | `/api/reload` | Hot-reload route handlers |

All POST bodies and responses are JSON. Auth is via `Authorization: token <bearer-token>` header.

## Output Filtering

The extension maintains two output surfaces:

- **Notebook file**: Full rich outputs (HTML, images, widgets, base64 data)
- **API responses**: Stripped to text-only for agent consumption

`toJupyter()` converts VS Code cell outputs to standard Jupyter format. `stripForAgent()` replaces rich media with text placeholders (e.g., `[image: image/png]`).

## Cell Identity

Each cell gets a stable UUID stored in `metadata.custom.agent-repl.cell_id`. The extension stamps missing IDs via `ensureIds()` on first access. Cell IDs survive structural changes (moves, deletes, insertions) — use them instead of indices for reliable targeting.

## Execution Queue

The extension maintains a per-notebook execution queue:

- Agent cells are queued and executed sequentially
- Kernel state is tracked via `executionSummary.timing.endTime` events
- `insert-and-execute` is fire-and-forget: the CLI gets a `cell_id` immediately
- Use `GET /api/notebook/execution?id=<id>` to poll for completion
- Use `GET /api/notebook/status` to see queue state

## Closed Notebooks

- `GET /api/notebook/contents` can read a closed `.ipynb` directly from disk
- `GET /api/notebook/status` returns `kernel_state: "not_open"` with empty queues when the notebook is closed
- Edit, prompt, kernel, and execution routes automatically open the notebook document first when notebook APIs require it
- Notebook paths are restricted to the active workspace by default; the bridge rejects external notebook paths instead of opening them in another window

## Execution Modes

Execution behavior is controlled by the `agent-repl.executionMode` setting:

- `no-yank` (default) first tries Jupyter's background kernel session so an already-open notebook can show running cells and outputs without intentionally stealing focus
- `native` always uses `notebook.cell.execute`, which preserves VS Code's original notebook behavior

Completed execution responses include `execution_mode` so callers can see which backend actually ran, and `execution_preference` so they can see which mode was requested.

`no-yank` is best when a human is working elsewhere in the editor and only wants to glance at notebook progress. The target behavior is that notebook creation and kernel attachment also stay in the background; if those flows reveal the notebook, prompt the user, or ask for a kernel restart, treat that as a bug rather than expected behavior.

## Hot Reload

`POST /api/reload` clears the Node.js `require.cache` for all extension modules (except the entry point) and rebuilds routes in-place. This supports rapid extension development without restarting VS Code.

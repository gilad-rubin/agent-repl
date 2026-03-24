# Architecture

This document describes the current shipped bridge architecture used by `agent-repl` today.

For the north-star target architecture, see:

- [Design Docs](v2/README.md)
- [North Star](v2/north-star.md)
- [Review Rubric](v2/review-rubric.md)

agent-repl uses a bridge architecture: a VS Code extension runs an HTTP server, and the CLI talks to it. Notebook reads and edits go through VS Code's notebook API. Execution can run through either a background Jupyter session (`no-yank`) or VS Code's native notebook command path (`native`).

## Components

```
┌─────────────────────────────────────────────┐
│  VS Code / Cursor                           │
│  ┌───────────────────────────────────────┐  │
│  │  agent-repl Extension                 │  │
│  │  ┌─────────┐  ┌──────────────────┐   │  │
│  │  │ HTTP    │  │ Notebook API     │   │  │
│  │  │ Server  │──│ (read/edit/exec) │   │  │
│  │  └─────────┘  └──────────────────┘   │  │
│  │       ↑                               │  │
│  └───────│───────────────────────────────┘  │
└──────────│──────────────────────────────────┘
           │ HTTP + bearer token
┌──────────│──────────────────────────────────┐
│  CLI     │                                   │
│  ┌───────┴──────┐                            │
│  │ BridgeClient │ → auto-discovers bridge    │
│  └──────────────┘                            │
└──────────────────────────────────────────────┘
```

### VS Code Extension (`extension/src/`)

The extension activates when a Jupyter notebook is opened. It:

1. Starts an HTTP server on a random port (or configured port)
2. Writes a connection file to `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
3. Handles all API requests by calling VS Code's notebook API
4. Manages an execution queue per notebook

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

### CLI (`src/agent_repl/`)

The CLI is a thin HTTP client. It:

1. Scans `~/Library/Jupyter/runtime/` for connection files
2. Pings each bridge's health endpoint
3. Requires a workspace or open-notebook match before sending commands, including the CLI's current working directory for notebook path resolution

Two files:

| File | Purpose |
|------|---------|
| `cli.py` | Argparse command definitions, handler functions |
| `client.py` | `BridgeClient` class, discovery logic, endpoint methods |

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

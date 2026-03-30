# Core Runtime Guide

## Module Map (`src/agent_repl/core/`)

| Module | Responsibility |
|--------|---------------|
| `server.py` | `CoreState` orchestrator — session/runtime/document records, notebook loading/saving |
| `asgi.py` | Starlette ASGI app, `TokenAuthMiddleware`, MCP mount, route registration |
| `db.py` | SQLite persistence — schema, bulk persist/load |
| `mcp_adapter.py` | FastMCP server with tools for all notebook/runtime/session operations |
| `notebook_read_service.py` | Read/projection APIs (contents, status, activity, projection) |
| `notebook_write_service.py` | Command/mutation wrappers (edit, create, select-kernel) |
| `notebook_mutation_service.py` | Private mutation engine — routes edits through YDoc then to nbformat |
| `notebook_execution_service.py` | Headless execution/restart engine |
| `execution_ledger_service.py` | Server-owned run truth — queue, promotion, execution records |
| `collaboration_service.py` | Sessions, presence, cell leases, branches, review |
| `ydoc_service.py` | YDoc-backed notebook editing via jupyter_ydoc CRDTs |
| `collaboration.py` | Collaboration data models and ranking policy |

## CLI Surface Notes

The public CLI in `src/agent_repl/cli.py` now includes onboarding and verification commands in addition to notebook operations:

- `setup` — orchestrates workspace onboarding actions and returns post-action JSON state
- `doctor` — inspects CLI, workspace kernel readiness, editor defaults, and optional MCP state
- `editor configure --default-canvas` — updates workspace `.vscode/settings.json` to prefer `agent-repl.canvasEditor` for `*.ipynb`

These commands are still thin adapters: they should reuse the same runtime and MCP helpers as the rest of the public CLI instead of inventing parallel install or configuration logic.

## Route Modules

Each exports a `routes(state) -> list[Route]` function consumed by `asgi.py`:

| Module | Domain | Routes |
|--------|--------|--------|
| `notebook_http_routes.py` | Notebook CRUD, execution, projection | ~19 POST |
| `collaboration_http_routes.py` | Sessions, presence, branches, leases | ~13 |
| `document_http_routes.py` | Document tracking | ~4 |
| `runtime_http_routes.py` | Runtime lifecycle, runs | ~9 |

## Request Models

Typed dataclasses with `from_payload()` for request validation:

- `notebook_requests.py` — 13 models (NotebookPathRequest, NotebookEditRequest, etc.)
- `collaboration_requests.py` — 12 models (SessionStartRequest, PresenceUpsertRequest, etc.)
- `runtime_requests.py` — 7 models (RuntimeStartRequest, RunStartRequest, etc.)
- `document_requests.py` — 3 models (DocumentOpenRequest, etc.)

## Persistence

- **SQLite** (`{workspace}/.agent-repl/core-state.db`) — sessions, documents, branches, runtimes, runs, executions, activity
- **WAL mode** for read/write concurrency
- **Current tables created on open** — the daemon creates the operational tables it needs when the DB is opened
- **Activity TTL** — records older than 7 days pruned on persist

## Key Design Rules

- `CoreState` is an orchestrator. Business logic lives in service modules.
- Notebook mutations route through `YDocService` (CRDT) then mirror to nbformat for disk persistence.
- Execution truth is server-owned. Clients derive queued/running state from daemon, not local inference.
- Leases remain for concurrency control alongside YDoc — eventual decommission once CRDT path is proven.
- MCP tools call the same `CoreState` methods as CLI and REST. Keep them in sync.
- Public onboarding commands should stay JSON-first so coding agents can execute and verify them without scraping prose output.

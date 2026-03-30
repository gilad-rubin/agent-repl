# agent-repl

Runtime-first notebook system for agents and humans.

## Quick Start

```bash
uv run agent-repl <command>              # CLI from source
uv run agent-repl core status            # daemon diagnostics
uv run pytest                            # Python tests
cd extension && npm run compile          # rebuild extension
cd extension && npm run preview:webview  # browser canvas preview
```

## Architecture

```
Human or Agent
    ↕
CLI / Browser Preview / VS Code Canvas
    ↕
ASGI Daemon + MCP (src/agent_repl/core/)
    ↕
SQLite state + YDoc notebooks + headless kernels
```

All surfaces call the same `CoreState` service layer. See [dev/core-guide.md](dev/core-guide.md) for module map and design rules, [dev/extension-guide.md](dev/extension-guide.md) for extension internals.

## Rules

- **TDD by default.** Behavior changes ship with the narrowest relevant test.
- **Docs are part of done.** Update `AGENTS.md`, `SKILL.md`, `docs/`, `dev/` together.
- **Adapters stay thin.** CLI, extension, browser, MCP reuse shared contracts — no re-encoding notebook semantics.
- **Execution truth is server-owned.** Clients derive queue/running state from the daemon.
- **Mutations route through YDoc** then mirror to nbformat for disk. See [dev/core-guide.md](dev/core-guide.md).

## Coupling

API changes touch multiple layers — keep in sync:

1. CLI parser/handlers (`src/agent_repl/cli.py`)
2. Core services (`src/agent_repl/core/server.py`, service modules)
3. Route modules + request models (`src/agent_repl/core/*_http_routes.py`, `*_requests.py`)
4. MCP tools (`src/agent_repl/core/mcp_adapter.py`)
5. Extension routes (`extension/src/routes.ts`) when editor-backed
6. Docs (`README.md`, `docs/`, `dev/`)

<important if="editing files in extension/src/ or extension/webview-src/">

## Extension Work

Read [dev/extension-guide.md](dev/extension-guide.md) for module map, dev loops, shared modules, error handling patterns, and canvas icon rules.

Key points:
- `execution/queue.ts` is the most complex module — read fully before modifying
- Execution paths must stay background-safe (no focus stealing)
- Use `npm run preview:webview` for renderer work, Extension Development Host for integration
- Prefer `@carbon/icons-react` over custom SVG for notebook chrome icons

</important>

<important if="editing files in src/agent_repl/">

## CLI + Core Work

Read [dev/core-guide.md](dev/core-guide.md) for the full module map, route structure, and persistence details.

Key points:
- `client.py` = extension bridge; `core/client.py` = shared runtime client; `cli.py` = public surface
- Hidden `agent-repl core ...` commands are the diagnostics surface
- Source input pattern (`-s`, `--source-file`, stdin) is shared across `ix`, `respond`, `edit` — keep consistent
- Omitted `--session-id` reuses the preferred active human session

</important>

<important if="debugging connection, discovery, or bridge issues">

## Troubleshooting

- Connection files: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
- `BridgeClient.discover()` scans files, matches `cwd`, pings health, picks freshest
- Stale files from dead processes are the most common failure
- `agent-repl core status` for daemon issues; `agent-repl core sessions` for session disagreements
- `agent-repl reload --pretty` reports `extension_root` and `routes_module` paths

</important>

## Further Reading

- [dev/README.md](dev/README.md) — dev docs index
- [dev/core-guide.md](dev/core-guide.md) — core module map, persistence, design rules
- [dev/extension-guide.md](dev/extension-guide.md) — extension modules, dev loops, patterns
- [dev/current-architecture.md](dev/current-architecture.md) — shipped topology
- [dev/architecture-modernization-plan.md](dev/architecture-modernization-plan.md) — modernization target stack
- [dev/behavior-locks/](dev/behavior-locks/) — preserved product behaviors and their test anchors

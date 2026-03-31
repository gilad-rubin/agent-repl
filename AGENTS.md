# agent-repl

Runtime-first notebook system for agents and humans.

## Quick Start

```bash
uv run agent-repl <command>              # CLI from source
uv run agent-repl setup --smoke-test     # onboarding checks + notebook smoke test
uv run agent-repl doctor --probe-mcp     # install/workspace/editor/MCP readiness
uv run agent-repl editor dev --editor vscode  # preferred extension dev loop
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

### JupyterLab Direction

The human-facing notebook surface is now moving onto a real JupyterLab notebook implementation.

What JupyterLab should own:

- code and markdown cell editing behavior
- command/edit mode semantics and notebook keyboard flows
- notebook output rendering, trust semantics, and widget-compatible rendering
- notebook-level styling/interaction defaults wherever upstream already has a strong answer

What `agent-repl` must continue to own:

- daemon/runtime/session authority
- headless execution and attach/detach behavior
- workspace routing and cross-project notebook resolution
- collaboration/session attribution, leases, branches, and review workflow
- product shell concerns around explorer/toolbar integration and browser/VS Code hosting

Treat old custom notebook-surface code as transitional unless it is clearly host-specific or product-specific.

## Rules

- **TDD by default.** Behavior changes ship with the narrowest relevant test.
- **Docs are part of done.** Update `AGENTS.md`, `SKILL.md`, `docs/`, `dev/` together.
- **Adapters stay thin.** CLI, extension, browser, MCP reuse shared contracts — no re-encoding notebook semantics.
- **Execution truth is server-owned.** Clients derive queue/running state from the daemon.
- **Staleness and conflict recovery are product behavior.** CLI, MCP, browser, and IDE surfaces should detect stale or mismatched state, self-heal when safe, and otherwise return actionable next steps instead of generic failures.
- **Mutations route through YDoc** then mirror to nbformat for disk. See [dev/core-guide.md](dev/core-guide.md).
- **Commit and push autonomously** as you work on a feature. This will allow backtracking.

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

Read [dev/extension-guide.md](dev/extension-guide.md) for module map, dev loops, shared modules, error handling patterns, and canvas icon rules. For browser QA, preview troubleshooting, and how to verify incremental cell output properly, use [dev/browser-verification-guide.md](dev/browser-verification-guide.md).

Key points:
- `execution/queue.ts` is the most complex module — read fully before modifying
- Execution paths must stay background-safe (no focus stealing)
- Use `npm run preview:webview` for renderer work, Extension Development Host for integration
- `extension/webview-src/jupyterlab-preview.tsx` is the current notebook-surface spike and should be the default place to extend notebook semantics before growing `main.tsx` further
- Prefer replacing bespoke notebook behavior with JupyterLab primitives instead of duplicating it in the host shell
- Keep custom UI work focused on host shell/product affordances; treat notebook-editing and rich-output behavior as JupyterLab-owned unless there is a clear product reason not to
- Prefer `agent-repl editor dev --editor vscode` over testing an installed extension during normal development
- When claiming a browser fix, verify it with the browser workflow in [dev/browser-verification-guide.md](dev/browser-verification-guide.md), including intermediate cell output when execution/rendering changed
- Prefer `@carbon/icons-react` over custom SVG for notebook chrome icons
- `npm run compile` rebuilds the repo copy only. It does not update an installed extension under `~/.vscode/extensions/` or `~/.cursor/extensions/`
- If you manually sync an installed extension, sync the full compiled `extension/out/` and `extension/media/` trees together. Partial file copies can leave a mixed module graph and produce misleading regressions or assertions
- `Agent REPL: Reload` hot-reloads routes and refreshes open canvases, but changes that affect `extension.js` still require a full VS Code window reload
- If the repo source and live behavior disagree, suspect installed-extension drift before changing notebook logic
- Webview markdown rendering must normalize notebook sources to strings before passing them to `marked`; nbformat-style arrays and nullish values should be treated as valid input shapes

</important>

<important if="editing files in src/agent_repl/">

## CLI + Core Work

Read [dev/core-guide.md](dev/core-guide.md) for the full module map, route structure, and persistence details.

Key points:
- `client.py` = extension bridge; `core/client.py` = shared runtime client; `cli.py` = public surface
- Hidden `agent-repl core ...` commands are the diagnostics surface
- Public onboarding commands are `agent-repl setup`, `agent-repl doctor`, `agent-repl editor configure --default-canvas`, and `agent-repl editor dev`
- `setup` should report post-action state in JSON so agents can continue safely after editor or MCP configuration
- When stale server, workspace mismatch, route mismatch, or lease/runtime conflicts are detected, prefer structured recovery metadata and safe automatic fallback over bare string errors
- Source input pattern (`-s`, `--source-file`, stdin) is shared across `ix`, `respond`, `edit` — keep consistent
- Omitted `--session-id` reuses the preferred active human session

</important>

<important if="debugging connection, discovery, or bridge issues">

## Troubleshooting

- For browser preview QA, stale-port confusion, or streamed-output verification, follow [dev/browser-verification-guide.md](dev/browser-verification-guide.md)
- Connection files: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
- `BridgeClient.discover()` scans files, matches `cwd`, pings health, picks freshest
- Stale files from dead processes are the most common failure
- `agent-repl core status` for daemon issues; `agent-repl core sessions` for session disagreements
- `agent-repl reload --pretty` reports `extension_root` and `routes_module` paths
- `agent-repl doctor` and `agent-repl reload --pretty` now report repo-vs-installed extension build drift when the workspace contains `extension/`
- Browser preview and IDE canvases should treat refresh/reload as part of recovery: refresh the notebook surface when local state is stale, reload the bridge or preview when server/module state is stale, and explain which action the user should take when auto-recovery is not possible
- If hot reload appears to succeed but the UI still behaves like old code, compare the live installed extension under `extension_root` with the repo build. The running copy may still be stale
- For installed-extension debugging, prefer syncing the entire compiled `out/` and `media/` directories instead of patching individual files
- After syncing installed `out/` files or changing `extension.ts`, reload the VS Code window once. `agent-repl reload` alone will not swap the already-loaded extension host entrypoint

</important>

## Further Reading

- [dev/README.md](dev/README.md) — dev docs index
- [dev/browser-verification-guide.md](dev/browser-verification-guide.md) — browser QA, troubleshooting, and streamed-output verification
- [dev/core-guide.md](dev/core-guide.md) — core module map, persistence, design rules
- [dev/extension-guide.md](dev/extension-guide.md) — extension modules, dev loops, patterns
- [dev/current-architecture.md](dev/current-architecture.md) — shipped topology
- [dev/architecture-modernization-plan.md](dev/architecture-modernization-plan.md) — modernization target stack
- [dev/behavior-locks/](dev/behavior-locks/) — preserved product behaviors and their test anchors

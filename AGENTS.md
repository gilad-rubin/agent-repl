# agent-repl

Runtime-first notebook system for agents and humans. See [README.md](/Users/giladrubin/python_workspace/agent-repl/README.md) for user-facing docs and [dev/README.md](/Users/giladrubin/python_workspace/agent-repl/dev/README.md) for development docs.

## Development

```bash
uv run agent-repl <command>              # run the public CLI from source
uv run agent-repl core status            # inspect the workspace runtime directly
uv tool install . --reinstall            # reinstall the CLI from the current repo
uv run pytest                            # Python tests (mock-heavy, no editor required)
cd extension && npm run build:webview    # rebuild the shared canvas bundle only
cd extension && npm run compile          # rebuild the canvas bundle + extension TS output
cd extension && npm run preview:webview  # open the shared browser canvas at http://127.0.0.1:4173/preview.html
cd extension && npm run test:preview     # preview smoke test
cd extension && npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
uv run agent-repl reload --pretty        # confirm which built extension/routes are live
```

## Testing Default

- Treat each user request that changes behavior as TDD by default: add or update automated regression tests for that request.
- Prefer writing the failing test first when the harness is practical; if you need to explore or patch first, still add the test in the same change before finishing.
- Do not ship behavior changes without the narrowest relevant automated coverage in Python tests, extension tests, or both.
- If a request cannot be covered with an automated test, call out the gap explicitly in the final report and explain why.
- During modernization refactors, treat hidden UX decisions as locked product behavior. If a user would notice it, add or update a behavior-lock test before moving internals.
- Preserve intentional test seams when extracting services. If a regression test patches a `CoreState` helper such as `_execute_source`, keep a delegate seam or replace it with an equally direct hook instead of silently removing it.
- Prefer a two-layer regression pass for core refactors: run the tight targeted subset for the touched seam first, then rerun the full Python suite before committing.

## Documentation Sync Default

- When a feature, command, workflow, architecture detail, dev loop, or user-visible behavior changes, update the durable docs in the same change when they are affected.
- Check and update all relevant surfaces together: `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/`.
- Do not treat doc sync as optional follow-up work for shipped behavior changes; it is part of done.

## Architecture

```
Human or Agent
    ↕
CLI / Browser Preview / VS Code Canvas
    ↕
Shared Runtime (`src/agent_repl/core/`)
    ↕
Notebook files + headless kernels
```

- Public notebook commands prefer the shared runtime in `src/agent_repl/core/`, even when no editor is attached
- The VS Code extension is now primarily a projection and editor-integration layer: custom canvas editor, prompt cells, kernel discovery, reload, and compatibility routes
- The browser preview and the VS Code canvas both render the same bundle from `extension/webview-src/main.tsx`
- CLI notebook commands, the VS Code canvas, and the browser preview reuse the active human workspace session by default when one already exists
- Prefer typed request/response contracts for notebook operations. Shared request models under `src/agent_repl/core/` are the source of truth for core client/server notebook APIs.
- Keep `CoreState` moving toward an orchestrator role. Read, mutation, execution, and command wrappers should live in focused service modules instead of accumulating back into `server.py`.
- Keep collaboration concerns cohesive. Session selection, presence, cell leases, and lease-conflict payloads belong together because notebook services depend on them as one domain.
- Keep session lifecycle and branch review in the same collaboration boundary as leases/presence. Lease conflicts already surface branch handoff, so splitting review flows back out makes the design harder to follow.
- Public subcommands return JSON; top-level help and version output remain plain text

## Modernization Notes

- When extracting code out of `src/agent_repl/core/server.py`, group by cohesive responsibility:
  - read/projection APIs
  - mutation/edit/project-visible logic
  - execution/restart logic
  - session/presence/lease collaboration logic
- Prefer service modules that compose over `CoreState` rather than new mini-frameworks. The current good pattern is a thin service class with explicit methods and `CoreState` delegation.
- Preserve lease lock ordering during collaboration refactors: resolve notebook/cell identity under `_notebook_lock`, then mutate lease state under `_lock`.
- Presence and cell leases are intentionally transient collaboration state. Keep them out of persisted core state unless the product decision explicitly changes.
- Add direct service-level tests for collaboration edge cases when extracting them: preferred-session ranking, lease TTL refresh on touch, and branch review activity should not rely only on broader end-to-end coverage.
- Keep adapters thin. CLI, browser preview, VS Code, and future MCP surfaces should reuse shared contracts and core services rather than re-encoding notebook semantics locally.
- For the core daemon HTTP surface, prefer small route modules by domain (`notebook`, `collaboration`, `runtime`, `document`) over one giant handler with repeated validation branches.
- When extracting route modules, give each one its own focused tests so request validation and dispatch behavior stay locked without relying only on end-to-end daemon tests.
- For Piece 2 execution work, treat the daemon run ledger as the source of truth for `queued` and run promotion, but do not let ledger transitions trample a live headless notebook execution that is already keeping the runtime busy.
- Keep `runtime.current_execution` for now as a live projection of server-owned execution state. The modernization path is to make it derive from stronger daemon truth, not to delete it before the browser/editor clients have a compatible replacement.
- When extending headless execution truth, prefer daemon-owned `execution_id`s and a server-first `notebook_execution()` lookup path before adding new transport complexity. That keeps polling semantics stable while we move execution authority out of editor/projection code.
- For extension/browser execution UI, prefer hydrating `queued`/`running` cell state from the daemon's notebook status/runtime refresh path when available, and use event-driven local inference only as a fallback. That keeps hidden queue semantics intact while shrinking client-side truth duplication.
- When the daemon already owns execution truth, carry that same queued/running state through notebook activity payloads too. Runtime refresh and activity polling should not drift into two different notions of execution state.
- In the legacy extension queue path, collapse duplicate "inspect running state / decide immediate vs queued / build queue payload" code into shared helpers before attempting larger behavioral changes. That keeps the older compatibility path readable while we gradually retire duplicated truth.
- In the canvas renderer, prefer extracting pure execution-bucket helpers (`queued`, `executing`, `failed`, `paused`) into shared modules before editing event handlers in place. That makes queue/running refactors easier to verify and reduces accidental drift between runtime updates, activity updates, and local command starts.
- For canvas activity polling, prefer a pure reducer-style helper for execution event transitions (`execution-started`, output-appended, finished, structural reload triggers) instead of mutating queued/executing/paused sets inline inside `main.tsx`.
- Apply the same reducer-style rule to direct canvas execution messages too. `execute-started`, `execute-finished`, and `execute-failed` should flow through a shared transition helper before the renderer updates timing/error UI.
- Before deleting an old helper, search tests for direct patching or mocking of that helper. Some internal methods are part of the regression harness even if they are not public APIs.
- If a refactor touches run-all, restart-and-run-all, save/flush, notebook switching, or trailing-cell reuse, update the matching behavior-lock docs under `dev/behavior-locks/` in the same change.

## Coupling

API changes usually require updating multiple layers together:

1. Public CLI parser/handlers in `src/agent_repl/cli.py`
2. Shared runtime surface in `src/agent_repl/core/client.py` and `src/agent_repl/core/server.py`
3. Extension routes in `extension/src/routes.ts` when the feature is editor-backed or compatibility-facing
4. Bridge client calls in `src/agent_repl/client.py` when the public command still talks to the extension
5. Docs in `README.md`, `docs/`, and `dev/`

<important if="editing files in extension/src/">

## Extension Notes

- `routes.ts` is still the extension API surface for editor-backed features and compatibility paths
- `execution/queue.ts` is the most complex module — read fully before modifying
- The extension also acts as a projection client for headless runtimes through `session.ts`
- `session.ts` and the standalone preview server should keep default human-session reuse aligned so browser, VS Code, and CLI requests behave the same unless a caller explicitly supplies `--session-id`
- The canvas custom editor lives in `editor/provider.ts`; it mounts the shared bundle through `editor/webview.ts` and proxies runtime traffic through `editor/proxy.ts`
- Execution paths must stay background-safe; if a path steals focus or surfaces UI, treat that as a product bug
- `executingCells` map tracks running cells via `onDidChangeNotebookDocument`; `reconcileKernelState()` checks real kernel status before declaring busy
- `POST /api/reload` clears `require.cache` for all modules under `out/` except `extension.js` and `server.js`
- Canvas Pyright shadow files live under the workspace-local `.agent-repl/pyright/` tree rather than beside notebooks
- For canvas UI work, prefer `cd extension && npm run preview:webview` first. It serves the real bundled canvas in a browser with a simulated notebook runtime, which is the fastest loop for renderer checks.
- Use the browser preview for renderer-only work in `extension/webview-src/`. Use the Extension Development Host or installed extension when you need real VS Code messaging, session attach, or custom-editor lifecycle behavior.
- The in-editor fast loop is: `cd extension && npm run compile`, then run `Agent REPL: Reload`. That hot-reloads routes/modules and refreshes open canvas panels without reinstalling the VSIX.
- Changes to `extension.ts` or `server.ts` require full window reload; everything else can hot-reload
- Recompiling does NOT update an installed extension under `~/.vscode/extensions/` — reinstall the `.vsix` or use Extension Development Host
- The browser preview does not exercise VS Code messaging, kernel attach, or custom-editor lifecycle. Before signoff on integration-sensitive changes, verify once in the Extension Development Host or installed extension.
- The browser preview does exercise the standalone session-selection path. If preview and VS Code disagree on lease behavior, inspect how the active human session is being reused before changing lease rules.
- Dirty-draft behavior around execute-all, restart-and-run-all, and notebook switching is high-risk. If you touch those flows, add or tighten explicit regression coverage instead of relying on current behavior by inference.
- `browserCanvasUrl` lets the installed extension prefer preview-served `canvas.js` and `canvas.css` on loopback, with a fallback to packaged assets. If preview and installed UI disagree, suspect asset drift first.
- If CLI behavior disagrees with the repo source, suspect installed-extension drift before debugging notebook state. `agent-repl reload --pretty` reports the live `extension_root` and `routes_module`; verify those paths point at the build you meant to test.

</important>

<important if="editing files in extension/webview-src/">

## Canvas Icon Matching

- When an icon should feel native to the notebook chrome, prefer `@carbon/icons-react` exports over custom inline SVG. This keeps stroke weight, corner treatment, and optical sizing aligned with the rest of the Carbon UI.
- To discover the available Carbon icon variants quickly, inspect the package exports from `extension/` with a focused Node query, for example:
  `node -e "const icons=require('@carbon/icons-react'); console.log(Object.keys(icons).filter((name) => /Play|Chevron|Caret|Add/i.test(name)))"`
- Prefer the simplest matching Carbon glyph first. Avoid extra circles, fills, badges, or other decoration unless the mockup explicitly calls for them.
- Keep browser-only chrome shortcuts browser-only. The standalone browser canvas owns `Cmd/Ctrl+B` for the explorer and `Cmd/Ctrl+S` for explicit draft flushes, but the VS Code-hosted canvas should still defer normal save behavior to the editor shell.
- After changing an icon in `extension/webview-src/`, run `cd extension && npm run build:webview` and verify in the preview that its weight and alignment match the adjacent Carbon icons.

</important>

<important if="writing catch blocks or alternate-path logic in extension/src/">

## Error Handling: No Silent Swallowing

Never write `catch { continue; }` or `catch { return undefined; }` without capturing diagnostics. When an operation fails silently, the CLI gets a useless `selection_failed` / error status with no way to debug.

**Required pattern** — collect diagnostics and surface them in the response:
```typescript
const diagnostics: AttachDiagnostic[] = [];
try {
    await riskyOperation();
} catch (err: any) {
    diagnostics.push({ method: 'operationName', detail: err?.message ?? String(err) });
    continue; // or return, but the error is captured
}
```

When a multi-method attach or selection attempt fails, return ALL diagnostics so the caller can see which steps were attempted and why each failed.

</important>

<important if="editing files in src/agent_repl/">

## CLI Notes

- `client.py`: extension bridge discovery + HTTP calls. `core/client.py`: shared runtime client. `cli.py`: public command surface
- Hidden `agent-repl core ...` commands are the runtime diagnostics surface: sessions, presence, documents, notebook activity/projection, leases, branches, runtimes, and runs
- Source input pattern (`-s`, `--source-file`, stdin) is shared across `ix`, `respond`, `edit replace-source`, `edit insert` — keep consistent
- Cell targeting (`--cell-id` or `-i INDEX`) is shared across cell-specific commands — keep consistent
- For notebook mutations and executions, treat omitted `--session-id` as meaningful behavior: the CLI should reuse the preferred active human session when possible instead of inventing a fresh owner
- When adding or changing core-backed notebook commands, prefer shaping the request through shared notebook request-contract helpers instead of open-coded dictionaries.

</important>

<important if="adding a new CLI command or modifying command arguments">

## Adding/Changing Commands

Both sides must be updated:
1. Public CLI parser/handler in `src/agent_repl/cli.py`
2. Shared-runtime method in `src/agent_repl/core/client.py` and `src/agent_repl/core/server.py` when the command is core-backed
3. Extension route/client updates in `extension/src/routes.ts` and `src/agent_repl/client.py` when the command is editor-backed
4. Test in `tests/test_agent_repl.py` and extension tests when relevant
5. Docs: `README.md`, `docs/commands.md`, `SKILL.md`

</important>

<important if="debugging connection, discovery, or bridge issues">

## Bridge Troubleshooting

- Connection files: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
- `BridgeClient.discover()` scans files, matches `cwd` to `workspace_folders`, pings health, picks freshest healthy one
- Stale files from dead processes are the most common failure mode
- The shared runtime also writes its own workspace-scoped connection metadata under the Jupyter runtime directory; use `agent-repl core status` when the runtime itself is suspect
- If browser preview, VS Code, and CLI disagree about lease ownership, inspect `agent-repl core sessions` first to see which human session is active and whether a surface failed to reuse it
- `agent-repl reload` returns `extension_root` and `routes_module` paths — use to verify which build is loaded
- Extension symlink lives at `~/.cursor/extensions/agent-repl.agent-repl-<version>` (or `~/.vscode/extensions/`)
- If `agent-repl cat` shows `cells: []` for a notebook that has cells on disk, treat that as a bridge/runtime issue first, not notebook JSON corruption. Compare the on-disk file, run `agent-repl reload --pretty`, and confirm the installed extension is not stale before changing notebook-resolution logic.

</important>

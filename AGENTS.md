# agent-repl

Runtime-first notebook system for agents and humans. See [README.md](/Users/giladrubin/python_workspace/agent-repl/README.md) for user-facing docs and [dev/README.md](/Users/giladrubin/python_workspace/agent-repl/dev/README.md) for development docs.

## Development

```bash
uv run agent-repl <command>              # run from source
uv tool install . --reinstall            # install globally (--reinstall forces rebuild from source)
uv run pytest                            # tests (mock-based, no running extension needed)
cd extension && npm run compile          # build extension
cd extension && npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
```

## Architecture

```
Human (VS Code / Cursor, optional)
    ↕
Projection Extension + Editor Commands
    ↕
agent-repl Runtime
    ↕
Agent (CLI)
```

- Core notebook commands prefer the shared runtime, even when the editor is closed
- The extension still hosts editor-specific surfaces such as projection attach, prompt cells, kernel discovery, and reload
- Public subcommands output JSON; top-level help and version output are plain text

## Coupling

API changes require updating both sides together:
- Routes: `extension/src/routes.ts`
- CLI handlers: `src/agent_repl/cli.py`
- Client methods: `src/agent_repl/client.py`

<important if="editing files in extension/src/">

## Extension Notes

- `routes.ts` is still the extension API surface for editor-backed features and compatibility paths
- `execution/queue.ts` is the most complex module — read fully before modifying
- The extension now also acts as a projection client for headless runtimes through `session.ts`
- Execution paths must stay background-safe; if a path steals focus or surfaces UI, treat that as a product bug
- `executingCells` map tracks running cells via `onDidChangeNotebookDocument`; `reconcileKernelState()` checks real kernel status before declaring busy
- `POST /api/reload` clears `require.cache` for all modules under `out/` except `extension.js` and `server.js`
- Changes to `extension.ts` or `server.ts` require full window reload; everything else can hot-reload
- Recompiling does NOT update an installed extension under `~/.vscode/extensions/` — reinstall the `.vsix` or use Extension Development Host
- If CLI behavior disagrees with the repo source, suspect installed-extension drift before debugging notebook state. `agent-repl reload --pretty` reports the live `extension_root` and `routes_module`; verify those paths point at the build you meant to test.

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
- Source input pattern (`-s`, `--source-file`, stdin) is shared across `ix`, `respond`, `edit replace-source`, `edit insert` — keep consistent
- Cell targeting (`--cell-id` or `-i INDEX`) is shared across cell-specific commands — keep consistent

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
- `agent-repl reload` returns `extension_root` and `routes_module` paths — use to verify which build is loaded
- Extension symlink lives at `~/.cursor/extensions/agent-repl.agent-repl-<version>` (or `~/.vscode/extensions/`)
- If `agent-repl cat` shows `cells: []` for a notebook that has cells on disk, treat that as a bridge/runtime issue first, not notebook JSON corruption. Compare the on-disk file, run `agent-repl reload --pretty`, and confirm the installed extension is not stale before changing notebook-resolution logic.

</important>

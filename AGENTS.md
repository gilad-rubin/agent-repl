# agent-repl

CLI + VS Code extension: agents and humans collaborate on Jupyter notebooks in real time. See README.md for user-facing docs; run `agent-repl --help` for CLI reference.

## Development

```bash
uv run agent-repl <command>              # run from source
uv tool install . --reinstall            # install globally (--reinstall forces rebuild from source)
uv run pytest                            # tests (mock-based, no running extension needed)
cd extension && npm run compile          # build extension
cd extension && npx vsce package         # package as .vsix
```

## Architecture

```
Human (VS Code / Cursor)
    ↕
VS Code Extension (HTTP bridge on localhost, bearer token auth)
    ↕
Agent (CLI)
```

- Extension writes `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json` on startup
- CLI auto-discovers by scanning connection files, matching `cwd` to `workspace_folders`, pinging health
- All commands output JSON — never print unstructured text to stdout

## Coupling

API changes require updating both sides together:
- Routes: `extension/src/routes.ts`
- CLI handlers: `src/agent_repl/cli.py`
- Client methods: `src/agent_repl/client.py`

<important if="editing files in extension/src/">

## Extension Notes

- `routes.ts` is the API surface — keep backward-compatible with the Python CLI
- `execution/queue.ts` is the most complex module — read fully before modifying
- Three execution backends (tried in order): private Jupyter session, Jupyter kernel API, VS Code notebook command. First two are "no-yank" (don't steal focus).
- `executingCells` map tracks running cells via `onDidChangeNotebookDocument`; `reconcileKernelState()` checks real kernel status before declaring busy
- `POST /api/reload` clears `require.cache` for all modules under `out/` except `extension.js` and `server.js`
- Changes to `extension.ts` or `server.ts` require full window reload; everything else can hot-reload
- Recompiling does NOT update an installed extension under `~/.vscode/extensions/` — reinstall the `.vsix` or use Extension Development Host

</important>

<important if="editing files in src/agent_repl/">

## CLI Notes

- `client.py`: bridge discovery + HTTP calls. `cli.py`: arg parsing + output formatting
- Source input pattern (`-s`, `--source-file`, stdin) is shared across `ix`, `respond`, `edit replace-source`, `edit insert` — keep consistent
- Cell targeting (`--cell-id` or `-i INDEX`) is shared across cell-specific commands — keep consistent

</important>

<important if="adding a new CLI command or modifying command arguments">

## Adding/Changing Commands

Both sides must be updated:
1. Route in `extension/src/routes.ts`
2. Subcommand in `src/agent_repl/cli.py`
3. Client method in `src/agent_repl/client.py`
4. Test in `tests/test_agent_repl.py`
5. Docs: README.md, docs/commands.md, SKILL.md command table

</important>

<important if="debugging connection, discovery, or bridge issues">

## Bridge Troubleshooting

- Connection files: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
- `BridgeClient.discover()` scans files, matches `cwd` to `workspace_folders`, pings health, picks freshest healthy one
- Stale files from dead processes are the most common failure mode
- `agent-repl reload` returns `extension_root` and `routes_module` paths — use to verify which build is loaded
- Extension symlink lives at `~/.cursor/extensions/agent-repl.agent-repl-<version>` (or `~/.vscode/extensions/`)

</important>

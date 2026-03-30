# agent-repl

**Runtime-first** - `agent-repl` owns a workspace-scoped notebook runtime, so agents can create, edit, and run notebooks even when no editor is open.

**Shared canvas** - VS Code, Cursor, and the browser preview all render the same canvas bundle from `extension/webview-src/main.tsx`.

**Minimal workflow** - The common path is `new`, then `ix`, then `edit` or `exec` only when you need explicit control.

## What You Use

For headless notebook work:

```bash
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
```

For an existing notebook:

```bash
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec notebooks/demo.ipynb --cell-id <id>
agent-repl edit notebooks/demo.ipynb insert --cells-json '[{"type":"markdown","source":"# Notes"},{"type":"code","source":"print(1)"}]'
agent-repl ix notebooks/demo.ipynb --cells-json '[{"type":"markdown","source":"# Step 1"},{"type":"code","source":"x = 2\nx * 3"}]'
```

For a live canvas:

```bash
agent-repl open notebooks/demo.ipynb
agent-repl open notebooks/demo.ipynb --target browser
```

For MCP onboarding:

```bash
agent-repl mcp setup
agent-repl mcp smoke-test
```

For guided onboarding after the CLI is installed:

```bash
agent-repl setup --smoke-test
agent-repl doctor --probe-mcp
```

## What `agent-repl` Does Automatically

- **Workspace daemon** - notebook commands start or reuse a workspace-scoped core daemon in `src/agent_repl/core/`
- **Workspace kernel** - `new` prefers the workspace `.venv` automatically when it exists
- **Workspace scratch state** - the first workspace bootstrap makes sure `.agent-repl/` is ignored in the workspace `.gitignore`
- **Persisted notebook state** - source, outputs, and metadata are written back to disk
- **Shared human session** - CLI notebook commands, the VS Code canvas, and the browser preview reuse the active human workspace session when one already exists
- **Live continuity** - if a human opens the notebook later, the next run can continue from the same headless runtime when it is still alive

## Public Commands

| Command | When to use it |
|---|---|
| `new` | Create a notebook and prepare the runtime |
| `open` | Open a notebook in VS Code or the browser canvas |
| `ix` | Insert a visible cell, run it, and return the result |
| `edit` | Replace source, insert, delete, move, or clear outputs |
| `exec` | Re-run an existing cell or insert and run inline code |
| `cat` | Inspect notebook structure or fetch `cell_id` values |
| `status` | Inspect current notebook state and execution activity |
| `run-all` | Execute every code cell |
| `restart` | Restart the notebook runtime |
| `restart-run-all` | Restart and execute every code cell |
| `kernels` | Inspect available editor-backed kernels |
| `select-kernel` | Change the notebook kernel |
| `prompts` | List prompt cells created from the editor |
| `respond` | Answer a prompt cell from the CLI |
| `setup` | Run onboarding checks and optional workspace setup actions |
| `doctor` | Inspect CLI, workspace, editor, and optional MCP readiness |
| `editor` | Configure workspace editor defaults for VS Code-family editors |
| `mcp` | Start, configure, and verify the MCP server for this workspace |
| `reload` | Hot-reload installed extension routes during development |

Public subcommands return JSON on success. Top-level help and version output are plain text.

## Install

Install the CLI:

```bash
uv tool install /path/to/agent-repl --reinstall
```

Or from this checkout:

```bash
uv tool install . --reinstall
```

Verify:

```bash
agent-repl --version
agent-repl --help
```

If you want live notebook projection in VS Code or Cursor, also install the extension. See [Installation](docs/installation.md).

## Documentation

Public docs:

- [Onboarding](/Users/giladrubin/python_workspace/agent-repl/docs/onboarding.md)
- [Getting Started](/Users/giladrubin/python_workspace/agent-repl/docs/getting-started.md)
- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
- [Installation](/Users/giladrubin/python_workspace/agent-repl/docs/installation.md)
- [MCP](/Users/giladrubin/python_workspace/agent-repl/docs/mcp.md)
- [Prompt Loop](/Users/giladrubin/python_workspace/agent-repl/docs/prompt-loop.md)
- [CLI JSON API](/Users/giladrubin/python_workspace/agent-repl/docs/api/cli-json.md)
- [Docs Summary](/Users/giladrubin/python_workspace/agent-repl/docs/SUMMARY.md)

Development docs:

- [Development Docs](/Users/giladrubin/python_workspace/agent-repl/dev/README.md)
- [Current Architecture](/Users/giladrubin/python_workspace/agent-repl/dev/current-architecture.md)

## Develop

```bash
uv run agent-repl --help
uv run pytest tests/test_agent_repl.py -q
cd extension && npm run compile
cd extension && node --test tests/*.test.js
```

Useful loops:

- **Reinstall CLI**:

```bash
uv tool install . --reinstall
```

- **Rebuild and reinstall extension**:

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
```

- **Hot-reload most extension changes**:

```bash
cd extension
npm run compile
agent-repl reload --pretty
```

Then run `Agent REPL: Reload` in VS Code. Changes to `extension.ts` or `server.ts` still require a full window reload.

- **Browser preview with the same shared canvas bundle**:

```bash
cd extension
npm run preview:webview
```

Then open `http://127.0.0.1:4173/preview.html` in Chrome. Plain `/preview.html` roots itself to the folder where `npm run preview:webview` was launched and auto-selects the first notebook it finds. Use `?path=relative/notebook.ipynb` to jump straight to a specific notebook, or `?mock=1` to force the isolated mock preview instead. That preview uses the same `media/canvas.js` and `media/canvas.css` bundle as the VS Code canvas, but it talks to the runtime through the standalone preview server instead of the VS Code webview host.

The preview server now exposes a standalone health contract and `agent-repl browse` validates it before reusing an existing port. If port `4173` is already serving a stale or workspace-mismatched preview, the CLI will start a fresh preview on another port instead of blindly trusting the old process.

In browser mode the canvas now includes a minimal VS Code-like explorer for `*.ipynb` files in the workspace. Use `Cmd+B` on macOS or `Ctrl+B` elsewhere to collapse or reopen it.

The browser canvas also exposes an explicit Save action in the toolbar. Use that button or press `Cmd+S` on macOS / `Ctrl+S` elsewhere to flush the current browser drafts into the notebook file without waiting for the editor to blur.

When comparing preview and VS Code UI, rebuild before packaging so the installed extension and the repo bundle stay in sync.

## License

MIT

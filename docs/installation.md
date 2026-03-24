# Installation

Get agent-repl running: the VS Code extension and the CLI.

## Prerequisites

- **VS Code or Cursor** (v1.86+)
- **Python 3.10+**
- **uv** — [Install uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it

## 1. Install the VS Code Extension

Build and install the extension:

```bash
make install-ext
```

This builds `extension/agent-repl-0.3.0.vsix` and reinstalls it into VS Code.
If you want the manual steps instead:

```bash
cd extension
npm install
npm run compile
npx vsce package
code --install-extension agent-repl-0.3.0.vsix
```

If you edit the extension in a local repo checkout, `npm run compile` only updates that checkout. VS Code keeps running the installed copy under `~/.vscode/extensions/` until you reinstall the new `.vsix` or launch the repo in an Extension Development Host. `agent-repl reload` only hot-reloads the extension copy that is already active in the current editor window.

Or in Cursor: open the command palette → "Extensions: Install from VSIX..."

The extension auto-starts when you open a `.ipynb` file. You can also start it manually via the command palette: "Agent REPL: Start Bridge".

## 2. Install the CLI

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl --reinstall

# Or from inside this repo checkout
make install-dev

# Or as a dev dependency in another project
uv add --dev agent-repl --path /path/to/agent-repl
```

Verify:

```bash
agent-repl --version
agent-repl v2 --help
```

```
0.3.0
```

You can also run the repo-provided verification shortcut:

```bash
make verify-install
```

## 3. Verify the Setup

1. Open a `.ipynb` file in VS Code (the extension starts automatically)
2. Run the CLI:

```bash
agent-repl new test.ipynb
agent-repl ix test.ipynb -s 'print("agent-repl is working")'
agent-repl cat test.ipynb
```

You should see the cell and its output in both the CLI response and VS Code.

## Extension Configuration

Settings available in VS Code (Settings → Extensions → Agent REPL):

| Setting | Default | Description |
|---------|---------|-------------|
| `agent-repl.port` | `0` (auto) | Fixed port for the bridge server |
| `agent-repl.autoStart` | `true` | Start bridge automatically on notebook open |
| `agent-repl.v2AutoAttach` | `true` | Auto-attach the VS Code window to the matching v2 core when the bridge starts |
| `agent-repl.cliPath` | `""` | Optional explicit `agent-repl` launcher path or command for extension-host v2 auto-attach |
| `agent-repl.maxQueueSize` | `20` | Maximum queued executions per notebook |
| `agent-repl.executionTimeout` | `300` | Execution timeout in seconds |
| `agent-repl.executionMode` | `no-yank` | `no-yank` prefers background execution to avoid stealing focus; `native` always uses VS Code's notebook command path |

## Troubleshooting

**"No running agent-repl bridge found"**
- Make sure VS Code/Cursor is open with a `.ipynb` file
- Check that the extension is installed: look for "Agent REPL" in the activity bar
- Manually start: Command Palette → "Agent REPL: Start Bridge"

**"Installed CLI is missing new commands like `v2`"**
- Check the installed version with `agent-repl --version`
- Reinstall local path installs with `uv tool install /path/to/agent-repl --reinstall` or `make install-dev`
- When working from source without reinstalling, prefer `uv run --project /path/to/agent-repl agent-repl ...`

**"Installed extension is still an older build"**
- Run `make install-ext`
- Then run `agent-repl reload --pretty` from the target workspace and confirm `extension_root` points at the new installed version

**"v2 auto-attach cannot find agent-repl"**
- Set `agent-repl.cliPath` if the extension host cannot resolve the CLI from PATH
- The extension prefers this order for v2 auto-attach: configured `cliPath`, workspace-local `.venv` launcher, `uv run agent-repl`, then plain `agent-repl`
- Check the extension host logs for the full launcher attempt diagnostics

**Connection file not found**
- The extension writes to `~/Library/Jupyter/runtime/` (macOS) or `~/.local/share/jupyter/runtime/` (Linux)
- Check for `agent-repl-bridge-*.json` files in that directory

## Next Steps

- [Getting Started](getting-started.md) — End-to-end tutorial
- [Command Reference](commands.md) — All commands with examples

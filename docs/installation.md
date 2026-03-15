# Installation

Get agent-repl running: the VS Code extension and the CLI.

## Prerequisites

- **VS Code or Cursor** (v1.86+)
- **Python 3.10+**
- **uv** — [Install uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it

## 1. Install the VS Code Extension

Build and install the extension:

```bash
cd extension
npm install
npm run compile
npx vsce package
```

This produces a `.vsix` file. Install it:

```bash
code --install-extension agent-repl-0.2.0.vsix
```

If you edit the extension in a local repo checkout, `npm run compile` only updates that checkout. VS Code keeps running the installed copy under `~/.vscode/extensions/` until you reinstall the new `.vsix` or launch the repo in an Extension Development Host. `agent-repl reload` only hot-reloads the extension copy that is already active in the current editor window.

Or in Cursor: open the command palette → "Extensions: Install from VSIX..."

The extension auto-starts when you open a `.ipynb` file. You can also start it manually via the command palette: "Agent REPL: Start Bridge".

## 2. Install the CLI

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl

# Or as a dev dependency in another project
uv add --dev agent-repl --path /path/to/agent-repl
```

Verify:

```bash
agent-repl --help
```

```
usage: agent-repl [-h] [--pretty] {reload,cat,status,edit,exec,ix,run-all,restart,restart-run-all,new,kernels,select-kernel,prompts,respond} ...
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
| `agent-repl.maxQueueSize` | `20` | Maximum queued executions per notebook |
| `agent-repl.executionTimeout` | `300` | Execution timeout in seconds |
| `agent-repl.executionMode` | `no-yank` | `no-yank` prefers background execution to avoid stealing focus; `native` always uses VS Code's notebook command path |

## Troubleshooting

**"No running agent-repl bridge found"**
- Make sure VS Code/Cursor is open with a `.ipynb` file
- Check that the extension is installed: look for "Agent REPL" in the activity bar
- Manually start: Command Palette → "Agent REPL: Start Bridge"

**Connection file not found**
- The extension writes to `~/Library/Jupyter/runtime/` (macOS) or `~/.local/share/jupyter/runtime/` (Linux)
- Check for `agent-repl-bridge-*.json` files in that directory

## Next Steps

- [Getting Started](getting-started.md) — End-to-end tutorial
- [Command Reference](commands.md) — All commands with examples

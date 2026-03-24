# Installation

**CLI first** - The agent workflow works headlessly, so installing the CLI is the primary setup step.

**Editor optional** - Install the VS Code or Cursor extension when you want live notebook projection, prompt cells, or editor-driven collaboration.

**Reinstall matters** - Use `uv tool install ... --reinstall` so the installed command matches your local source checkout.

## Prerequisites

- **Python 3.10+**
- **uv**
- **Optional editor** - VS Code or Cursor when you want live projection

## Install the CLI

Recommended:

```bash
uv tool install /path/to/agent-repl --reinstall
```

From this checkout:

```bash
uv tool install . --reinstall
```

Verify:

```bash
agent-repl --version
agent-repl --help
```

## Install the Extension

Only required for:

- live notebook projection in VS Code or Cursor
- prompt-cell creation from the editor
- development commands such as `reload`

Build and install it:

```bash
cd extension
npm install
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
```

Cursor users can install the `.vsix` from the command palette.

## Verify the Headless Path

This is the most important verification:

```bash
agent-repl new tmp/install-check.ipynb
agent-repl ix tmp/install-check.ipynb -s 'print(\"agent-repl is working\")'
```

If those succeed, the core notebook workflow is ready even with the editor closed.

## Verify Live Projection

Open the same workspace in VS Code or Cursor, then open the notebook. You should see the existing cells and outputs immediately. If the runtime is still alive, the next manual cell should continue from the same in-memory state.

## Editor Settings

Available extension settings:

| Setting | Default | Description |
|---|---|---|
| `agent-repl.port` | `0` | Fixed port for the extension bridge |
| `agent-repl.autoStart` | `true` | Start the extension bridge automatically |
| `agent-repl.sessionAutoAttach` | `true` | Auto-attach the editor window to the shared runtime |
| `agent-repl.cliCommand` | `""` | Explicit launcher path or command for extension-host auto-attach |
| `agent-repl.maxQueueSize` | `20` | Maximum queued executions per notebook |
| `agent-repl.executionTimeout` | `300` | Execution timeout in seconds |
| `agent-repl.executionMode` | `no-yank` | Background-safe execution preference for editor-backed runs |

## Troubleshooting

**Installed CLI is stale**

```bash
uv tool install /path/to/agent-repl --reinstall
agent-repl --version
```

**Installed extension is stale**

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
agent-repl reload --pretty
```

**Headless `new` fails with no kernel**

How to fix:
- create a workspace `.venv`, or
- pass `--kernel /absolute/path/to/python`

**Prompt loop commands fail**

How to fix:
- open the workspace in VS Code or Cursor
- make sure the extension is installed and running

**Auto-attach cannot launch `agent-repl` inside the extension host**

How to fix:
- set `agent-repl.cliCommand`, or
- ensure the workspace `.venv` contains `agent-repl`, or
- reinstall the CLI globally with `uv tool install ... --reinstall`

## Next Steps

- [Getting Started](/Users/giladrubin/python_workspace/agent-repl/docs/getting-started.md)
- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
- [Prompt Loop](/Users/giladrubin/python_workspace/agent-repl/docs/prompt-loop.md)

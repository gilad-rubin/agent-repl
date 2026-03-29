# Installation

**CLI first** - the shared runtime path is the primary product surface, so install the CLI before you worry about editor integration.

**Editor optional** - install the VS Code or Cursor extension only when you want the canvas UI, prompt cells, or live notebook projection.

**Reinstall matters** - use `uv tool install ... --reinstall` so the installed command matches your local checkout.

## Prerequisites

- **Python 3.10+**
- **uv**
- **Optional editor** - VS Code or Cursor for the canvas and projection features

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
- the Agent REPL canvas editor
- prompt-cell workflows
- extension development commands such as `reload`

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
agent-repl ix tmp/install-check.ipynb -s 'print("agent-repl is working")'
```

If those succeed, the shared runtime path is working even with the editor closed.

## Verify the Canvas

Open the same workspace in VS Code or Cursor, then open the notebook:

```bash
agent-repl open tmp/install-check.ipynb
```

Expected result:

- the notebook opens in the Agent REPL canvas by default
- the previously persisted cells and outputs are visible
- if the headless runtime is still alive, the next run can continue from the same in-memory state

To verify the browser canvas:

```bash
cd extension
npm run preview:webview
```

Then open:

```text
http://127.0.0.1:4173/preview.html
```

Expected result:

- the preview roots itself to the folder where `npm run preview:webview` was launched
- the first workspace notebook opens automatically; `?path=tmp/install-check.ipynb` still targets a notebook directly
- `?mock=1` opens the isolated mock/demo preview instead of the live workspace
- the browser opens the same notebook canvas bundle as the VS Code custom editor
- a minimal notebook explorer appears on the left for workspace `*.ipynb` files
- `Cmd+B` on macOS or `Ctrl+B` elsewhere collapses and reopens that explorer
- the browser toolbar exposes Save, and `Cmd+S` on macOS or `Ctrl+S` elsewhere flushes dirty drafts into the notebook file immediately

The browser canvas now reuses the workspace's active human session when one already exists, so runs from VS Code, the browser preview, and the CLI share the same lease ownership by default.

## Editor Settings

Available extension settings:

| Setting | Default | Description |
|---|---|---|
| `agent-repl.port` | `0` | Fixed port for the extension bridge |
| `agent-repl.autoStart` | `true` | Start the bridge automatically when the workspace opens |
| `agent-repl.sessionAutoAttach` | `true` | Auto-attach the window to the shared runtime session |
| `agent-repl.cliCommand` | `""` | Explicit launcher path or command for CLI auto-attach |
| `agent-repl.pyrightCommand` | `""` | Optional pyright-langserver path for the canvas editor |
| `agent-repl.browserCanvasUrl` | `http://127.0.0.1:4173/preview.html` | Base URL used by `open --target browser` and `new --open --target browser` |
| `agent-repl.maxQueueSize` | `20` | Maximum queued executions per notebook |
| `agent-repl.executionTimeout` | `300` | Execution timeout in seconds |
| `agent-repl.executionMode` | `no-yank` | Background-safe execution preference for editor-backed runs |

The canvas editor's Python IDE features write generated Pyright shadow files under `.agent-repl/pyright/` inside the workspace so notebooks do not accumulate sibling `.py` files.

## Development Install Loops

Fast loop for most extension edits:

```bash
cd extension
npm run compile
agent-repl reload --pretty
```

Then run `Agent REPL: Reload` from the command palette. This hot-reloads route modules and refreshes open canvas editors in place.

Use a full window reload when you changed:

- `extension/src/extension.ts`
- `extension/src/server.ts`

Use a full VSIX reinstall when you are testing the installed extension instead of an Extension Development Host:

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
```

If you compare the browser preview with the installed extension UI, rebuild before packaging so the shared `media/canvas.js` and `media/canvas.css` bundle stays in sync across both surfaces.

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

**Auto-attach cannot launch `agent-repl` inside the extension host**

How to fix:

- set `agent-repl.cliCommand`, or
- ensure the workspace `.venv` contains `agent-repl`, or
- reinstall the CLI globally with `uv tool install ... --reinstall`

**Preview and installed VS Code canvas do not match**

How to fix:

- rebuild the shared webview bundle with `cd extension && npm run compile`
- reinstall the VSIX if you are testing the installed extension
- reload the open canvas editor

## Next Steps

- [Getting Started](/Users/giladrubin/python_workspace/agent-repl/docs/getting-started.md)
- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
- [Prompt Loop](/Users/giladrubin/python_workspace/agent-repl/docs/prompt-loop.md)

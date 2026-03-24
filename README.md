# agent-repl

**Runtime-first** - `agent-repl` owns the shared notebook runtime, so agents can create, edit, and run notebooks from the CLI even when the editor is closed.

**Editor-optional** - VS Code or Cursor can attach later as a live projection client. Humans see the same cells, outputs, and running state without becoming the source of truth.

**Minimal workflow** - The normal happy path is small: `agent-repl new ...`, then `agent-repl ix ...`, then `edit` or `exec` when needed.

## What You Use

For headless notebook work:

```bash
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
```

For existing notebooks:

```bash
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec notebooks/demo.ipynb --cell-id <id>
```

If the notebook is already open in VS Code or Cursor, the editor should reflect the same shared runtime live. If it is closed, the CLI still works.

## What `agent-repl` Does Automatically

- **Workspace kernel** - If a workspace `.venv` exists, `agent-repl new` uses it automatically.
- **Headless runtime** - Creating, editing, and running notebooks works without opening the editor.
- **Persisted notebook state** - Cells, source, and outputs are written back to disk.
- **Live continuity** - If the shared runtime is still alive when a human opens the notebook later, the next cell can continue from live in-memory objects.

## Public Commands

| Command | When to use it |
|---|---|
| `new` | Create a notebook and prepare the runtime |
| `ix` | Insert a new cell, run it, and return the result |
| `edit` | Replace source, insert, delete, move, or clear outputs |
| `exec` | Re-run a known cell or insert and run inline code |
| `cat` | Inspect notebook structure or fetch `cell_id` values |
| `status` | Check whether the notebook is idle or busy |
| `run-all` | Execute all cells |
| `restart` | Restart the notebook runtime |
| `restart-run-all` | Restart and execute all cells |
| `kernels` | Inspect available kernels in an editor-backed workspace |
| `select-kernel` | Explicitly choose a non-default kernel |
| `prompts` | List prompt cells created from the editor |
| `respond` | Answer a prompt cell from the CLI |
| `reload` | Hot-reload the installed extension during development |

Public subcommands return JSON. Use `--pretty` when you want indented output.

## Install

Install the CLI:

```bash
uv tool install /path/to/agent-repl --reinstall
```

Or from this checkout:

```bash
uv tool install . --reinstall
```

Verify the installed surface:

```bash
agent-repl --version
agent-repl --help
```

If you want live notebook projection in VS Code or Cursor, also install the extension. See [Installation](docs/installation.md).

## Documentation

Public docs:

- [Getting Started](/Users/giladrubin/python_workspace/agent-repl/docs/getting-started.md)
- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
- [Installation](/Users/giladrubin/python_workspace/agent-repl/docs/installation.md)
- [Prompt Loop](/Users/giladrubin/python_workspace/agent-repl/docs/prompt-loop.md)
- [CLI JSON API](/Users/giladrubin/python_workspace/agent-repl/docs/api/cli-json.md)
- [Docs Summary](/Users/giladrubin/python_workspace/agent-repl/docs/SUMMARY.md)

Development and architecture docs:

- [Development Docs](/Users/giladrubin/python_workspace/agent-repl/dev/README.md)

## Develop

```bash
uv run agent-repl --help
uv run pytest tests/test_agent_repl.py -q
cd extension && npm run compile
cd extension && node --test tests/*.test.js
```

To reinstall the CLI after local changes:

```bash
uv tool install . --reinstall
```

To rebuild and reinstall the extension:

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
```

## License

MIT

---
name: agent-repl
description: Work against the shared agent-repl notebook runtime. Use this when an agent needs to create notebooks, edit or execute cells, inspect results, or participate in an editor-driven prompt loop.
---

# agent-repl

`agent-repl` is the notebook runtime. The CLI is the normal agent surface. VS Code or Cursor is optional unless you are intentionally using editor-only features such as prompt-cell creation, explicit kernel picking, or extension reload.

Public subcommands print JSON. Top-level help and version output are plain text.

## First Check

Before validating behavior from another workspace:

```bash
agent-repl --version
agent-repl --help
```

If the installed CLI is stale:

```bash
uv tool install /path/to/agent-repl --reinstall
```

## Minimal Happy Path

For a new notebook:

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

For an existing notebook:

```bash
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec notebooks/demo.ipynb --cell-id <id>
```

Use `ix` as the default execution primitive. It inserts a visible code cell, executes it, and returns the result directly.

## What Commands Are For

- `new` - create the notebook and prepare the runtime automatically
- `ix` - insert a new cell, run it, and return the result
- `edit` - explicit notebook mutation
- `exec` - rerun a known cell or insert and run inline code
- `cat` - diagnostics or `cell_id` lookup
- `status` - diagnostics for long-running or uncertain execution

`cat` and `status` are useful, but they are not part of the normal happy path.

## Validation Flow

For a prompt like “test out the new agent-repl capabilities,” start with the smallest happy path:

```bash
agent-repl --version
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

Only inspect structure when you need a `cell_id` for an explicit edit or rerun:

```bash
agent-repl cat tmp/validation.ipynb --no-outputs
agent-repl edit tmp/validation.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec tmp/validation.ipynb --cell-id <id>
```

Then validate an existing notebook:

```bash
agent-repl cat notebooks/demo.ipynb --no-outputs
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'updated code'
agent-repl exec notebooks/demo.ipynb --cell-id <id>
agent-repl ix notebooks/demo.ipynb -s 'x + 1'
```

What to confirm:

- `new` returns a ready notebook
- `ix` returns the result directly
- edited source and outputs stay in sync
- the same commands work with the editor closed
- if the editor is open, the human sees the notebook update live without popups or focus steal

## Kernel Rules

- if a workspace `.venv` exists, it is the default runtime for `new` and `ix`
- the `.venv` must have `ipykernel` installed — if it doesn't, the error will name the `.venv` path and tell you how to fix it
- if no workspace `.venv` exists, pass `--kernel` explicitly
- `select-kernel` changes the active kernel for a notebook in the headless runtime — subsequent `ix` and `exec` use the selected kernel
- use `--interactive` with `select-kernel` only when you want the VS Code kernel picker

```bash
agent-repl select-kernel analysis.ipynb --kernel-id /opt/miniconda3/bin/python3
agent-repl ix analysis.ipynb -s 'import sys; print(sys.executable)'
```

Starter cells from `new --cells-json` are created, not auto-executed.

Failed `ix` calls do not leave orphan cells — if the kernel cannot be resolved or an infrastructure error occurs (kernel crash, connection lost, timeout), the inserted cell is rolled back and the notebook is unchanged. The error message will say "ix failed and the inserted cell was rolled back." Python exceptions in your code are *not* rolled back — those behave like normal notebook cells with error output.

## Editor-Assisted Features

These features still assume the extension is available:

- `prompts`
- `respond`
- `kernels`
- `reload`

If you are validating live editor projection behavior, rebuild/reinstall the extension when needed and then verify:

```bash
agent-repl reload --pretty
```

## Prompt Loop

Human in the editor:

- click **Ask Agent**

Agent in the CLI:

```bash
agent-repl prompts notebooks/demo.ipynb
agent-repl respond notebooks/demo.ipynb --to <cell_id> -s 'df = df.dropna()'
```

Use this only when the notebook is intentionally being used as a conversation surface.

## Troubleshooting

**Need a cell ID**

```bash
agent-repl cat notebooks/demo.ipynb --no-outputs
```

**Notebook still busy**

```bash
agent-repl status notebooks/demo.ipynb
```

**No workspace kernel**

How to fix:
- create a workspace `.venv`, or
- pass `--kernel /absolute/path/to/python`

**Installed extension still runs old code**

How to fix:

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
agent-repl reload --pretty
```

**Prompt loop commands fail in a closed-editor workflow**

How to fix:
- open the workspace in VS Code or Cursor
- make sure the extension is installed and running

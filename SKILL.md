---
name: agent-repl
description: Work against the shared agent-repl notebook runtime. Use this when an agent needs to create notebooks, edit or execute cells, inspect results, or participate in an editor-driven prompt loop.
---

# agent-repl

`agent-repl` is the notebook runtime. The CLI is the normal agent surface. VS Code or Cursor is optional unless you need editor-only features (prompt cells, kernel picker, extension reload). Public subcommands return JSON.

Run `agent-repl --help` for the full command list, and `agent-repl <command> --help` for exact syntax and defaults on any subcommand.

## CLI Reference

The output below is injected dynamically when available. If you see the raw `!` line instead, run the command yourself to get current syntax.

!agent-repl --help

## Before You Start

1. Confirm you are in the correct workspace directory.
2. Run `agent-repl --version` to verify the CLI is current.
3. If the workspace has a `.venv`, it must contain `ipykernel`.
4. If the installed CLI is stale: `uv tool install /path/to/agent-repl --reinstall`

## Quick Start

```bash
# New notebook
agent-repl new scratch.ipynb
agent-repl ix scratch.ipynb -s 'x = 2; x * 3'

# Existing notebook — look up cell IDs, then edit and rerun
agent-repl cat demo.ipynb --no-outputs
agent-repl edit demo.ipynb replace-source --cell-id <id> -s 'x = 7; x ** 2'
agent-repl exec demo.ipynb --cell-id <id>
```

## What Commands Are For

- `new` — create a notebook and prepare the runtime
- `open` — open an existing notebook in VS Code or the browser
- `ix` — insert a new cell, run it, return the result (recommended default)
- `edit` — explicit notebook mutation (insert, replace-source, delete, move, clear-outputs)
- `exec` — rerun a known cell or insert and run inline code
- `cat` — read notebook contents; use `--no-outputs` for cell ID lookup
- `status` — check execution state for long-running or uncertain cells
- `run-all` / `restart` / `restart-run-all` — notebook-wide execution control
- `select-kernel` — switch the notebook kernel
- `prompts` / `respond` — editor-driven prompt loop (requires extension)

## Best Practices

**Prefer `ix` over separate insert + execute.** `ix` inserts a cell, runs it, and returns the result in one call. Use `edit` + `exec` only when you need to modify or rerun an existing cell.

**Batch multiple cells with `--cells-json`.** Instead of calling `ix` five times, pass a JSON array in one call. Each code cell executes sequentially; batch `ix` stops on the first error.

Starter cells passed to `agent-repl new --cells-json` are created but not auto-executed.

```bash
agent-repl ix demo.ipynb --cells-json '[{"type":"code","source":"import pandas as pd"},{"type":"code","source":"df = pd.read_csv(\"data.csv\")\ndf.head()"}]'
```

**Verify your environment before starting work.** Check that you're in the right directory, the CLI is current, and the kernel resolves. Fixing these after creating cells wastes more time than checking upfront.

**Understand rollback behavior.** If `ix` fails due to infrastructure (kernel crash, timeout, connection lost), the inserted cell is rolled back and the notebook is unchanged. Python exceptions in your code are *not* rolled back — those produce normal error output.

**Use `cat --no-outputs` for cell IDs.** When you need to edit or rerun a specific cell, `cat` gives you the cell IDs. Don't guess them.

## Kernel Rules

- If a workspace `.venv` exists, it is the default runtime for `new` and `ix`.
- The `.venv` must have `ipykernel` installed — the error will name the path and tell you how to fix it.
- If no `.venv` exists, pass `--kernel` explicitly.
- `select-kernel` changes the active kernel; subsequent runs use the selected kernel.

## Session Ownership

- `ix`, `edit`, `exec`, `run-all`, and `restart-run-all` automatically reuse the active human session when `--session-id` is omitted.
- Use `--session-id` only when you intentionally need a different collaboration owner.

## Editor-Assisted Features

These commands require the VS Code / Cursor extension: `respond`, `kernels`, `reload`, `open --target vscode`, `new --open`. The `prompts` command works from the CLI but the prompt loop itself is editor-driven.

## Troubleshooting

- **Need a cell ID:** `agent-repl cat notebook.ipynb --no-outputs`
- **Notebook still busy:** `agent-repl status notebook.ipynb`
- **No workspace kernel:** create a `.venv` with `ipykernel`, or pass `--kernel /path/to/python`
- **Stale CLI:** `uv tool install /path/to/agent-repl --reinstall`
- **Stale extension:** rebuild with `cd extension && npm run compile`, repackage the VSIX, reinstall, then `agent-repl reload --pretty`

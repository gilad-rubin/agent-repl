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
3. Run `agent-repl doctor` when you need a structured readiness check for CLI, kernel, editor defaults, or MCP.
4. If the workspace has a `.venv`, it must contain `ipykernel`.
5. If the installed CLI is stale: `uv tool install /path/to/agent-repl --reinstall`
6. If you are developing the extension from this repo, prefer `agent-repl editor dev --editor vscode` over testing an installed copy.

## Quick Start

```bash
# Guided onboarding / verification
agent-repl setup --smoke-test
agent-repl doctor --probe-mcp
agent-repl editor configure --default-canvas

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
- `core checkpoint-create` — snapshot notebook state before risky work
- `core checkpoint-restore` — restore a prior notebook snapshot
- `core checkpoint-list` — list checkpoints for a notebook
- `core checkpoint-delete` — remove a checkpoint
- `setup` — onboarding helper that can configure editor defaults, run MCP setup, and execute a notebook smoke test
- `doctor` — JSON readiness report for install method, workspace kernel, editor defaults, and optional MCP
- `editor configure --default-canvas` — make the Agent REPL canvas the workspace default for `*.ipynb`
- `editor dev` — compile the repo extension and open an Extension Development Host (preferred extension dev loop)
- `prompts` / `respond` — editor-driven prompt loop (requires extension)

## Best Practices

**Prefer `ix` over separate insert + execute.** `ix` inserts a cell, runs it, and returns the result in one call. Use `edit` + `exec` only when you need to modify or rerun an existing cell.

**Batch multiple cells with `--cells-json`.** Instead of calling `ix` five times, pass a JSON array in one call. Each code cell executes sequentially; batch `ix` stops on the first error.

Starter cells passed to `agent-repl new --cells-json` are created but not auto-executed.

```bash
agent-repl ix demo.ipynb --cells-json '[{"type":"code","source":"import pandas as pd"},{"type":"code","source":"df = pd.read_csv(\"data.csv\")\ndf.head()"}]'
```

**Verify your environment before starting work.** Check that you're in the right directory, the CLI is current, and the kernel resolves. Fixing these after creating cells wastes more time than checking upfront.

When onboarding a fresh workspace, prefer `agent-repl setup --smoke-test` over manually stitching together verification commands. When you only need diagnostics, prefer `agent-repl doctor`.

**Understand rollback behavior.** If `ix` fails due to infrastructure (kernel crash, timeout, connection lost), the inserted cell is rolled back and the notebook is unchanged. Python exceptions in your code are *not* rolled back — those produce normal error output.

**Use checkpoints before risky work.** `core checkpoint-create --path notebook.ipynb --label "before refactor"` snapshots the full notebook state. If things go wrong, `core checkpoint-restore --checkpoint-id <id>` brings it back. Restore refuses if cells are still executing.

**Use `cat --no-outputs` for cell IDs.** When you need to edit or rerun a specific cell, `cat` gives you the cell IDs. Don't guess them.

## Kernel Rules

- If a workspace `.venv` exists, it is the default runtime for `new` and `ix`.
- The `.venv` must have `ipykernel` installed — the error will name the path and tell you how to fix it.
- If no `.venv` exists, pass `--kernel` explicitly.
- `select-kernel` changes the active kernel; subsequent runs use the selected kernel.

## MCP Surface (for agents)

The daemon exposes 6 bundled MCP tools at `/mcp`:

- **`notebook_observe`** — Read notebook state. `aspect`: `cells`, `summary`, `queue`, `search`, `activity`, `projection`.
- **`notebook_edit`** — Edit notebook structure. `action`: `edit` (with operations array), `create`.
- **`notebook_execute`** — Run cells. `action`: `cell`, `all`, `insert-and-execute`, `interrupt`, `restart`, `restart-and-run-all`.
- **`notebook_runtime`** — Manage kernels. `action`: `select-kernel`, `status`, `list-runtimes`, `start`, `stop`, `recover`.
- **`workspace_files`** — List and open documents. `action`: `list`, `open`.
- **`checkpoint`** — Snapshot/restore. `action`: `create`, `restore`, `list`, `delete`.

## Session Ownership

- `ix`, `edit`, `exec`, `run-all`, and `restart-run-all` automatically reuse the active human session when `--session-id` is omitted.
- Use `--session-id` only when you intentionally need a different collaboration owner.

## Editor-Assisted Features

These commands require the VS Code / Cursor extension: `respond`, `kernels`, `reload`, `open --target vscode`, `new --open`. The `prompts` command works from the CLI but the prompt loop itself is editor-driven.

`agent-repl editor configure --default-canvas` only writes workspace settings. It does not install the extension for the user.

## Canvas Keyboard Shortcuts

| Shortcut | Mode | Action |
|---|---|---|
| Shift+Enter | Any | Run cell and advance |
| Cmd+Enter | Any | Run cell in place |
| Alt+Enter | Any | Run cell and insert below |
| Cmd+S | Any | Save notebook |
| Cmd+B | Any | Toggle file explorer |
| Escape | Edit | Enter command mode |
| Enter | Command | Enter edit mode |
| A | Command | Insert cell above |
| B | Command | Insert cell below |
| D, D | Command | Delete selected cells |
| M | Command | Change cell to markdown |
| Y | Command | Change cell to code |
| Z | Command | Undo notebook structure |
| Shift+Z | Command | Redo notebook structure |
| Arrow Up / K | Command | Select cell above |
| Arrow Down / J | Command | Select cell below |
| Cmd+Shift+ArrowUp | Command | Move cell up |
| Cmd+Shift+ArrowDown | Command | Move cell down |
| Cmd+A | Command | Select all cells |
| C | Command | Copy selected cells |
| V | Command | Paste cells |
| Shift+M | Command | Merge selected cells |
| Cmd+Shift+- | Edit | Split cell at cursor |
| Cmd+F | Edit | Search within cell (CodeMirror) |
| Cmd+Z / Cmd+Shift+Z | Edit | Undo / redo cell source |

The toolbar also provides a **Clear Outputs** button to clear all cell outputs and reset execution indicators.

## Troubleshooting

- **Need a cell ID:** `agent-repl cat notebook.ipynb --no-outputs`
- **Notebook still busy:** `agent-repl status notebook.ipynb`
- **No workspace kernel:** create a `.venv` with `ipykernel`, or pass `--kernel /path/to/python`
- **Need a full readiness report:** `agent-repl doctor --probe-mcp`
- **Stale CLI:** `uv tool install /path/to/agent-repl --reinstall`
- **Extension drift or stale installed copy:** run `agent-repl doctor`, prefer `agent-repl editor dev --editor vscode`, and use `agent-repl reload --pretty` to inspect build-sync status when you intentionally test the installed extension

---
name: agent-repl
description: Work against a live Jupyter notebook via the VS Code bridge. Use this when an agent needs to read, edit, or execute notebook cells while Cursor/VS Code is open.
---

# agent-repl

CLI for AI agents to work with Jupyter notebooks via the VS Code extension bridge. Agents use the CLI; humans see changes live in VS Code/Cursor. Both share the same kernel.

The extension must be running (auto-starts when a `.ipynb` file is open). Run `agent-repl --help` for full command and flag details.

## Install

```bash
uv tool install /path/to/agent-repl    # global
uv run agent-repl <command>             # or run from source
```

If already on `$PATH`, skip installation.

## Workflow

```bash
agent-repl new analysis.ipynb                       # create notebook
agent-repl ix analysis.ipynb -s 'print("hi")'       # insert + execute cell (waits for completion)
agent-repl ix analysis.ipynb --source-file /tmp/cell.py   # multi-line code from file
agent-repl cat analysis.ipynb                        # read cells + outputs
agent-repl cat analysis.ipynb --no-outputs           # sources only
agent-repl status analysis.ipynb                     # kernel state
agent-repl edit analysis.ipynb replace-source --cell-id <id> -s 'new code'
agent-repl restart analysis.ipynb                    # restart kernel
```

All commands output JSON. Pass `--pretty` for formatted output. Source input accepts `-s 'inline'`, `--source-file path`, or stdin.

## Usage Tips

- Prefer `ix` over `exec` for new code — it inserts a visible cell in the notebook
- Use `--cell-id` over `--index` when possible — IDs survive cell moves and deletes
- After `ix`, read output with `cat` to verify results
- For cell editing subcommands (`insert`, `delete`, `move`, `clear-outputs`), run `agent-repl edit --help`

## Prompts

Humans create prompt cells in VS Code. Agents discover and respond:

```bash
agent-repl prompts demo.ipynb
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
```

The `respond` command atomically: marks the prompt in-progress, inserts a response cell, executes it, marks the prompt answered.

<important if="creating a new notebook or selecting a kernel">

## Kernel Selection

`agent-repl new` auto-selects the workspace `.venv` kernel if present. If no `.venv` and no `--kernel`, the response includes `"kernel_status": "needs_selection"` — use `agent-repl kernels` to list options and `agent-repl select-kernel <path> --kernel-id <id>` to choose.

Creating a brand-new notebook may briefly steal focus (Jupyter kernel startup). After the kernel is attached, execution stays on the no-yank (background) path.

</important>

<important if="a cell is taking a long time, or ix returned status timeout">

## Timeout Handling

`ix` default timeout is **30 seconds**. For longer cells:

```bash
agent-repl ix demo.ipynb --source-file /tmp/cell.py --timeout 300
```

If timeout occurs: the cell **keeps running in the kernel**. Use `agent-repl status` to check when it finishes, then `agent-repl cat` to read output. **Do not queue additional cells** after a timeout.

</important>

<important if="getting errors, 404s, or unexpected behavior from the CLI">

## Troubleshooting

**Cell IDs change after window reload** — Always re-read with `cat` before using `--cell-id`.

**404 errors on execute** — Usually means the cell ID doesn't exist (not a missing route). Re-read cell IDs with `cat`.

**"Kernel is busy" when it's not** — If the user interrupts from VS Code UI, tracking can get stuck. The extension auto-reconciles against real kernel status, but `agent-repl restart <notebook>` forces a full reset.

**CLI has stale code** — `uv tool install <path> --force` reuses cached wheels. Use `--reinstall`:
```bash
uv tool install /path/to/agent-repl --reinstall
```

**Hot-reload scope** — `agent-repl reload` reloads routes and execution queue. Changes to `extension.ts` or `server.ts` require a full VS Code window reload.

</important>

## Execution Modes

Agent-triggered execution defaults to `no-yank`: runs via a background Jupyter session without stealing editor focus. Set `agent-repl.executionMode` to `native` for VS Code's built-in execution. Completed responses include `execution_mode` so you can tell which path ran.

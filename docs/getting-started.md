# Getting Started

Create a notebook, execute code, edit cells, and use the prompt loop — all from the CLI.

## Prerequisites

1. **VS Code or Cursor** with the agent-repl extension installed and running
2. **Python 3.10+** with the CLI installed (`uv tool install /path/to/agent-repl --reinstall` or `make install-dev`)

The extension auto-starts when you open a notebook. Verify the CLI and bridge state first:

```bash
agent-repl --version
agent-repl reload --pretty
```

If `agent-repl --version` is older than the repo version you meant to test, reinstall the CLI. If `agent-repl reload --pretty` points at an older `extension_root` or `routes_module`, reinstall the extension with `make install-ext`, then reload or reopen that VS Code window.

## Create a Notebook

```bash
agent-repl new demo.ipynb
```

```json
{"status":"ok","path":"demo.ipynb","kernel_status":"selected","message":"Selected workspace .venv kernel: subtext (.venv)"}
```

The notebook appears in VS Code immediately with a selected kernel when a workspace `.venv` can be matched. If no `.venv` exists, create returns `"kernel_status": "needs_selection"`, lists the discovered kernels, and includes the exact `agent-repl select-kernel demo.ipynb` command to run next.

Create and kernel attach should stay in the background. If VS Code reveals the notebook, prompts for manual intervention, or asks the user to restart the kernel, treat that as a bug and capture the returned JSON plus the active `execution_mode`.

## Execute Code

Use `ix` (insert-execute) to add a cell and run it:

```bash
agent-repl ix demo.ipynb -s 'import math; print(math.pi)'
```

The cell appears in VS Code with its output. `ix` waits for completion by default; use `--no-wait` when you intentionally want fire-and-forget behavior.

By default, agent-triggered execution uses `agent-repl.executionMode = "no-yank"`, which prefers the background Jupyter execution path on an already-open notebook with a live kernel. That keeps the running cell and outputs visible without intentionally stealing focus. If you want the original VS Code notebook execution behavior instead, set `agent-repl.executionMode` to `native`.

To execute inline code that also inserts a cell:

```bash
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
```

To execute an existing cell by ID:

```bash
agent-repl exec demo.ipynb --cell-id a1b2c3
```

Completed execution responses include `execution_mode` and `execution_preference`, so you can see whether the run used the background no-yank path or the native notebook command path.

## Read the Notebook

```bash
agent-repl cat demo.ipynb
```

```json
{
  "path": "demo.ipynb",
  "cells": [
    {
      "index": 0,
      "cell_id": "a1b2c3",
      "cell_type": "code",
      "source": "import math; print(math.pi)",
      "execution_count": 1,
      "outputs": [{"output_type": "stream", "name": "stdout", "text": "3.141592653589793\n"}]
    }
  ]
}
```

Use `--no-outputs` for sources only:

```bash
agent-repl cat demo.ipynb --no-outputs
```

If the notebook was still closed when you first read it, `cat` may have emitted fallback IDs like `index-1`. Once the notebook is live/open, re-run `cat --no-outputs` and switch to the real UUIDs before using `--cell-id`.

## Edit Cells

Replace a cell's source:

```bash
agent-repl edit demo.ipynb replace-source --cell-id a1b2c3 -s 'area = math.pi * 5 ** 2
print(f"Area: {area:.2f}")'
```

Insert a new cell:

```bash
agent-repl edit demo.ipynb insert --at-index 0 --cell-type code -s 'import math'
```

Other edit operations: `delete`, `move`, `clear-outputs`.
After editing code, re-run that cell and confirm that its outputs now match the new source.

## Check Status

See what the kernel is doing:

```bash
agent-repl status demo.ipynb
```

Returns kernel state (idle/busy), running cells, and queued cells.

## The Prompt Loop

Humans create prompt cells in VS Code using the "Ask Agent" toolbar button. Agents discover and respond:

```bash
agent-repl prompts demo.ipynb
```

```json
{
  "prompts": [
    {
      "cell_id": "d4e5f6",
      "cell_type": "markdown",
      "source": "calculate the circumference too",
      "metadata": {
        "custom": {
          "agent-repl": {"cell_id": "d4e5f6", "type": "prompt", "status": "pending"}
        }
      }
    }
  ]
}
```

Respond to the prompt:

```bash
agent-repl respond demo.ipynb --to d4e5f6 -s 'circ = 2 * math.pi * 5; print(f"Circumference: {circ:.2f}")'
```

The response cell is inserted after the prompt, executed, and the prompt is marked as answered.

## Verify with Run-All

Re-execute every cell to confirm reproducibility:

```bash
agent-repl restart-run-all demo.ipynb
```

## Next Steps

- [Command Reference](commands.md) — Full reference for all 14 commands
- [Prompt Loop](prompt-loop.md) — Deep dive into the conversation pattern
- [Architecture](architecture.md) — How the bridge works

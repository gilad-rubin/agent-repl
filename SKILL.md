---
name: agent-repl
description: Work against a live Jupyter notebook via the VS Code bridge. Use this when an agent needs to read, edit, or execute notebook cells while Cursor/VS Code is open.
---

# agent-repl

CLI for AI agents to work with Jupyter notebooks via the VS Code extension bridge. The notebook in Cursor/VS Code is the human-facing surface; the CLI is the agent-facing surface.

## Prerequisites

The VS Code/Cursor extension must be running. It auto-starts when the editor opens and writes a connection file to `~/Library/Jupyter/runtime/`. The CLI discovers it automatically.

## Installation

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl

# Direct invocation without install
uv run agent-repl <command>
```

If `agent-repl` is already on `$PATH`, skip installation — run commands directly.

## Quick Start

```bash
# Create a notebook and start working
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
agent-repl cat analysis.ipynb
```

By default, agent-triggered execution prefers `no-yank` behavior: it keeps notebook execution visible without intentionally stealing focus from whatever the human is editing. If you want the old VS Code execution behavior, set `agent-repl.executionMode` to `native`.

## Core Loop

### Read
```bash
agent-repl cat demo.ipynb                    # cell sources + outputs (cleaned)
agent-repl cat demo.ipynb --no-outputs       # sources only
agent-repl status demo.ipynb                 # kernel state, running/queued cells
```

### Create
```bash
agent-repl new analysis.ipynb                    # auto-selects .venv kernel if present
agent-repl new analysis.ipynb --kernel <id>      # select a specific kernel by ID
agent-repl new analysis.ipynb --cells-json '[{"type":"code","source":"x=1"}]'
```

**Kernel auto-selection:** When creating a notebook, agent-repl checks for a `.venv` directory in the workspace. If found, it asks the Jupyter extension to open the notebook against that environment so the response can include `"kernel_status": "selected"` and the agent can run code immediately. If no `.venv` exists and no `--kernel` is provided, the response includes `"kernel_status": "needs_selection"` — use `agent-repl kernels` to list options and `agent-repl select-kernel` to choose one.

**Focus caveat:** Creating a brand-new notebook or forcing the first kernel attach may still briefly reveal the notebook because that path goes through Jupyter's kernel-selection/startup flow. After the kernel is already attached, execution can usually stay on the no-yank path.

### Execute
```bash
# Insert + execute (returns immediately with cell_id)
agent-repl ix demo.ipynb -s 'import pandas as pd'
agent-repl ix demo.ipynb --source-file /tmp/cell.py

# Execute existing cell by ID
agent-repl exec demo.ipynb --cell-id <cell_id>

# Execute inline code (inserts + runs)
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
```

On an already-open notebook with a live kernel, `ix` and `exec` now default to the no-yank execution path. Completed execution responses include `execution_mode` and `execution_preference` so you can tell whether the background path ran or the command fell back to VS Code's native notebook command.

### Edit
```bash
agent-repl edit demo.ipynb replace-source --cell-id abc -s 'x = 2'
agent-repl edit demo.ipynb insert --at-index 1 --cell-type code -s 'print("hello")'
agent-repl edit demo.ipynb delete --cell-id abc
agent-repl edit demo.ipynb move --cell-id abc --to-index 0
agent-repl edit demo.ipynb clear-outputs --all
```

### Lifecycle
```bash
agent-repl run-all demo.ipynb
agent-repl restart demo.ipynb
agent-repl restart-run-all demo.ipynb
```

## Prompts

Humans write prompt cells in the editor, agents discover and respond via CLI.

### Agent discovers and responds
```bash
agent-repl prompts demo.ipynb                          # list prompt cells
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
```

## Extension Management

```bash
agent-repl reload      # hot-reload extension routes (no restart needed)
```

## Command Reference

| Command | Description |
|---------|-------------|
| `cat` | Read notebook contents (cleaned for agent consumption) |
| `status` | Kernel state + running/queued cells with owner (human/agent) |
| `exec` | Execute a cell by `--cell-id` or inline `-c`/`--code` |
| `ix` | Insert and execute code (`-s SOURCE`, `--source-file`, or stdin) |
| `edit` | Cell editing: `replace-source`, `insert`, `delete`, `move`, `clear-outputs` |
| `run-all` | Execute all cells |
| `restart` | Restart kernel |
| `restart-run-all` | Restart kernel then run all |
| `new` | Create a new notebook (auto-selects `.venv` kernel if present; `--kernel ID` for explicit) |
| `kernels` | List available notebook kernels |
| `select-kernel` | Select kernel for a notebook (`--kernel-id ID` for programmatic, omit for interactive picker) |
| `prompts` | List prompt cells |
| `respond` | Answer a prompt (`--to CELL_ID`) — auto-updates prompt status |
| `reload` | Hot-reload extension routes |

All commands output JSON. Pass `--pretty` for formatted output.

## Resources

- CLI source: `src/agent_repl/`
- Extension source: `extension/`
- Tests: `tests/test_agent_repl.py`

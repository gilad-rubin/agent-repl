# agent-repl

CLI for AI agents to work with Jupyter notebooks via the VS Code extension bridge. The notebook in VS Code/Cursor is the human-facing surface; the CLI is the agent-facing surface. Both share the same kernel and file.

## Prerequisites

The VS Code/Cursor extension must be running. It auto-starts when the editor opens a `.ipynb` file and writes a connection file to `~/Library/Jupyter/runtime/`. The CLI discovers it automatically.

## Installation

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl

# Direct invocation without install
uv run agent-repl <command>
```

If `agent-repl` is already on `$PATH`, skip installation.

## Quick Start

```bash
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
agent-repl cat analysis.ipynb
```

## Commands

### Read

```bash
agent-repl cat demo.ipynb                    # cell sources + outputs (cleaned for agents)
agent-repl cat demo.ipynb --no-outputs       # sources only
agent-repl status demo.ipynb                 # kernel state, running/queued cells
```

### Create

```bash
agent-repl new analysis.ipynb
agent-repl new analysis.ipynb --cells-json '[{"type":"code","source":"x=1"}]'
```

### Execute

```bash
# Insert + execute (fire-and-forget, returns cell_id immediately)
agent-repl ix demo.ipynb -s 'import pandas as pd'
agent-repl ix demo.ipynb --source-file /tmp/cell.py
echo 'print("hello")' | agent-repl ix demo.ipynb

# Execute existing cell by ID
agent-repl exec demo.ipynb --cell-id <cell_id>

# Execute inline code (inserts + runs)
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
```

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

### Prompts

Humans create prompt cells in the editor (via the "Ask Agent" toolbar button). Agents discover and respond via CLI.

```bash
agent-repl prompts demo.ipynb                          # list prompt cells
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
```

The `respond` command atomically: marks the prompt in-progress, inserts a response cell, executes it, then marks the prompt answered.

### Extension Management

```bash
agent-repl reload      # hot-reload extension routes (no restart needed)
```

## Command Reference

| Command | Description |
|---------|-------------|
| `cat` | Read notebook contents (cleaned for agent consumption) |
| `status` | Kernel state + running/queued cells |
| `exec` | Execute a cell by `--cell-id` or inline `-c`/`--code` |
| `ix` | Insert and execute code (`-s SOURCE`, `--source-file`, or stdin). Returns immediately with `cell_id` |
| `edit` | Cell editing: `replace-source`, `insert`, `delete`, `move`, `clear-outputs` |
| `run-all` | Execute all cells |
| `restart` | Restart kernel |
| `restart-run-all` | Restart kernel then run all |
| `new` | Create a new notebook |
| `prompts` | List prompt cells (cells with `agent-repl` prompt metadata) |
| `respond` | Answer a prompt (`--to CELL_ID`) — auto-updates prompt status |
| `reload` | Hot-reload extension routes without restarting the bridge |

All commands output JSON. Pass `--pretty` for formatted output.

## Source Input

Commands that accept source code (`ix`, `respond`, `edit replace-source`, `edit insert`) support three input methods:

1. **Inline**: `-s 'code here'`
2. **File**: `--source-file path`
3. **Stdin**: pipe content when neither flag is provided

## Cell Identification

Commands that target a specific cell accept either:
- `--cell-id ID` — cell by its stable UUID (preferred, survives moves/deletes)
- `-i INDEX` / `--index INDEX` — cell by position (0-based)

## Architecture

```
VS Code / Cursor (human interface)
    |
VS Code Extension (bridge server on localhost)
    |  HTTP + bearer token auth
CLI (agent interface)
```

- Extension writes connection file: `~/Library/Jupyter/runtime/agent-repl-bridge-<pid>.json`
- CLI auto-discovers the bridge by scanning connection files and pinging health
- All notebook operations go through the extension's API

## Source

- CLI: `src/agent_repl/` (Python, ~640 lines)
- Extension: `extension/src/` (TypeScript, ~1200 lines)
- Tests: `tests/test_agent_repl.py`

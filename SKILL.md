---
name: agent-repl
description: Work against a live local Jupyter notebook kernel. Use this when an agent needs a Jupyter-like in-memory REPL, wants to inspect or edit a notebook while the kernel stays alive, or needs an explicit verification pass.
---

# agent-repl

CLI tool giving AI agents direct access to live Jupyter notebook kernels. The notebook in JupyterLab is the human-facing surface; the CLI is the agent-facing surface.

## Installation

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl

# Dev dependency in another project
uv add --dev agent-repl --path /path/to/agent-repl

# Direct invocation without install
uv run /path/to/agent-repl/scripts/agent_repl.py --help
```

## When Already Installed

If `agent-repl` is already on `$PATH` (via `uv tool install`), skip installation. Just run commands directly — no `uv run` prefix needed.

Server auto-selects when only one is running — no flags needed. Use `-p PORT` only when multiple servers are running. If JupyterLab is already running, jump straight to `agent-repl servers` to confirm, then start working.

## Quick Start

```bash
# Launch JupyterLab (no-auth, background)
agent-repl start

# Or if JupyterLab is already running (tokens are read automatically)
agent-repl servers

# Create a notebook and start working
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
agent-repl cat analysis.ipynb
```

Set `AGENT_REPL_PORT=8899` to avoid passing `-p` every time. If only one server is running, it's auto-selected.

## Core Loop

### Discover
```bash
agent-repl servers                    # list all Jupyter servers
agent-repl ls                         # list live notebooks (alias for notebooks)
agent-repl kernels                    # list kernelspecs + running kernels
```

### Read
```bash
agent-repl cat demo.ipynb                              # brief view (default)
agent-repl cat demo.ipynb --detail minimal             # one-liner per cell
agent-repl cat demo.ipynb --detail full                # full source + outputs
agent-repl cat demo.ipynb --cells 0-2,5,8-             # flexible ranges
agent-repl cat demo.ipynb --cell-type code             # filter by type
agent-repl cat demo.ipynb --detail full --raw-output   # full MIME bundles
```

### Create
```bash
agent-repl new analysis.ipynb                          # auto-starts kernel
agent-repl new analysis.ipynb --kernel-name julia-1.9
agent-repl new analysis.ipynb --no-start-kernel
```

### Execute
```bash
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
agent-repl exec demo.ipynb -c 'train_model()' --stream    # real-time JSONL output
agent-repl ix demo.ipynb -s 'import pandas as pd'         # insert + execute
agent-repl ix demo.ipynb --at-index 3 -s 'x = 1'
```

### Edit
```bash
agent-repl edit demo.ipynb replace-source --cell-id abc -s 'x = 2'
agent-repl edit demo.ipynb insert --at-index 1 -t code -s 'print("hello")'
agent-repl edit demo.ipynb delete --cell-id abc
agent-repl edit demo.ipynb move --cell-id abc --to-index 0
agent-repl edit demo.ipynb clear-outputs --all
agent-repl edit demo.ipynb batch --operations '[
  {"op":"insert","at_index":-1,"cell_type":"code","source":"x=1"},
  {"op":"replace-source","cell_id":"abc","source":"x=2"}
]'
```

### Variables
```bash
agent-repl vars demo.ipynb list
agent-repl vars demo.ipynb preview --name df
```

### Verify
```bash
agent-repl run-all demo.ipynb --save-outputs
agent-repl run-all demo.ipynb --skip-tags setup,expensive
agent-repl run-all demo.ipynb --only-tags critical
agent-repl restart-run-all demo.ipynb --save-outputs
```

## Notebook-as-Conversation (Prompting from Cells)

Humans write prompt cells in JupyterLab, agents discover and respond via CLI.

### Human writes in JupyterLab
```python
#| agent: clean this dataframe — drop nulls, normalize column names
df = pd.read_csv("sales.csv")
df.head()
```

Or in markdown:
```markdown
<!-- agent: create a visualization of sales by region -->
```

### Agent discovers and responds
```bash
agent-repl prompts demo.ipynb                          # list pending prompts
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
agent-repl watch demo.ipynb                            # poll for new prompts (JSONL)
agent-repl watch demo.ipynb --once                     # check once and exit
```

### Cell Directives
```python
#| agent: <instruction>           # prompt the agent
#| agent-tags: critical, setup    # tag cells for filtering
#| agent-skip                     # skip in run-all
```

## Execution Context

Get a full snapshot of kernel + notebook state in one call:
```bash
agent-repl context demo.ipynb
```
Returns: cells (brief), variables, kernel state, pending prompts.

## Streaming Execution

Real-time output for long-running cells:
```bash
agent-repl exec demo.ipynb -c 'train_model()' --stream
```
Output (JSONL, one event per line):
```jsonl
{"elapsed":2.1,"name":"stdout","text":"Epoch 1: loss=0.45\n","type":"stream"}
{"elapsed":21.0,"data":{"text/plain":"<accuracy=0.94>"},"type":"execute_result"}
```

## Git-Friendly Notebooks

```bash
agent-repl clean demo.ipynb           # strip outputs for clean diffs (stdout)
agent-repl git-setup                  # configure .gitattributes + git filter
```

## Output Filtering

CLI strips rich media by default. Notebook file always keeps full outputs.

- **Default**: HTML dropped when text/plain exists, images → `[image: image/png]`, widgets → `[widget]`
- **`--raw-output`**: disable stripping
- **On save**: ANSI codes stripped, repr addresses cleaned, Colab metadata removed

## Command Reference

| Command | Alias | Description |
|---------|-------|-------------|
| `servers` | | List Jupyter servers |
| `notebooks` | `ls` | List live notebooks |
| `contents` | `cat` | Read notebook contents |
| `execute` | `exec` | Execute code |
| `insert-execute` | `ix` | Insert cell + execute |
| `edit` | | Edit cells (replace-source, insert, delete, move, clear-outputs, batch) |
| `new` | | Create notebook |
| `kernels` | | List kernelspecs |
| `variables` | `vars` | Inspect variables |
| `run-all` | | Execute all cells |
| `restart-run-all` | | Restart + run all |
| `restart` | | Restart kernel |
| `start` | | Launch JupyterLab |
| `prompts` | | List agent prompt cells |
| `respond` | | Respond to a prompt |
| `watch` | | Poll for new prompts |
| `context` | | Snapshot kernel state |
| `clean` | | Strip outputs for git |
| `git-setup` | | Configure git filters |

## Timeouts

Execution commands (`exec`, `ix`, `run-all`, etc.) have **no timeout by default** — cells run until they finish. To set an explicit timeout, pass `--timeout SECONDS`. Non-execution commands (like `cat`, `ls`) have a 10s default.

## Target Selection

1. Server: `-p PORT` (auto-selects if only one server is running)
2. Notebook: positional path argument
3. Session/Kernel: `--session-id` or `--kernel-id` (when needed)

## Resources

- Package: `src/agent_repl/`
- Docs: `docs/`
- Tests: `tests/test_agent_repl.py`

# Getting Started

Build and run a complete notebook workflow from the command line -- install through git-clean in 10 minutes.

## Install

```bash
uv tool install /path/to/agent-repl
agent-repl --help
```

```
usage: agent-repl [-h] {servers,notebooks,ls,contents,cat,...} ...
```

See [Installation](installation.md) for alternative install methods.

## Start JupyterLab

```bash
agent-repl start
```

```json
{"pid": 42710, "command": "jupyter lab --IdentityProvider.token='' --ServerApp.password='' --no-browser"}
```

JupyterLab runs in the background with auth disabled. Open `http://localhost:8888/lab` to watch the notebook update in real time.

## Discover the Server

```bash
agent-repl servers
```

```json
{"servers": [{"url": "http://localhost:8888/", "pid": 42710, "notebook_dir": "/Users/you/project"}]}
```

When only one server is running, all commands auto-select it. Set `AGENT_REPL_PORT=8899` if you run multiple servers.

## Create a Notebook

```bash
agent-repl new demo.ipynb
```

```json
{"operation": "new", "path": "demo.ipynb", "kernel_name": "python3", "cell_count": 2, "session": {"id": "...", "kernel": {"id": "..."}}}
```

Creates `demo.ipynb` with a title cell and an empty code cell, starts a Python kernel. The notebook appears in JupyterLab immediately.

## Execute Code

Run code in the kernel without touching the notebook file:

```bash
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
```

```json
{"status": "ok", "events": [{"type": "stream", "name": "stdout", "text": "42\n"}]}
```

`exec` is for throwaway evaluation. To build the notebook, use `ix`:

```bash
agent-repl ix demo.ipynb -s 'import math; print(math.pi)'
```

```json
{"operation": "insert-execute", "insert": {"cell_id": "a1b2c3", "cell_count": 3}, "execute": {"status": "ok", "events": [{"type": "stream", "name": "stdout", "text": "3.141592653589793\n"}]}}
```

`ix` (insert-execute) adds a cell AND runs it. The cell appears in JupyterLab with its output. Insert at a specific position with `--at-index 1`.

## Read the Notebook

```bash
agent-repl cat demo.ipynb
```

```json
{"path": "demo.ipynb", "cells": [
  {"index": 0, "cell_type": "markdown", "source_preview": "# demo"},
  {"index": 1, "cell_type": "code", "source_preview": ""},
  {"index": 2, "cell_type": "code", "source_preview": "import math; print(math.pi)"}
]}
```

Default `brief` detail shows first 3 lines per cell. Use `--detail full` for outputs, or `--cells 0,2` to select specific cells.

## Edit Cells

Replace a cell's source by ID (from `cat` output):

```bash
agent-repl edit demo.ipynb replace-source --cell-id a1b2c3 -s 'area = math.pi * 5 ** 2
print(f"Area: {area:.2f}")'
```

```json
{"changed": true, "cell": {"index": 2, "cell_id": "a1b2c3", "cell_type": "code"}}
```

Other edit operations: `insert`, `delete`, `move`, `clear-outputs`, `batch`.

## Inspect Variables

```bash
agent-repl vars demo.ipynb list
```

```json
{"variables": [
  {"name": "area", "type": "float", "module": "builtins"},
  {"name": "x", "type": "int", "module": "builtins"}
]}
```

Preview a specific variable:

```bash
agent-repl vars demo.ipynb preview --name area
```

```json
{"variable": {"name": "area", "type": "float", "preview": 78.53981633974483}}
```

## Try the Prompt Loop

Humans write instructions in JupyterLab that agents discover via CLI. In JupyterLab, add a cell with a `#| agent:` directive:

```python
#| agent: calculate the circumference too
radius = 5
```

Discover pending prompts:

```bash
agent-repl prompts demo.ipynb
```

```json
{"prompts": [{"cell_id": "d4e5f6", "index": 3, "instruction": "calculate the circumference too", "status": "pending"}]}
```

Respond:

```bash
agent-repl respond demo.ipynb --to d4e5f6 -s 'circ = 2 * math.pi * radius; print(f"Circumference: {circ:.2f}")'
```

The response cell is inserted after the prompt, executed, and linked via metadata. Markdown cells use `<!-- agent: ... -->` instead.

## Verify with Run-All

Re-execute every cell from a fresh kernel to confirm reproducibility:

```bash
agent-repl restart-run-all demo.ipynb --save-outputs
```

```json
{"operation": "restart-run-all", "run_all": {"status": "ok", "executed_cell_count": 3, "skipped_cell_count": 0, "outputs_saved": true}}
```

If any cell errors, the output shows which cell failed and why.

## Clean for Git

Strip outputs so notebooks produce clean diffs:

```bash
agent-repl clean demo.ipynb > demo-clean.ipynb
```

For automatic cleaning on every `git add`:

```bash
agent-repl git-setup
```

```json
{"gitattributes": ".gitattributes", "git_config_clean": "git config filter.agent-repl-clean.clean 'agent-repl clean %f'", "git_config_smudge": "git config filter.agent-repl-clean.smudge cat"}
```

Run the printed `git config` commands to activate the filter. After that, `git diff` only shows source changes.

## Next Steps

- [Output filtering](output-filtering.md) -- How agent-repl strips rich media for CLI readability
- [Command reference](index.md) -- Full command list with descriptions

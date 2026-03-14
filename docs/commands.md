# Command Reference

Complete reference for every `agent-repl` command. All commands return JSON (compact by default, formatted with `--pretty`).

**Server selection** applies to most commands: use `-p PORT` or set `AGENT_REPL_PORT`. If only one server is running, it auto-selects.

---

## Discovery

### servers

List discovered running Jupyter servers.

```
agent-repl servers [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--timeout` | `float` | `10.0` | HTTP request timeout in seconds |
| `--pretty` | flag | off | Pretty-print JSON output |

```bash
agent-repl servers
```

```json
[{"base_url": "/", "pid": 42100, "port": 8888, "url": "http://localhost:8888/", "version": "2.0.0"}]
```

---

### notebooks / ls

List live notebook sessions on a server.

```
agent-repl notebooks [--server-url URL] [-p PORT] [--timeout FLOAT] [--pretty]
agent-repl ls ...
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--server-url` | `str` | | Full server URL (alternative to port) |
| `--timeout` | `float` | `10.0` | HTTP request timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl ls -p 8888
```

```json
[{"kernel_id": "abc-123", "name": "analysis.ipynb", "path": "analysis.ipynb", "session_id": "s-456"}]
```

---

### kernels

List available kernelspecs and currently running kernels.

```
agent-repl kernels [--server-url URL] [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--server-url` | `str` | | Full server URL |
| `--timeout` | `float` | `10.0` | HTTP request timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl kernels -p 8888
```

```json
{"kernelspecs": {"python3": {"display_name": "Python 3", "language": "python"}}, "running": [{"id": "abc-123", "name": "python3"}]}
```

---

## Reading

### contents / cat

Fetch saved notebook contents with flexible filtering and detail levels.

```
agent-repl contents PATH [--cells RANGES] [--cell-type TYPE] [--detail LEVEL]
                         [--include-outputs] [--raw] [--raw-output]
                         [-p PORT] [--timeout FLOAT] [--pretty]
agent-repl cat ...
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--cells` | `str` | all | Cell index ranges: `0,2,5` or `0-2,4,7-` |
| `--cell-type` | `code\|markdown\|raw` | all | Filter cells by type |
| `--detail` | `minimal\|brief\|full` | `brief` | Output detail level |
| `--include-outputs` | flag | off | Alias for `--detail full` |
| `--raw` | flag | off | Return raw notebook JSON |
| `--raw-output` | flag | off | Disable media stripping (show full MIME bundles) |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `10.0` | HTTP request timeout |
| `--pretty` | flag | off | Pretty-print JSON |

**Detail levels**:
- `minimal` -- one-liner per cell (index, type, first line)
- `brief` -- source code, no outputs (default)
- `full` -- full source and outputs

```bash
agent-repl cat demo.ipynb --detail full --cells 0-2
```

```json
{"cells": [{"cell_type": "code", "index": 0, "outputs": [{"text": "42\n", "type": "stream"}], "source": "x = 42\nprint(x)"}], "path": "demo.ipynb"}
```

---

## Creating

### new

Create a new notebook on the server. Auto-starts a kernel by default.

```
agent-repl new PATH [--kernel-name NAME] [--cells JSON] [--cells-file FILE]
                     [--no-start-kernel] [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path to create |
| `--kernel-name` | `str` | `python3` | Kernelspec to use |
| `--cells` | `str` | | JSON array of `{"type": "code", "source": "..."}` |
| `--cells-file` | `str` | | Path to JSON file with cell definitions |
| `--no-start-kernel` | flag | off | Create file only, don't start a kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `10.0` | HTTP request timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl new analysis.ipynb --kernel-name python3
```

```json
{"kernel_id": "abc-123", "path": "analysis.ipynb", "session_id": "s-456"}
```

---

## Executing

### execute / exec

Execute code in a live notebook kernel. Optionally save outputs to the notebook file.

```
agent-repl execute PATH -c CODE [--code-file FILE] [--cell-id ID]
                        [--save-outputs] [--no-save-outputs] [--stream]
                        [--transport MODE] [--raw-output]
                        [--session-id ID] [--kernel-id ID]
                        [-p PORT] [--timeout FLOAT] [--pretty]
agent-repl exec ...
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `-c`, `--code` | `str` | | Code to execute (or use `--code-file` or stdin) |
| `--code-file` | `str` | | File containing code to execute |
| `--cell-id` | `str` | | Execute an existing cell by its ID |
| `--save-outputs` | flag | off | Write outputs back to the notebook file |
| `--no-save-outputs` | flag | off | Suppress saving outputs |
| `--stream` | flag | off | Output events as JSONL in real-time |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--raw-output` | flag | off | Disable media stripping |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
```

```json
{"outputs": [{"name": "stdout", "text": "42\n", "type": "stream"}], "status": "ok"}
```

With `--stream`, output arrives as JSONL (one event per line):

```bash
agent-repl exec demo.ipynb -c 'train_model()' --stream
```

```jsonl
{"elapsed":2.1,"name":"stdout","text":"Epoch 1: loss=0.45\n","type":"stream"}
{"elapsed":21.0,"data":{"text/plain":"<accuracy=0.94>"},"type":"execute_result"}
```

---

### insert-execute / ix

Insert a new cell into the notebook and execute it in one step.

```
agent-repl insert-execute PATH -s SOURCE [--source-file FILE]
                               [--at-index INT] [-t TYPE]
                               [--transport MODE] [--raw-output]
                               [--session-id ID] [--kernel-id ID]
                               [-p PORT] [--timeout FLOAT] [--pretty]
agent-repl ix ...
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `-s`, `--source` | `str` | | Cell source code (or use `--source-file` or stdin) |
| `--source-file` | `str` | | File containing cell source |
| `--at-index` | `int` | `-1` (end) | Position to insert at (-1 appends) |
| `-t`, `--cell-type` | `code\|markdown\|raw` | `code` | Type of cell to insert |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--raw-output` | flag | off | Disable media stripping |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl ix demo.ipynb -s 'import pandas as pd; print(pd.__version__)'
```

```json
{"cell_id": "new-abc", "index": 3, "outputs": [{"name": "stdout", "text": "2.1.4\n", "type": "stream"}], "status": "ok"}
```

---

### run-all

Execute all code cells in the notebook sequentially. Use tag filters to control which cells run.

```
agent-repl run-all PATH [--save-outputs] [--skip-tags TAGS] [--only-tags TAGS]
                        [--transport MODE] [--raw-output]
                        [--session-id ID] [--kernel-id ID]
                        [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--save-outputs` | flag | off | Write outputs back to the notebook file |
| `--skip-tags` | `str` | | Skip cells with these tags (comma-separated) |
| `--only-tags` | `str` | | Only run cells with these tags (comma-separated) |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--raw-output` | flag | off | Disable media stripping |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Per-cell execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl run-all demo.ipynb --save-outputs --skip-tags setup,expensive
```

```json
{"cells_run": 5, "cells_skipped": 2, "errors": [], "status": "ok"}
```

---

### restart-run-all

Restart the kernel, then execute all code cells. Gives a clean-slate verification run.

```
agent-repl restart-run-all PATH [--save-outputs] [--skip-tags TAGS] [--only-tags TAGS]
                                [--transport MODE] [--raw-output]
                                [--session-id ID] [--kernel-id ID]
                                [-p PORT] [--timeout FLOAT] [--pretty]
```

Flags are identical to [`run-all`](#run-all).

```bash
agent-repl restart-run-all demo.ipynb --save-outputs
```

```json
{"cells_run": 7, "cells_skipped": 0, "errors": [], "restarted": true, "status": "ok"}
```

---

### restart

Restart a live notebook kernel without running any cells.

```
agent-repl restart PATH [--session-id ID] [--kernel-id ID]
                        [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Timeout for restart |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl restart demo.ipynb
```

```json
{"kernel_id": "abc-123", "status": "ok"}
```

---

## Editing

### edit replace-source

Replace the source of an existing cell.

```
agent-repl edit PATH replace-source (-i INDEX | --cell-id ID) -s SOURCE [--source-file FILE] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `-i`, `--index` | `int` | | Cell index (mutually exclusive with `--cell-id`) |
| `--cell-id` | `str` | | Cell ID (mutually exclusive with `-i`) |
| `-s`, `--source` | `str` | | New cell source (or use `--source-file` or stdin) |
| `--source-file` | `str` | | File containing new source |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl edit demo.ipynb replace-source -i 0 -s 'x = 99'
```

```json
{"cell_id": "abc-123", "index": 0, "status": "ok"}
```

---

### edit insert

Insert a new cell at a specified position.

```
agent-repl edit PATH insert (--at-index INT | --before INT | --after INT)
                            -t TYPE -s SOURCE [--source-file FILE] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--at-index` | `int` | | Insert at this index (mutually exclusive) |
| `--before` | `int` | | Insert before this cell index (mutually exclusive) |
| `--after` | `int` | | Insert after this cell index (mutually exclusive) |
| `-t`, `--cell-type` | `code\|markdown\|raw` | required | Type of cell to insert |
| `-s`, `--source` | `str` | | Cell source (or use `--source-file` or stdin) |
| `--source-file` | `str` | | File containing cell source |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl edit demo.ipynb insert --at-index 1 -t code -s 'print("hello")'
```

```json
{"cell_id": "new-abc", "index": 1, "status": "ok"}
```

---

### edit delete

Delete a cell from the notebook.

```
agent-repl edit PATH delete (-i INDEX | --cell-id ID) [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `-i`, `--index` | `int` | | Cell index (mutually exclusive with `--cell-id`) |
| `--cell-id` | `str` | | Cell ID (mutually exclusive with `-i`) |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl edit demo.ipynb delete --cell-id abc-123
```

```json
{"cell_id": "abc-123", "status": "ok"}
```

---

### edit move

Move a cell to a different position in the notebook.

```
agent-repl edit PATH move (-i INDEX | --cell-id ID) --to-index INT [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `-i`, `--index` | `int` | | Cell to move (mutually exclusive with `--cell-id`) |
| `--cell-id` | `str` | | Cell to move (mutually exclusive with `-i`) |
| `--to-index` | `int` | required | Destination index |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl edit demo.ipynb move --cell-id abc-123 --to-index 0
```

```json
{"cell_id": "abc-123", "new_index": 0, "status": "ok"}
```

---

### edit clear-outputs

Clear outputs from one or all cells.

```
agent-repl edit PATH clear-outputs [--all | --index INT | --cell-id ID] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--all` | flag | off | Clear outputs from all cells |
| `--index` | `int` | | Clear outputs from cell at this index |
| `--cell-id` | `str` | | Clear outputs from cell with this ID |
| `--pretty` | flag | off | Pretty-print JSON |

If none of `--all`, `--index`, or `--cell-id` is specified, defaults to clearing all.

```bash
agent-repl edit demo.ipynb clear-outputs --all
```

```json
{"cells_cleared": 5, "status": "ok"}
```

---

### edit batch

Apply multiple edit operations atomically in a single call.

```
agent-repl edit PATH batch --operations JSON [--operations-file FILE] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--operations` | `str` | | JSON array of operations (or use `--operations-file` or stdin) |
| `--operations-file` | `str` | | File containing JSON operations array |
| `--pretty` | flag | off | Pretty-print JSON |

Operations are JSON objects with an `"op"` field matching the edit subcommand names, plus the same fields as the corresponding subcommand flags.

```bash
agent-repl edit demo.ipynb batch --operations '[
  {"op": "insert", "at_index": -1, "cell_type": "code", "source": "x = 1"},
  {"op": "replace-source", "cell_id": "abc-123", "source": "x = 2"}
]'
```

```json
{"operations_applied": 2, "status": "ok"}
```

---

## Variables

### variables list / vars list

List live variables in the kernel's namespace.

```
agent-repl variables PATH list [--limit INT] [--include-private] [--include-callables]
                               [--transport MODE] [--session-id ID] [--kernel-id ID]
                               [-p PORT] [--timeout FLOAT] [--pretty]
agent-repl vars PATH list ...
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--limit` | `int` | `25` | Maximum number of variables to return |
| `--include-private` | flag | off | Include `_`-prefixed variables |
| `--include-callables` | flag | off | Include functions and classes |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl vars demo.ipynb list --limit 10
```

```json
{"variables": [{"name": "df", "shape": "(100, 5)", "type": "DataFrame"}, {"name": "x", "type": "int", "value": "42"}]}
```

---

### variables preview / vars preview

Preview the value of a single live variable.

```
agent-repl variables PATH preview --name NAME [--max-chars INT]
                                  [--transport MODE] [--session-id ID] [--kernel-id ID]
                                  [-p PORT] [--timeout FLOAT] [--pretty]
agent-repl vars PATH preview ...
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--name` | `str` | required | Variable name to preview |
| `--max-chars` | `int` | `400` | Truncate preview to this many characters |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl vars demo.ipynb preview --name df
```

```json
{"name": "df", "preview": "   col_a  col_b\n0      1     10\n1      2     20\n...", "type": "DataFrame"}
```

---

## Verification

### context

Get a full snapshot of kernel and notebook state in one call. Returns cells (brief), variables, kernel state, and pending prompts.

```
agent-repl context PATH [--include-outputs] [--transport MODE]
                        [--session-id ID] [--kernel-id ID]
                        [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--include-outputs` | flag | off | Include cell outputs in the snapshot |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl context demo.ipynb --pretty
```

```json
{
  "cells": [{"cell_type": "code", "index": 0, "source": "x = 42"}],
  "kernel": {"status": "idle"},
  "prompts": [],
  "variables": [{"name": "x", "type": "int", "value": "42"}]
}
```

---

## Prompt Loop

Commands for the notebook-as-conversation workflow. Humans write prompt cells in JupyterLab using `#| agent: <instruction>` directives; agents discover and respond via CLI.

### prompts

List agent prompt cells in a notebook.

```
agent-repl prompts PATH [--all] [--context INT]
                        [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--all` | flag | off | Include already-answered prompts |
| `--context` | `int` | `1` | Number of surrounding cells to include for context |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `10.0` | HTTP request timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl prompts demo.ipynb
```

```json
[{"cell_id": "abc-123", "context_after": [], "context_before": [{"index": 2, "source": "df.head()"}], "index": 3, "instruction": "clean this dataframe", "source": "#| agent: clean this dataframe\ndf = pd.read_csv('sales.csv')"}]
```

---

### respond

Respond to an agent prompt cell by inserting and executing a response cell after it.

```
agent-repl respond PATH --to CELL_ID -s SOURCE [--source-file FILE]
                        [-t TYPE] [--transport MODE] [--raw-output]
                        [--session-id ID] [--kernel-id ID]
                        [-p PORT] [--timeout FLOAT] [--pretty]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--to` | `str` | required | Cell ID of the prompt to respond to |
| `-s`, `--source` | `str` | | Response code (or use `--source-file` or stdin) |
| `--source-file` | `str` | | File containing response code |
| `-t`, `--cell-type` | `code\|markdown\|raw` | `code` | Type of response cell |
| `--transport` | `auto\|websocket\|zmq` | `auto` | Kernel communication transport |
| `--raw-output` | flag | off | Disable media stripping |
| `--session-id` | `str` | | Target a specific session |
| `--kernel-id` | `str` | | Target a specific kernel |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `30.0` | Execution timeout |
| `--pretty` | flag | off | Pretty-print JSON |

```bash
agent-repl respond demo.ipynb --to abc-123 -s 'df.dropna(inplace=True); print(df.shape)'
```

```json
{"cell_id": "resp-456", "outputs": [{"name": "stdout", "text": "(95, 5)\n", "type": "stream"}], "status": "ok"}
```

---

### watch

Poll a notebook for new agent prompts. Outputs JSONL, one event per new prompt.

```
agent-repl watch PATH [--interval FLOAT] [--once] [--context INT]
                      [-p PORT] [--timeout FLOAT]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `--interval` | `float` | `2.0` | Poll interval in seconds |
| `--once` | flag | off | Check once and exit |
| `--context` | `int` | `1` | Number of surrounding cells to include |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `10.0` | HTTP request timeout |

```bash
agent-repl watch demo.ipynb --once
```

```jsonl
{"cell_id":"abc-123","index":3,"instruction":"clean this dataframe","source":"#| agent: clean this dataframe\ndf = pd.read_csv('sales.csv')"}
```

Runs continuously until interrupted (Ctrl-C), unless `--once` is set.

---

## Git

### clean

Strip outputs from a notebook and write the cleaned JSON to stdout. Use this for clean diffs before committing.

```
agent-repl clean PATH [-p PORT] [--timeout FLOAT]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `PATH` | positional | required | Notebook path |
| `-p`, `--port` | `int` | `AGENT_REPL_PORT` | Server port |
| `--timeout` | `float` | `10.0` | HTTP request timeout |

```bash
agent-repl clean demo.ipynb > demo_clean.ipynb
```

Output is the full notebook JSON with all cell outputs removed.

---

### git-setup

Configure `.gitattributes` and a git clean filter so notebooks are automatically stripped of outputs on `git add`.

```
agent-repl git-setup
```

No flags. Run once per repository.

```bash
agent-repl git-setup
```

```json
{"gitattributes": ".gitattributes updated", "status": "ok"}
```

---

## Utility

### start

Launch JupyterLab with no-auth flags in the background. Designed for agent workflows where token management is unnecessary.

```
agent-repl start [--dir PATH] [--port INT] [--foreground]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--dir` | `str` | `.` | Root directory for JupyterLab |
| `--port` | `int` | auto | Port to run on |
| `--foreground` | flag | off | Run in foreground instead of backgrounding |

```bash
agent-repl start --dir ./notebooks --port 8899
```

The server starts in the background by default. Use `agent-repl servers` to verify it is running.

---

## Shared Flags

These flags appear across multiple commands:

| Flag | Scope | Description |
|------|-------|-------------|
| `-p`, `--port` | most commands | Server port (or set `AGENT_REPL_PORT` env var) |
| `--server-url` | most commands | Full server URL, alternative to `-p` |
| `--session-id` | execution commands | Target a specific notebook session |
| `--kernel-id` | execution commands | Target a specific kernel directly |
| `--transport` | execution commands | Kernel transport: `auto`, `websocket`, or `zmq` |
| `--timeout` | all commands | Request/execution timeout (10.0s for reads, 30.0s for execution) |
| `--pretty` | all commands | Pretty-print JSON output |
| `--raw-output` | read/execute commands | Disable media stripping, show full MIME bundles |

### Input Sources

Commands that accept source code or operations support three input methods:

1. **Inline flag**: `-s 'code here'` or `-c 'code here'` or `--operations '[...]'`
2. **File flag**: `--source-file path` or `--code-file path` or `--operations-file path`
3. **Stdin**: pipe content when neither flag is provided

### Cell Selection

Commands that target a specific cell accept either:

- `-i INDEX` / `--index INDEX` -- cell by position (0-based)
- `--cell-id ID` -- cell by its unique ID

These are mutually exclusive.

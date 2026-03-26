# Command Reference

**Headless notebook workflow** - `new`, `ix`, `edit`, `exec`, `cat`, `status`, `select-kernel`, `run-all`, `restart`, and `restart-run-all` are the core notebook commands.

**Editor-assisted workflow** - `kernels`, `prompts`, `respond`, and `reload` are mainly for extension-backed or development scenarios.

**Structured outputs** - Public subcommands return JSON. Use `--pretty` when you want indented output.

## Minimal Happy Path

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

That is the default workflow. Use `cat` or `status` only when you need diagnostics.

## Core Notebook Commands

### `new`

Create a notebook and prepare the runtime.

```bash
agent-repl new PATH [--kernel PYTHON] [--cells-json JSON]
```

Examples:

```bash
agent-repl new analysis.ipynb
agent-repl new analysis.ipynb --cells-json '[{"type":"markdown","source":"# Notes"},{"type":"code","source":"print(1)"}]'
```

Notes:

- uses the workspace `.venv` automatically when it exists
- returns `ready: true` when the notebook is immediately usable
- does not auto-run starter cells from `--cells-json`

### `ix`

Insert a new cell, run it, and return the result.

```bash
agent-repl ix PATH (-s SOURCE | --source-file FILE | stdin) [--at-index N] [--timeout SECONDS] [--no-wait] [--session-id ID]
```

Examples:

```bash
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
agent-repl ix analysis.ipynb --source-file /tmp/cell.py
agent-repl ix analysis.ipynb -s 'df.head()' --session-id sess-human
```

Notes:

- waits for completion by default
- use `--no-wait` only when you intentionally want fire-and-forget behavior
- use `--session-id` when the run should be attributed to a live collaboration session
- the result is returned directly, so `cat` is not required in the normal path
- if an infrastructure error occurs (kernel crash, connection lost, timeout), the inserted cell is automatically rolled back and the error message says "ix failed and the inserted cell was rolled back"
- Python exceptions in your code are not rolled back — the cell stays with error output, like a normal notebook

### `edit`

Edit notebook cells.

```bash
agent-repl edit PATH replace-source --cell-id ID -s 'new code'
agent-repl edit PATH insert -s 'print(1)' [--cell-type code|markdown] [--at-index N] [--session-id ID]
agent-repl edit PATH delete --cell-id ID
agent-repl edit PATH move --cell-id ID --to-index N
agent-repl edit PATH clear-outputs --all
```

Use `--cell-id` when possible. It is safer than positional indexes.
Use `--session-id` when you want edit events and lease checks tied to a specific collaboration session.

### `exec`

Run an existing cell, or insert and run inline code.

```bash
agent-repl exec PATH --cell-id ID [--timeout SECONDS] [--no-wait] [--session-id ID]
agent-repl exec PATH -c 'probe code' [--session-id ID]
```

Notes:

- `exec --cell-id` reruns an existing cell
- `exec -c` inserts a real persistent code cell and runs it
- `--session-id` attributes the execution to a live collaboration session for activity and lease checks

### `cat`

Inspect notebook structure and outputs.

```bash
agent-repl cat PATH [--no-outputs]
```

Use this when you need:

- live `cell_id` values
- full notebook structure
- a source/output inspection pass

If the notebook is still on a disk-only path, `cat` may emit placeholder IDs such as `index-1`. Once the notebook becomes live, re-run `cat --no-outputs` and switch to the real UUIDs.

### `status`

Inspect notebook execution state.

```bash
agent-repl status PATH
```

Use this when:

- a run is long-lived
- a command timed out
- you want to confirm the notebook is idle before the next step

### `run-all`

Execute every code cell and return the execution results.

```bash
agent-repl run-all PATH
```

On the public CLI path, this runs synchronously against the shared runtime and returns the resulting execution payloads.

### `restart`

Restart the notebook runtime.

```bash
agent-repl restart PATH
```

### `restart-run-all`

Restart the notebook runtime and execute every code cell.

```bash
agent-repl restart-run-all PATH
```

On the public CLI path, this completes the restart and returns the execution results from the rerun.

### `select-kernel`

Choose a kernel for a notebook. This affects subsequent `ix` and `exec` calls on the same notebook.

```bash
agent-repl select-kernel PATH [--kernel-id ID] [--interactive]
```

Examples:

```bash
agent-repl select-kernel analysis.ipynb --kernel-id /opt/miniconda3/bin/python3
agent-repl select-kernel analysis.ipynb --kernel-id python3
```

Notes:

- without `--kernel-id`, it tries the workspace `.venv` first
- this changes the active headless runtime kernel — the selected kernel is used by all subsequent execution commands
- if a runtime is already running with a different kernel, it is restarted with the new one
- use `--interactive` only when you explicitly want the VS Code editor kernel picker (requires extension)

## Editor-Assisted Commands

### `kernels`

List available kernels in an editor-backed workspace.

```bash
agent-repl kernels
```

### `prompts`

List prompt cells already present in the notebook.

```bash
agent-repl prompts PATH
```

### `respond`

Answer a prompt cell from the CLI.

```bash
agent-repl respond PATH --to CELL_ID (-s SOURCE | --source-file FILE | stdin)
```

This workflow assumes a human is using the notebook UI and has created prompt cells from the editor. `respond` is still an editor-backed workflow today.

### `reload`

Hot-reload the installed extension routes.

```bash
agent-repl reload --pretty
```

This is mainly a development or extension-debugging command.

## Shared Input Rules

Source-accepting commands support three input modes:

1. `-s 'inline code'`
2. `--source-file /path/to/file`
3. stdin when neither flag is provided

## Command Surface Summary

| Command | Headless with editor closed | Live projection aware |
|---|---|---|
| `new` | Yes | Yes |
| `ix` | Yes | Yes |
| `edit` | Yes | Yes |
| `exec` | Yes | Yes |
| `cat` | Yes | Yes |
| `status` | Yes | Yes |
| `run-all` | Yes | Yes |
| `restart` | Yes | Yes |
| `restart-run-all` | Yes | Yes |
| `kernels` | No | Yes |
| `select-kernel` | Yes | Yes |
| `prompts` | Yes | Yes |
| `respond` | Usually no | Yes |
| `reload` | No | Yes |

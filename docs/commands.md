# Command Reference

Complete reference for every `agent-repl` command. All commands return JSON (compact by default, formatted with `--pretty`).

---

## cat

Read notebook contents, cleaned for agent consumption. Rich media (HTML, images, widgets) is stripped; the notebook file retains everything for humans.

```
agent-repl cat PATH [--no-outputs] [--pretty]
```

| Flag | Description |
|------|-------------|
| `PATH` | Notebook path (relative to workspace) |
| `--no-outputs` | Show cell sources only, without outputs |
| `--pretty` | Pretty-print JSON output |

```bash
agent-repl cat demo.ipynb
```

Each cell in the response includes: `index`, `cell_id`, `cell_type`, `source`, and optionally `outputs` and `execution_count`. Cells with prompt metadata include an `agent_repl` object with `type` and `status`.
Paths outside the active workspace are rejected.

---

## status

Get kernel execution state and queue information for a notebook.

```
agent-repl status PATH [--pretty]
```

```bash
agent-repl status demo.ipynb
```

Returns kernel state (idle/busy), currently running cells, and queued cells with their owner (human/agent). Paths outside the active workspace are rejected.

---

## exec

Execute code in a notebook's kernel. Either run an existing cell by ID, or insert and execute inline code.

```
agent-repl exec PATH (--cell-id ID | -c CODE) [--pretty]
```

| Flag | Description |
|------|-------------|
| `PATH` | Notebook path |
| `--cell-id` | Execute an existing cell by its ID |
| `-c`, `--code` | Code to insert and execute |

```bash
# Execute existing cell
agent-repl exec demo.ipynb --cell-id abc123

# Insert and execute inline code
agent-repl exec demo.ipynb -c 'x = 42; print(x)'
```

When using `-c`, a new persistent cell is inserted and executed (same as `ix`). One of `--cell-id` or `-c` is required.

Completed `exec` responses include:

- `execution_mode`: the backend that actually ran the cell, such as `jupyter-private-session` or `notebook-command`
- `execution_preference`: the requested behavior, either `no-yank` or `native`
- `execution_fallback_reason`: present when a no-yank attempt had to fall back to the notebook command path

---

## ix

Insert a new cell and execute it. Returns immediately with the `cell_id` — execution continues asynchronously in VS Code.

```
agent-repl ix PATH (-s SOURCE | --source-file FILE | stdin) [--pretty]
```

| Flag | Description |
|------|-------------|
| `PATH` | Notebook path |
| `-s`, `--source` | Cell source code |
| `--source-file` | File containing cell source |

```bash
# Inline source
agent-repl ix demo.ipynb -s 'import pandas as pd; print(pd.__version__)'

# From a file
agent-repl ix demo.ipynb --source-file /tmp/cell.py

# From stdin
echo 'print("hello")' | agent-repl ix demo.ipynb
```

The cell is appended to the end of the notebook by default.

`ix` respects the `agent-repl.executionMode` setting:

- `no-yank` (default) prefers the background Jupyter execution path so the notebook can update without stealing focus
- `native` always uses VS Code's notebook execution command path

On a brand-new notebook or first kernel attach, Jupyter may still briefly reveal the notebook before later runs settle into the no-yank path.

---

## edit

Edit notebook cells. Five subcommands for different operations.

### edit replace-source

Replace the source of an existing cell.

```
agent-repl edit PATH replace-source (--cell-id ID | -i INDEX) (-s SOURCE | --source-file FILE | stdin) [--pretty]
```

```bash
agent-repl edit demo.ipynb replace-source --cell-id abc -s 'x = 99'
```

### edit insert

Insert a new cell at a specified position.

```
agent-repl edit PATH insert (-s SOURCE | --source-file FILE | stdin) [--cell-type TYPE] [--at-index INT] [--pretty]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--cell-type` | `code` | Cell type: `code` or `markdown` |
| `--at-index` | `-1` (end) | Position to insert at |

```bash
agent-repl edit demo.ipynb insert --at-index 0 --cell-type code -s 'import math'
```

### edit delete

Delete a cell from the notebook.

```
agent-repl edit PATH delete (--cell-id ID | -i INDEX) [--pretty]
```

```bash
agent-repl edit demo.ipynb delete --cell-id abc
```

### edit move

Move a cell to a different position.

```
agent-repl edit PATH move (--cell-id ID | -i INDEX) --to-index INT [--pretty]
```

```bash
agent-repl edit demo.ipynb move --cell-id abc --to-index 0
```

### edit clear-outputs

Clear outputs from one or all cells.

```
agent-repl edit PATH clear-outputs [--cell-id ID | -i INDEX | --all] [--pretty]
```

```bash
agent-repl edit demo.ipynb clear-outputs --all
```

---

## run-all

Trigger execution of all cells in the notebook and return immediately.

```
agent-repl run-all PATH [--pretty]
```

```bash
agent-repl run-all demo.ipynb
```

Use `agent-repl status PATH` to watch the run until the kernel becomes idle.

---

## restart

Restart the notebook's kernel without running any cells.

```
agent-repl restart PATH [--pretty]
```

```bash
agent-repl restart demo.ipynb
```

---

## restart-run-all

Restart the kernel, trigger execution of all cells, and return immediately.

```
agent-repl restart-run-all PATH [--pretty]
```

```bash
agent-repl restart-run-all demo.ipynb
```

Use `agent-repl status PATH` to watch the run until the kernel becomes idle.

---

## new

Create a new notebook in the workspace.

```
agent-repl new PATH [--kernel ID] [--cells-json JSON] [--pretty]
```

| Flag | Description |
|------|-------------|
| `PATH` | Notebook path to create |
| `--kernel` | Kernel ID to auto-select (skips interactive picker) |
| `--cells-json` | JSON array of `{"type": "code", "source": "..."}` |

```bash
agent-repl new analysis.ipynb
agent-repl new analysis.ipynb --cells-json '[{"type":"code","source":"import pandas as pd"}]'
```

The notebook is created and opened in VS Code with a Python kernel.

When a workspace `.venv` is present, `new` prefers it automatically and the response includes `kernel_status: "selected"` plus a message naming the selected kernel. If no workspace `.venv` is available, the response includes `kernel_status: "needs_selection"`, `available_kernels`, and `select_kernel_command` so an agent or human can pick one immediately.

---

## kernels

List available Jupyter kernels, including workspace `.venv` and installed kernelspecs.

```
agent-repl kernels [--pretty]
```

```bash
agent-repl kernels
```

Returns `kernels` (array of kernel records with `id`, `label`, `type`, `python` path), `preferred_kernel` (workspace `.venv` if found), and `workspace` path. Use the returned `id` value with `select-kernel --kernel-id`.

---

## select-kernel

Select a kernel for a notebook. With `--kernel-id`, selects programmatically using one of the identifiers returned by `agent-repl kernels`. Without it, opens VS Code's interactive kernel picker.

```
agent-repl select-kernel PATH [--kernel-id ID] [--extension EXT] [--pretty]
```

| Flag | Default | Description |
|------|---------|-------------|
| `PATH` | | Notebook path |
| `--kernel-id` | | Kernel ID to select programmatically |
| `--extension` | `ms-toolsai.jupyter` | Extension providing the kernel controller |

```bash
# Programmatic selection using a kernel identifier from `agent-repl kernels`
agent-repl select-kernel demo.ipynb --kernel-id /path/to/.venv/bin/python

# Interactive picker
agent-repl select-kernel demo.ipynb
```

If programmatic selection cannot be completed quietly, the command returns `status: "selection_failed"` with guidance instead of silently dropping into the interactive picker.

---

## prompts

List cells marked as agent prompts in a notebook.

```
agent-repl prompts PATH [--pretty]
```

```bash
agent-repl prompts demo.ipynb
```

Returns cells with `agent-repl` prompt metadata (created via the "Ask Agent" button in VS Code). Each prompt includes `cell_id`, `type`, `status` (pending/in-progress/answered), and the cell source.

---

## respond

Respond to a prompt cell. Atomically: marks the prompt in-progress, inserts a response cell, executes it, then marks the prompt as answered.

```
agent-repl respond PATH --to CELL_ID (-s SOURCE | --source-file FILE | stdin) [--pretty]
```

| Flag | Description |
|------|-------------|
| `--to` | Cell ID of the prompt to respond to (required) |
| `-s`, `--source` | Response code |
| `--source-file` | File containing response code |

```bash
agent-repl respond demo.ipynb --to abc123 -s 'df.dropna(inplace=True); print(df.shape)'
```

---

## reload

Hot-reload the extension's route handlers without restarting the bridge server. Useful during extension development.

```
agent-repl reload
```

```bash
agent-repl reload
```

No arguments. Prints "Extension host restarting..." on success.

---

## Shared Concepts

### Source Input

Commands that accept source code (`ix`, `respond`, `edit replace-source`, `edit insert`) support three input methods:

1. **Inline flag**: `-s 'code here'`
2. **File flag**: `--source-file path`
3. **Stdin**: pipe content when neither flag is provided

### Cell Selection

Commands that target a specific cell accept either:

- `--cell-id ID` — stable UUID (preferred, survives structural changes)
- `-i INDEX` / `--index INDEX` — position (0-based)

### Output Format

All commands return JSON objects. Use `--pretty` for indented output. Errors are returned as `{"error": "message"}` on stderr with a non-zero exit code.

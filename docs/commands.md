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

Select a kernel for a notebook. With `--kernel-id`, selects programmatically using one of the identifiers returned by `agent-repl kernels`. Without it, `agent-repl` first tries the workspace `.venv` automatically when one exists. Use `--interactive` to open VS Code's kernel picker explicitly.

```
agent-repl select-kernel PATH [--kernel-id ID] [--interactive] [--extension EXT] [--pretty]
```

| Flag | Default | Description |
|------|---------|-------------|
| `PATH` | | Notebook path |
| `--kernel-id` | | Kernel ID to select programmatically |
| `--interactive` | `false` | Open the VS Code kernel picker instead of defaulting to the workspace `.venv` |
| `--extension` | `ms-toolsai.jupyter` | Extension providing the kernel controller |

```bash
# Programmatic selection using a kernel identifier from `agent-repl kernels`
agent-repl select-kernel demo.ipynb --kernel-id /path/to/.venv/bin/python

# Default to the workspace `.venv` when it exists
agent-repl select-kernel demo.ipynb

# Interactive picker
agent-repl select-kernel demo.ipynb --interactive
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

## v2

Experimental workspace-scoped core daemon commands for the v2 architecture work.

```
agent-repl v2 {start|attach|status|stop|sessions|session-start|session-touch|session-detach|session-end|documents|document-open|document-refresh|document-rebind|branches|branch-start|branch-finish|runtimes|runtime-start|runtime-stop|runs|run-start|run-finish} [--workspace-root PATH] [--pretty]
```

### v2 start

Start the experimental v2 core daemon for the current workspace and return its status payload.
The daemon now restores workspace-owned v2 state from `.agent-repl/v2-core-state.json` when present.

```bash
agent-repl v2 start
agent-repl v2 start --workspace-root /path/to/workspace
```

### v2 attach

Ensure the v2 core daemon is running, then attach or resume a client session against that workspace authority.
When the VS Code extension bridge starts, it now attempts this attach flow automatically for the
primary workspace folder unless `agent-repl.v2AutoAttach` is disabled in extension settings.
For deterministic extension-host launches, you can also set `agent-repl.cliPath` to an explicit
CLI path or command name; otherwise the extension tries a workspace-local `.venv` launcher first,
then `uv run agent-repl`, then `agent-repl` on PATH.

```bash
agent-repl v2 attach --actor agent --client-type cli --label "worker"
agent-repl v2 attach --actor human --client-type vscode --session-id <session-id>
```

### v2 status

Inspect the experimental v2 core daemon bound to a workspace.
The status payload includes the workspace-owned `state_file` path used for persisted continuity.

```bash
agent-repl v2 status
agent-repl v2 status --workspace-root /path/to/workspace
```

### v2 stop

Stop the experimental v2 core daemon bound to a workspace.

```bash
agent-repl v2 stop
agent-repl v2 stop --workspace-root /path/to/workspace
```

### v2 sessions

List active v2 sessions registered in the workspace-scoped core daemon.

```bash
agent-repl v2 sessions
```

### v2 session-start

Start or resume a v2 session for the workspace.

```bash
agent-repl v2 session-start --actor agent --client-type cli --label "worker"
agent-repl v2 session-start --actor human --client-type vscode
agent-repl v2 session-start --actor agent --client-type cli --capability ops --capability automation
```

Reusing the same `--session-id` resumes the same attachment record and increments its resume count.

### v2 session-touch

Refresh liveness for an attached v2 session without redefining its identity.

```bash
agent-repl v2 session-touch --session-id <session-id>
```

### v2 session-detach

Detach a v2 session without deleting its continuity record. Use this when a client surface goes away but the system should remember its attachment history for safe reconnect.

```bash
agent-repl v2 session-detach --session-id <session-id>
```

### v2 session-end

End a previously registered v2 session.

```bash
agent-repl v2 session-end --session-id <session-id>
```

### v2 documents

List canonical documents currently registered in the workspace-scoped v2 core.

```bash
agent-repl v2 documents
```

### v2 document-open

Register a canonical v2 document inside the workspace-scoped core authority. The first open captures the currently observed file snapshot as the bound compatibility baseline. Opening an already-registered path refreshes the observed snapshot without silently rebinding canonical state.

```bash
agent-repl v2 document-open notebooks/demo.ipynb
```

### v2 document-refresh

Refresh the observed file snapshot for a registered document and report whether the file is still in sync with the bound baseline.

```bash
agent-repl v2 document-refresh --document-id <document-id>
```

### v2 document-rebind

Explicitly accept the currently observed file snapshot as the new bound baseline for a registered document.

```bash
agent-repl v2 document-rebind --document-id <document-id>
```

### v2 branches

List collaboration branches registered in the workspace-scoped v2 core.

```bash
agent-repl v2 branches
```

### v2 branch-start

Create a collaboration branch for a canonical document with explicit ownership and purpose metadata.

```bash
agent-repl v2 branch-start --document-id <document-id> --owner-session-id <session-id> --title "Experiment"
agent-repl v2 branch-start --document-id <document-id> --parent-branch-id <branch-id> --purpose "Review risky refactor"
```

### v2 branch-finish

Move a collaboration branch to a terminal review outcome.

```bash
agent-repl v2 branch-finish --branch-id <branch-id> --status-value merged
agent-repl v2 branch-finish --branch-id <branch-id> --status-value rejected
```

### v2 runtimes

List runtimes registered in the workspace-scoped v2 core.

```bash
agent-repl v2 runtimes
```

### v2 runtime-start

Register or resume a runtime in the workspace-scoped v2 core.

```bash
agent-repl v2 runtime-start --mode shared --label primary --environment .venv
agent-repl v2 runtime-start --mode headless
```

### v2 runtime-stop

Mark a registered runtime as stopped.

```bash
agent-repl v2 runtime-stop --runtime-id <runtime-id>
```

### v2 runs

List runs recorded in the workspace-scoped v2 core.

```bash
agent-repl v2 runs
```

### v2 run-start

Register a running execution intent against a target.

```bash
agent-repl v2 run-start --runtime-id <runtime-id> --target-type document --target-ref <document-id>
agent-repl v2 run-start --runtime-id <runtime-id> --target-type branch --target-ref <branch-id> --kind execute
```

### v2 run-finish

Finish a run with a terminal status.

```bash
agent-repl v2 run-finish --run-id <run-id> --status-value completed
agent-repl v2 run-finish --run-id <run-id> --status-value failed
```

Current behavior:

- the daemon is workspace-scoped
- it runs independently of VS Code
- it now exposes experimental session, document, runtime, run, and file-sync registration APIs
- documents now carry explicit file compatibility state with `bound_snapshot`, `observed_snapshot`, and `sync_state`
- external file changes stay visible as `external-change` until you explicitly `document-rebind`
- runtime and run state are explicit, but real notebook editing/execution are not yet routed through this path
- this is still early v2 core scaffolding, not the finished workflow

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

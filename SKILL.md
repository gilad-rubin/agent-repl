---
name: agent-repl
description: Work against the shared agent-repl notebook runtime. Use this when an agent needs to create notebooks, select kernels, read or edit cells, execute notebook code, answer prompt cells, or debug notebook/runtime behavior whether or not the editor is open.
---

# agent-repl

CLI for AI agents to work with a shared notebook runtime. Agents use the CLI; if VS Code/Cursor is open, humans see the notebook update live as a projection of that same runtime. The editor can be open or closed at the start.

Everything prints JSON to stdout.

Before validating behavior from another workspace, check the install state first:

```bash
agent-repl --version
```

- if `agent-repl --version` fails, reinstall the CLI with `uv tool install /path/to/agent-repl --reinstall`
- if you are explicitly validating live editor projection behavior, also run `agent-repl reload --pretty`; if it points at an older `extension_root` or `routes_module`, rebuild and reinstall the extension `.vsix`, then reload or reopen that VS Code window
- when you intentionally want to test repo source before reinstalling, prefer `uv run --project /Users/giladrubin/python_workspace/agent-repl agent-repl ...`

## Core Loop

Prefer the minimal happy path:

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb --source-file /tmp/cell.py
```

For existing notebooks:

```bash
agent-repl edit demo.ipynb replace-source --cell-id <id> -s 'updated code'
agent-repl ix demo.ipynb -s 'x + 1'
```

- `new` should create the notebook and silently prepare the runtime/kernel
- if a workspace `.venv` exists, it should be the default kernel choice
- `ix` is the default execution primitive because it inserts a visible code cell, runs it, and returns the result directly
- `status` and `cat` are diagnostics, not required ritual for the happy path
- use `cat --no-outputs` when you specifically need live `cell_id` values or full notebook structure
- if `cat` returns placeholder IDs like `index-1` for a closed notebook, re-run `cat --no-outputs` after the notebook becomes live before using `--cell-id`

## Validation Loop

For the prompt "test out the new agent-repl capabilities", use this order:

```bash
agent-repl --version
```

Then validate a brand-new notebook:

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
agent-repl edit tmp/validation.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec tmp/validation.ipynb --cell-id <id>
```

Then validate an existing notebook:

```bash
agent-repl cat notebooks/demo.ipynb --no-outputs
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <live-uuid> -s 'updated code'
agent-repl exec notebooks/demo.ipynb --cell-id <live-uuid>
agent-repl ix notebooks/demo.ipynb -s 'x + 1'
```

Important validation expectations:

- `new` should succeed only when the notebook is ready for immediate `ix`, `edit`, or `exec`
- `new` should start or resume the needed runtime automatically; no separate bootstrap step is part of the happy path
- if no workspace `.venv` exists and no explicit kernel is provided, `new` should fail clearly instead of prompting through UI
- `ix` should return the result directly in the common case
- after editing code, re-run the edited cell and verify that the outputs now match the new source
- if the editor is closed, the same commands should still work headlessly
- if the editor is already open, the human should see the new/edited/running cells appear live without focus steal or popups
- if you opened an existing notebook from a closed state, ignore placeholder `index-*` IDs after it becomes live; re-`cat` and use the live UUIDs instead

## Command Contracts

```bash
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'print("hi")'
agent-repl ix analysis.ipynb --source-file /tmp/cell.py
agent-repl exec analysis.ipynb --cell-id <id>
agent-repl edit analysis.ipynb replace-source --cell-id <id> -s 'new code'
agent-repl status analysis.ipynb
agent-repl restart analysis.ipynb
```

- `ix` waits for completion by default, with a default timeout of 30 seconds
- use `ix --no-wait` only when you intentionally want fire-and-forget behavior
- `exec --cell-id` is for re-running an existing cell
- `exec -c/--code` inserts a new persistent code cell and executes it, just like `ix`; use it only when you want that probe to remain in the notebook or you are prepared to delete it afterward
- prefer `--cell-id` over `--index`; IDs survive reordering while indexes do not
- source input is shared across commands: `-s`, `--source-file`, or stdin
- `run-all` and `restart-run-all` trigger notebook execution and return immediately; follow them with `status` until the kernel is idle before assuming the notebook is ready
- the top-level commands are the normal workflow surface; if you need to debug the internal core daemon, keep that as an explicit maintenance task rather than part of the standard notebook loop

## Notebook Creation

Use `new` when you want a real notebook file with a ready runtime/kernel:

```bash
agent-repl new analysis.ipynb
agent-repl new analysis.ipynb --cells-json '[{"type":"markdown","source":"# Notes"},{"type":"code","source":"print(1)"}]'
```

Important input rule:

- `--cells-json` expects objects with `type` and `source`
- valid `type` values are `code` and `markdown`
- notebook-file style `cell_type` is output schema, not input schema
- if you pass `cell_type` instead of `type`, creation may silently coerce cells to markdown

When `new` returns:

- `kernel_status: "selected"`: runtime/kernel prepared successfully
- `ready: true`: notebook is immediately usable for `ix`, `edit`, or `exec`
- starter cells are created, not auto-executed; execute the seed code explicitly before depending on its variables

## Kernel Selection

`new` prefers the workspace `.venv` when it exists. Use explicit selection only when you intentionally want a non-default kernel:

```bash
agent-repl kernels
agent-repl select-kernel analysis.ipynb
```

- `select-kernel` now defaults to the workspace `.venv` when it exists
- use `agent-repl select-kernel analysis.ipynb --interactive` to open the VS Code kernel picker explicitly
- use the exact `id` returned by `agent-repl kernels` when you need a non-default kernel
- creating a notebook or selecting a kernel should stay in the background on the quiet path; if VS Code prompts or steals focus, treat that as a product bug rather than expected behavior

## Timeouts and Busy Kernels

```bash
agent-repl ix demo.ipynb --source-file /tmp/cell.py --timeout 300
```

- a timeout means the CLI stopped waiting; it does not necessarily mean execution stopped
- after a timeout, check `status`, then `cat` when the kernel is idle
- avoid stacking more `ix` calls blindly after a timeout
- if notebook state looks stale, use `agent-repl restart <path>` to reset execution tracking and the kernel together
- after `run-all` or `restart-run-all`, expect the notebook to stay busy until VS Code finishes the run; this is normal unless `status` stops changing

## Prompts

Humans can create prompt cells in the notebook UI. Agents should answer them with:

```bash
agent-repl prompts demo.ipynb
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
```

`respond` is the safest path for prompt cells because it marks the prompt in progress, inserts a response cell, executes it, then marks the prompt answered.

<important if="creating a new notebook or selecting a kernel">

## Kernel Selection

Use the JSON response from `new` directly:

- if it already selected a kernel, continue with `ix`
- if it returned kernel choices such as `available_kernels`, use `select-kernel` for the workspace default or `kernels` to inspect the exact IDs
- if the notebook is open in the editor and auto-select still failed, retry `select-kernel` before resorting to manual UI clicks

Creating a brand-new notebook and attaching a kernel should stay in the background. If VS Code prompts, steals focus, or asks the user to restart a kernel, treat that as a product bug and capture the exact command plus the returned JSON.

</important>

<important if="a cell is taking a long time, or ix returned status timeout">

## Timeout Handling

`ix` default timeout is **30 seconds**. For longer cells:

```bash
agent-repl ix demo.ipynb --source-file /tmp/cell.py --timeout 300
```

If timeout occurs:

- assume the kernel may still be running the cell
- use `status` until the notebook becomes idle
- use `cat` to inspect outputs and grab fresh `cell_id` values
- do not queue more executions until the running cell is resolved
- if the state is obviously wrong, use `restart`

</important>

<important if="getting errors, 404s, or unexpected behavior from the CLI">

## Troubleshooting

**Cell IDs changed after reload or edits** — Re-read with `cat` before using `--cell-id`.

**Closed notebook `cat` returned `index-*` IDs** — Those placeholder IDs are only safe while the notebook stays on the disk-only path. Once the notebook becomes live/open, re-run `cat --no-outputs` and switch to the real UUIDs before `exec` or `edit`.

**404 on execute/edit** — Usually means the cell ID is wrong, not that the route is missing.

**`exec -c` left a surprise probe cell behind** — That is expected behavior: `exec -c` inserts a real notebook cell. Prefer `exec --cell-id` to rerun existing code, or delete the probe cell after debugging.

**Notebook is outside the workspace** — Run the command from the correct workspace root, or pass a notebook path inside that workspace. Opening VS Code is not required for the headless path.

**Kernel selection acted strangely** — Retry `select-kernel` first. If you need to inspect exact IDs, use `kernels`. If you need the VS Code picker, use `select-kernel --interactive` explicitly.

**`run-all` or `restart-run-all` returned but the notebook is still busy** — Those commands return after triggering execution, not after completion. Use `status` to watch the active run.

**CLI has stale code** — `uv tool install <path> --force` can reuse cached wheels. Use `--reinstall`:
```bash
uv tool install /path/to/agent-repl --reinstall
```

From this repo checkout, the fastest install/verify path is:

```bash
uv tool install . --reinstall
agent-repl --version
agent-repl --help
```

**Repo source changed, but VS Code still runs old behavior** — `agent-repl reload` hot-reloads the installed extension, not your repo checkout. After changing `extension/src/*`, package and reinstall the extension or use an Extension Development Host:

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
agent-repl reload --pretty
```

**`cat` returns `cells: []` but the `.ipynb` file has real cells on disk** — Treat that as bridge drift or a bad in-memory notebook state first. Verify the file on disk, then run `agent-repl reload --pretty` and confirm `extension_root` / `routes_module` point at the build you intended to test before changing path-resolution code.

**Hot-reload scope** — `agent-repl reload` updates `routes` and the execution queue. Changes to `extension.ts` or `server.ts` still need a full VS Code window reload.

</important>

## Execution Modes

Agent-triggered execution defaults to `no-yank`, which means the steady-state path should stay in the background and avoid stealing editor focus. Set `agent-repl.executionMode` to `native` only if you explicitly want VS Code's built-in execution behavior. Completed responses include `execution_mode` and `execution_preference`, so check them when debugging focus-stealing or execution-path issues.

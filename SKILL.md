---
name: agent-repl
description: Work against a live Jupyter notebook via the VS Code bridge. Use this when an agent needs to create notebooks, select kernels, read or edit cells, execute notebook code, answer prompt cells, or debug bridge/kernel behavior while Cursor or VS Code is open.
---

# agent-repl

CLI for AI agents to work with a live Jupyter notebook through the VS Code/Cursor bridge. Agents use the CLI; humans see the notebook update live in the editor. Both share the same kernel.

Everything prints JSON to stdout.

Before validating behavior from another workspace, check the install state first:

```bash
agent-repl --version
agent-repl reload --pretty
```

- if `agent-repl --version` fails, reinstall the CLI with `uv tool install /path/to/agent-repl --reinstall`
- if `agent-repl reload --pretty` points at an older `extension_root` or `routes_module`, rebuild and reinstall the extension `.vsix`, then reload or reopen that VS Code window
- when you intentionally want to test repo source before reinstalling, prefer `uv run --project /Users/giladrubin/python_workspace/agent-repl agent-repl ...`

## Core Loop

Use this order unless the user asks for something narrower:

```bash
agent-repl status demo.ipynb
agent-repl cat demo.ipynb --no-outputs
agent-repl ix demo.ipynb --source-file /tmp/cell.py
agent-repl cat demo.ipynb
```

- `status` tells you whether the notebook is open, whether the kernel is busy, and which cells are running or queued
- `cat --no-outputs` gives you stable live cell IDs and source without dragging large outputs into context
- `ix` is the default way to add new executable work because it inserts a visible cell and runs it
- `cat` after execution is the fastest way to verify outputs and capture new `cell_id` values
- if `status` says the notebook is closed or not open yet, `cat` may return fallback IDs like `index-1`; once the notebook becomes live, re-run `cat --no-outputs` before using `--cell-id`

## Validation Loop

For the prompt "test out the new agent-repl capabilities", use this order:

```bash
agent-repl --version
agent-repl reload --pretty
```

Then validate a brand-new notebook:

```bash
agent-repl new tmp/validation.ipynb --cells-json '[{"type":"markdown","source":"# Validation"},{"type":"code","source":"x = 2\\nprint(x)"}]'
agent-repl status tmp/validation.ipynb
agent-repl cat tmp/validation.ipynb --no-outputs
agent-repl exec tmp/validation.ipynb --cell-id <seed-cell-id>
agent-repl edit tmp/validation.ipynb replace-source --cell-id <seed-cell-id> -s 'x = 7\nx ** 2'
agent-repl exec tmp/validation.ipynb --cell-id <seed-cell-id>
agent-repl cat tmp/validation.ipynb
```

Then validate an existing notebook:

```bash
agent-repl status notebooks/demo.ipynb
agent-repl cat notebooks/demo.ipynb --no-outputs
agent-repl select-kernel notebooks/demo.ipynb
agent-repl status notebooks/demo.ipynb
agent-repl cat notebooks/demo.ipynb --no-outputs
agent-repl exec notebooks/demo.ipynb --cell-id <live-uuid>
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <live-uuid> -s 'updated code'
agent-repl exec notebooks/demo.ipynb --cell-id <live-uuid>
agent-repl ix notebooks/demo.ipynb -s 'x + 1'
agent-repl cat notebooks/demo.ipynb
```

Important validation expectations:

- `new --cells-json` creates starter cells but does not execute them; if a later cell depends on a seed variable, run the seed cell first
- after editing code, re-run the edited cell and verify that the outputs now match the new source
- if you opened an existing notebook from a closed state, ignore fallback `index-*` IDs after it becomes live; re-`cat` and use the live UUIDs instead

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

Use `new` when you want a real notebook file that opens in VS Code and attaches a kernel:

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

- `kernel_status: "selected"`: kernel attached successfully
- `kernel_status: "needs_selection"`: no preferred kernel was available; use `kernels` and `select-kernel`
- `kernel_status: "selection_failed"`: the notebook was created, but kernel attachment needs manual recovery
- starter cells are created, not auto-executed; execute the seed code explicitly before depending on its variables

## Kernel Selection

`new` prefers the workspace `.venv` when it exists. If that does not settle cleanly:

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
- if the notebook is open in the editor and auto-select still failed, retry `select-kernel` before falling back to manual UI clicks

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

**Closed notebook `cat` returned `index-*` IDs** — Those fallback IDs are only safe while the notebook stays on the disk-only path. Once the notebook becomes live/open, re-run `cat --no-outputs` and switch to the real UUIDs before `exec` or `edit`.

**404 on execute/edit** — Usually means the cell ID is wrong, not that the route is missing.

**`exec -c` left a surprise probe cell behind** — That is expected behavior: `exec -c` inserts a real notebook cell. Prefer `exec --cell-id` to rerun existing code, or delete the probe cell after debugging.

**Notebook is outside the workspace** — The bridge only serves notebooks in its own workspace. Open the correct VS Code window first.

**Kernel selection acted strangely** — Retry `select-kernel` first. If you need to inspect exact IDs, use `kernels`. If you need the VS Code picker, use `select-kernel --interactive`.

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

Agent-triggered execution defaults to `no-yank`, which prefers background Jupyter APIs and falls back to the notebook command path when needed. Set `agent-repl.executionMode` to `native` if you explicitly want VS Code's built-in execution behavior. Completed responses include `execution_mode` and `execution_preference`, so check them when debugging focus-stealing or execution-path issues.

---
name: agent-repl
description: Work against the shared agent-repl notebook runtime. Use this when an agent needs to create notebooks, edit or execute cells, inspect results, or participate in an editor-driven prompt loop.
---

# agent-repl

`agent-repl` is the notebook runtime. The CLI is the normal agent surface. VS Code or Cursor is optional unless you are intentionally using editor-only features such as prompt-cell creation, explicit kernel picking, or extension reload.

The browser preview and the VS Code canvas share the same bundled UI from `extension/webview-src/main.tsx`.

CLI notebook commands, the VS Code canvas, and the standalone browser preview now reuse the active human workspace session by default. If a human session already exists, new `ix`, `edit`, `exec`, `run-all`, and `restart-run-all` requests join that same ownership context unless you explicitly override it with `--session-id`.

When the notebook is opened in the standalone browser canvas, that shared bundle now wraps the notebook in a minimal VS Code-like explorer for workspace `*.ipynb` files. Plain `/preview.html` roots itself to the folder where `npm run preview:webview` was launched and auto-selects the first notebook it finds; use `?path=...` to force a notebook or `?mock=1` to force the mock preview. Use `Cmd+B` on macOS or `Ctrl+B` elsewhere to collapse or reopen the explorer, and use the toolbar Save action or `Cmd+S` / `Ctrl+S` to flush in-browser drafts into the notebook file immediately.

Public subcommands print JSON on success. Top-level help and version output are plain text.

Canvas Python IDE features use generated Pyright shadow files under the workspace-local `.agent-repl/pyright/` directory instead of creating sibling `.py` files next to notebooks.

## Documentation Sync

- When a shipped feature, command shape, workflow, architecture note, or dev loop changes, update the affected durable docs in the same change.
- Check `AGENTS.md`, `SKILL.md`, `docs/`, and `dev/` together instead of updating only one layer.

## First Check

Before validating behavior from another workspace:

```bash
agent-repl --version
agent-repl --help
```

If the installed CLI is stale:

```bash
uv tool install /path/to/agent-repl --reinstall
```

## Minimal Happy Path

For a new notebook:

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

If you want the notebook open in the editor too:

```bash
agent-repl new tmp/validation.ipynb --open
```

If you want it in the standalone browser canvas instead:

```bash
agent-repl new tmp/validation.ipynb --open --target browser
```

If you want native Jupyter instead of the Agent REPL canvas:

```bash
agent-repl new tmp/validation.ipynb --open --editor jupyter
```

For an existing notebook:

```bash
agent-repl open notebooks/demo.ipynb
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec notebooks/demo.ipynb --cell-id <id>
agent-repl edit notebooks/demo.ipynb insert --cells-json '[{"type":"markdown","source":"# Notes"},{"type":"code","source":"print(1)"}]'
agent-repl ix notebooks/demo.ipynb --cells-json '[{"type":"markdown","source":"# Step 1"},{"type":"code","source":"x = 2\nx * 3"}]'
```

Use `ix` as the default execution primitive. It inserts a visible code cell, executes it, and returns the result directly.
Batch `ix` also supports `--cells-json` / `--cells-file`; it inserts cells sequentially so each code cell still projects as inserted and then running.

## What Commands Are For

- `new` - create the notebook and prepare the runtime automatically
- `open` - open an existing notebook in VS Code or the browser
- `ix` - insert a new cell, run it, and return the result
- `edit` - explicit notebook mutation
- `exec` - rerun a known cell or insert and run inline code
- `cat` - diagnostics or `cell_id` lookup
- `status` - diagnostics for long-running or uncertain execution
- `run-all` / `restart` / `restart-run-all` - notebook-wide execution control
- `select-kernel` - switch the notebook kernel, usually through the shared runtime

`cat` and `status` are useful, but they are not part of the normal happy path.

## Validation Flow

For a prompt like “test out the new agent-repl capabilities,” start with the smallest happy path:

```bash
agent-repl --version
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

Only inspect structure when you need a `cell_id` for an explicit edit or rerun:

```bash
agent-repl cat tmp/validation.ipynb --no-outputs
agent-repl edit tmp/validation.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
agent-repl exec tmp/validation.ipynb --cell-id <id>
```

Then validate an existing notebook:

```bash
agent-repl cat notebooks/demo.ipynb --no-outputs
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'updated code'
agent-repl exec notebooks/demo.ipynb --cell-id <id>
agent-repl ix notebooks/demo.ipynb -s 'x + 1'
```

What to confirm:

- `new` returns a ready notebook
- `new --open` and `open` default to VS Code, and within VS Code they use the Agent REPL canvas editor by default
- `new --open --editor jupyter` and `open --editor jupyter` explicitly choose the native notebook UI
- `new --open --target browser` and `open --target browser` open the standalone browser canvas URL instead
- when VS Code is already attached, the browser preview reuses that same human session instead of creating a lease-conflicting sibling session
- `ix` returns the result directly
- edited source and outputs stay in sync
- the same commands work with the editor closed
- if the editor is open, the human sees the notebook update live without popups or focus steal

## Kernel Rules

- if a workspace `.venv` exists, it is the default runtime for `new` and `ix`
- the `.venv` must have `ipykernel` installed — if it doesn't, the error will name the `.venv` path and tell you how to fix it
- if no workspace `.venv` exists, pass `--kernel` explicitly
- `select-kernel` changes the active kernel for a notebook in the shared runtime — subsequent `ix`, `exec`, and notebook-wide runs use the selected kernel
- use `--interactive` with `select-kernel` only when you want the VS Code kernel picker

## Session Ownership Rules

- `ix`, `edit`, `exec`, `run-all`, and `restart-run-all` reuse the active human workspace session automatically when `--session-id` is omitted
- if no reusable human session exists, the CLI creates a human CLI session and attributes the operation to it
- `--session-id` still overrides the default reuse path when you intentionally want a different collaboration owner
- explicit cross-session lease conflicts are still expected when different session ids target the same leased cell

```bash
agent-repl select-kernel analysis.ipynb --kernel-id /opt/miniconda3/bin/python3
agent-repl ix analysis.ipynb -s 'import sys; print(sys.executable)'
```

Starter cells from `new --cells-json` are created, not auto-executed.

Failed `ix` calls do not leave orphan cells — if the kernel cannot be resolved or an infrastructure error occurs (kernel crash, connection lost, timeout), the inserted cell is rolled back and the notebook is unchanged. The error message will say "ix failed and the inserted cell was rolled back." Python exceptions in your code are *not* rolled back — those behave like normal notebook cells with error output.

## Editor-Assisted Features

These features still assume the extension is available:

- `respond`
- `kernels`
- `reload`
- `open --target vscode`
- `new --open`

These are still editor-oriented, but not all of them are purely VS Code-only:

- `prompts` can be used to inspect prompt metadata from the notebook conversation flow
- `respond` still relies on the editor-assisted prompt loop
- `reload` hot-reloads installed extension routes and reports the live extension paths; it does not fully restart the extension host

If you are validating live editor projection behavior, rebuild/reinstall the extension when needed and then verify:

```bash
agent-repl reload --pretty
```

## Prompt Loop

Human in the editor:

- click **Ask Agent**

Agent in the CLI:

```bash
agent-repl prompts notebooks/demo.ipynb
agent-repl respond notebooks/demo.ipynb --to <cell_id> -s 'df = df.dropna()'
```

Use this only when the notebook is intentionally being used as a conversation surface.

## Troubleshooting

**Need a cell ID**

```bash
agent-repl cat notebooks/demo.ipynb --no-outputs
```

**Notebook still busy**

```bash
agent-repl status notebooks/demo.ipynb
```

**No workspace kernel**

How to fix:
- create a workspace `.venv`, or
- pass `--kernel /absolute/path/to/python`

**Installed extension still runs old code**

How to fix:

```bash
cd extension
npm run compile
npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension agent-repl-0.3.0.vsix --force
agent-repl reload --pretty
```

If preview and VS Code disagree visually, rebuild before packaging so the shared `canvas.js` and `canvas.css` bundle stays in sync across both surfaces.

**Prompt loop commands fail in a closed-editor workflow**

How to fix:
- open the workspace in VS Code or Cursor
- make sure the extension is installed and running

# Getting Started

**Headless first** - the public notebook workflow runs against the shared runtime, so you can create and execute notebooks without opening VS Code or Cursor.

**Canvas by default** - when you open a notebook through `agent-repl`, the default editor is the Agent REPL canvas, not the native Jupyter notebook UI.

**Projection optional** - if the same notebook is open in the canvas while the agent works, the editor should project the shared runtime state live.

## Prerequisites

- **CLI installed** - `uv tool install /path/to/agent-repl --reinstall`
- **Python environment** - a workspace `.venv` is preferred automatically when it exists
- **Optional editor** - install the VS Code or Cursor extension only when you want live projection, prompt cells, or the canvas UI

Verify the installed CLI:

```bash
agent-repl --version
agent-repl --help
```

## 1. Create a Notebook

```bash
agent-repl new tmp/validation.ipynb
```

Expected result:

- `status: "ok"`
- `kernel_status: "selected"`
- `ready: true`

If the workspace has no `.venv`, pass `--kernel /absolute/path/to/python`.

## 2. Insert and Run a Cell

```bash
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

Expected result:

- a real code cell is inserted into the notebook
- the code is executed against the shared runtime
- the JSON response includes the outputs directly

Use `ix` as the default notebook primitive. `cat` is usually unnecessary in the normal happy path.

## 3. Edit and Re-Run

Replace the cell source:

```bash
agent-repl edit tmp/validation.ipynb replace-source --cell-id <id> -s 'x = 7\nx ** 2'
```

Then rerun it:

```bash
agent-repl exec tmp/validation.ipynb --cell-id <id>
```

If you need the `cell_id`, fetch it with:

```bash
agent-repl cat tmp/validation.ipynb --no-outputs
```

## 4. Open the Notebook

Open it in the Agent REPL canvas:

```bash
agent-repl open tmp/validation.ipynb
```

Open it in the native Jupyter editor instead:

```bash
agent-repl open tmp/validation.ipynb --editor jupyter
```

Open it in the standalone browser canvas:

```bash
agent-repl open tmp/validation.ipynb --target browser
```

The browser canvas includes a minimal notebook explorer for other `*.ipynb` files in the same workspace. Use `Cmd+B` on macOS or `Ctrl+B` elsewhere to collapse or reopen it.

Use the Save button in the browser toolbar or press `Cmd+S` on macOS / `Ctrl+S` elsewhere when you want to flush the current in-browser drafts immediately instead of waiting for the editor to blur.

`new --open` accepts the same `--target` and `--editor` choices.

When the notebook is already attached in VS Code, the standalone browser canvas reuses that same human session by default, so execution and edit leases stay aligned across both surfaces.

## 5. Work With the Notebook Already Open

If the notebook is already open in the canvas while the agent works, the editor should behave like a projection client:

- inserted cells appear in place
- source edits update in place
- execution status updates without intentionally stealing focus
- outputs are persisted back to disk

What should not happen:

- notebook tabs jumping to the foreground
- kernel picker interruptions during the normal headless path
- manual restart prompts during ordinary agent execution

The browser preview is useful for renderer-only work, but it is not a substitute for one final in-editor check when the change touches VS Code messaging, session auto-attach, or custom-editor lifecycle.

## 6. Use Diagnostics Only When Needed

Use `cat` when you need structure or IDs:

```bash
agent-repl cat tmp/validation.ipynb --no-outputs
```

Use `status` when you need execution diagnostics:

```bash
agent-repl status tmp/validation.ipynb
```

Use `reload` during extension development:

```bash
agent-repl reload --pretty
```

`reload` hot-reloads installed extension routes and returns the live `extension_root` and `routes_module`. It does not fully restart the VS Code extension host.

## Prompt Cells

Prompt cells are still an editor-started workflow.

Human in the editor:

```text
Click "Ask Agent" in the notebook toolbar
```

Agent in the CLI:

```bash
agent-repl prompts notebooks/demo.ipynb
agent-repl respond notebooks/demo.ipynb --to <cell_id> -s 'df = df.dropna()'
```

Use prompt cells when the notebook itself should behave like the conversation surface. Use `new` + `ix` for the normal notebook workflow.

## Next Steps

- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
- [Installation](/Users/giladrubin/python_workspace/agent-repl/docs/installation.md)
- [Prompt Loop](/Users/giladrubin/python_workspace/agent-repl/docs/prompt-loop.md)
- [CLI JSON API](/Users/giladrubin/python_workspace/agent-repl/docs/api/cli-json.md)

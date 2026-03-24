# Getting Started

**Headless first** - You can create and run notebooks from the CLI without opening VS Code or Cursor.

**Minimal happy path** - The common flow is `new`, then `ix`, then `edit` or `exec` only when you need to change or rerun code.

**Optional live projection** - If the notebook is open in the editor, humans should see the same cells and outputs update live.

## Prerequisites

- **CLI installed** - `uv tool install /path/to/agent-repl --reinstall`
- **Python environment** - a workspace `.venv` is the default runtime when it exists
- **Optional editor projection** - install the VS Code/Cursor extension only if you want live notebook projection or prompt cells

Verify the installed CLI first:

```bash
agent-repl --version
agent-repl --help
```

## 1. Create a Notebook

```bash
agent-repl new tmp/validation.ipynb
```

What to expect:

- `status: "ok"`
- `kernel_status: "selected"`
- `ready: true`

If a workspace `.venv` exists, `new` uses it automatically. If no workspace `.venv` exists, `new` should fail clearly unless you pass `--kernel`.

## 2. Insert and Run a Cell

```bash
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

What to expect:

- the cell is inserted into the notebook
- the code is executed
- the JSON response contains the result directly

Use `ix` for the default “do notebook work” path. You should not need `cat` just to see the result of a normal `ix`.

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

`cat` and `status` are diagnostics. They are useful, but they are not part of the minimal happy path.

## 4. Work on an Existing Notebook

Inspect structure only when needed:

```bash
agent-repl cat notebooks/demo.ipynb --no-outputs
```

Edit a cell:

```bash
agent-repl edit notebooks/demo.ipynb replace-source --cell-id <id> -s 'print(\"updated\")'
```

Run a new cell:

```bash
agent-repl ix notebooks/demo.ipynb -s 'x + 1'
```

## 5. Open the Notebook Later

If you open the notebook after headless agent work, you should immediately see:

- the created or edited cells
- the latest source
- the persisted outputs

If the runtime is still alive, the next manual cell should continue naturally from the same in-memory objects.

## 6. Work With the Notebook Already Open

If the notebook is already open in VS Code or Cursor while the agent works, the editor should behave like a live projection:

- new cells appear in place
- edited source updates in place
- running cells show execution state
- outputs update when execution completes

What should not happen:

- focus steal
- notebook tabs jumping to the foreground
- kernel restart prompts
- manual kernel picker interruptions

## Prompt Cells

Prompt cells are the one workflow that still starts from the editor today.

Human in the editor:

```text
Click “Ask Agent” in the notebook toolbar
```

Agent in the CLI:

```bash
agent-repl prompts notebooks/demo.ipynb
agent-repl respond notebooks/demo.ipynb --to <cell_id> -s 'df = df.dropna()'
```

Use this when the human is explicitly driving a notebook-as-conversation flow.

## Next Steps

- [Command Reference](/Users/giladrubin/python_workspace/agent-repl/docs/commands.md)
- [Installation](/Users/giladrubin/python_workspace/agent-repl/docs/installation.md)
- [Prompt Loop](/Users/giladrubin/python_workspace/agent-repl/docs/prompt-loop.md)
- [CLI JSON API](/Users/giladrubin/python_workspace/agent-repl/docs/api/cli-json.md)

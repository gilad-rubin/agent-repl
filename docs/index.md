# agent-repl

**CLI tool giving AI agents direct access to live Jupyter notebook kernels.**

The notebook open in JupyterLab is the human-facing surface. The CLI is the agent-facing surface. Both share the same kernel, the same file, and the same state -- so humans and agents collaborate on a single notebook in real time.

- **Live kernel access** - Execute code, inspect variables, and read outputs against a running Jupyter kernel from the command line.
- **Prompt loop** - Humans write prompt cells in JupyterLab (`#| agent: ...`), agents discover and respond via CLI.
- **Streaming execution** - Long-running cells emit real-time JSONL events instead of blocking until completion.
- **Git-friendly** - Strip outputs for clean diffs, configure git filters with one command.
- **Output filtering** - Rich media (HTML, images, widgets) is collapsed to text summaries by default, keeping CLI output readable for agents.

## How It Works

Launch JupyterLab, create a notebook, and start executing code -- all from the CLI:

```bash
agent-repl start                          # launch JupyterLab (no-auth, background)
agent-repl new analysis.ipynb             # create notebook, auto-start kernel
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
```

Output:
```json
{"cell_id": "abc123", "outputs": [{"text": "2.2.0\n", "name": "stdout"}], "status": "ok"}
```

The `ix` command (insert-execute) adds a cell to the notebook and runs it in one step. The cell appears in JupyterLab immediately -- a human watching the notebook sees it show up with its output.

## The Core Loop

Agents work with notebooks through five operations:

```bash
# Discover what's running
agent-repl servers                        # list Jupyter servers
agent-repl ls                             # list open notebooks

# Read notebook state
agent-repl cat demo.ipynb                 # view cells (brief by default)
agent-repl cat demo.ipynb --detail full   # full source + outputs

# Execute code
agent-repl exec demo.ipynb -c 'x = 42'   # run in existing cell's kernel
agent-repl ix demo.ipynb -s 'print(x)'   # insert cell + execute

# Edit the notebook
agent-repl edit demo.ipynb replace-source --cell-id abc -s 'x = 2'
agent-repl edit demo.ipynb insert --at-index 1 -t code -s 'print("hi")'

# Verify everything
agent-repl run-all demo.ipynb --save-outputs
```

## Notebook-as-Conversation

Humans write instructions in notebook cells. Agents find and respond to them.

A human writes this cell in JupyterLab:

```python
#| agent: clean this dataframe -- drop nulls, normalize column names
df = pd.read_csv("sales.csv")
df.head()
```

The agent discovers and responds:

```bash
agent-repl prompts demo.ipynb
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
agent-repl watch demo.ipynb              # poll for new prompts (JSONL)
```

The `#| agent:` directive turns any cell into a prompt. Markdown cells use `<!-- agent: ... -->` instead.

## Documentation

- [Installation](installation.md) - Install agent-repl and start JupyterLab
- [Getting Started](getting-started.md) - First notebook workflow end-to-end

## Command Reference

| Command | Alias | Description |
|---------|-------|-------------|
| `servers` | | List Jupyter servers |
| `notebooks` | `ls` | List open notebooks |
| `contents` | `cat` | Read notebook cells and outputs |
| `execute` | `exec` | Execute code in a kernel |
| `insert-execute` | `ix` | Insert cell + execute in one step |
| `edit` | | Edit cells (replace, insert, delete, move, clear, batch) |
| `new` | | Create a notebook |
| `kernels` | | List kernelspecs and running kernels |
| `variables` | `vars` | Inspect live kernel variables |
| `run-all` | | Execute all cells top-to-bottom |
| `restart-run-all` | | Restart kernel + run all cells |
| `restart` | | Restart a kernel |
| `start` | | Launch JupyterLab |
| `prompts` | | List agent prompt cells |
| `respond` | | Respond to a prompt cell |
| `watch` | | Poll for new prompts |
| `context` | | Snapshot kernel + notebook state |
| `clean` | | Strip outputs for git |
| `git-setup` | | Configure git filters |

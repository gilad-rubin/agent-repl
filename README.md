# agent-repl

**CLI tool giving AI agents direct access to live Jupyter notebook kernels.**

The notebook open in JupyterLab is the human-facing surface. The CLI is the agent-facing surface. Both share the same kernel, the same file, and the same state — so humans and agents collaborate on a single notebook in real time.

## Quick Start

```bash
# Install
uv tool install /path/to/agent-repl

# Launch JupyterLab and create a notebook
agent-repl start
agent-repl new analysis.ipynb

# Execute code (cell appears in JupyterLab immediately)
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'

# Read it back
agent-repl cat analysis.ipynb
```

## Key Features

- **Live kernel access** — Execute code, inspect variables, read outputs against a running Jupyter kernel from the CLI
- **Notebook-as-conversation** — Humans write `#| agent: ...` prompt cells in JupyterLab, agents discover and respond via CLI
- **Streaming execution** — Long-running cells emit real-time JSONL events (`--stream`)
- **Smart output filtering** — Rich media (HTML, images, widgets) stripped for agents; notebook file keeps everything for humans
- **Git-friendly** — Strip outputs for clean diffs, configure git filters with one command
- **Batch operations** — Multiple cell edits in a single atomic save
- **Progressive disclosure** — `--detail minimal|brief|full` controls how much context agents receive

## The Prompt Loop

The killer feature: the notebook becomes a bidirectional conversation channel.

**Human writes in JupyterLab:**
```python
#| agent: clean this dataframe — drop nulls, normalize column names
df = pd.read_csv("sales.csv")
df.head()
```

**Agent discovers and responds:**
```bash
agent-repl prompts demo.ipynb                    # find pending prompts
agent-repl respond demo.ipynb --to abc123 \
  -s 'df.columns = [c.lower().replace(" ", "_") for c in df.columns]
df.dropna(inplace=True)
print(f"Cleaned: {df.shape}")'
```

**Continuous monitoring:**
```bash
agent-repl watch demo.ipynb    # poll for new prompts, output JSONL
```

## Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `servers` | | List Jupyter servers |
| `notebooks` | `ls` | List live notebooks |
| `contents` | `cat` | Read notebook contents |
| `execute` | `exec` | Execute code |
| `insert-execute` | `ix` | Insert cell + execute |
| `edit` | | Edit cells (replace, insert, delete, move, clear, batch) |
| `new` | | Create notebook |
| `kernels` | | List kernelspecs |
| `variables` | `vars` | Inspect kernel variables |
| `run-all` | | Execute all cells |
| `restart-run-all` | | Restart kernel + run all |
| `start` | | Launch JupyterLab |
| `prompts` | | List agent prompt cells |
| `respond` | | Respond to a prompt |
| `watch` | | Poll for new prompts |
| `context` | | Snapshot kernel + notebook state |
| `clean` | | Strip outputs for git |
| `git-setup` | | Configure git filters |

## Cell Directives

```python
#| agent: <instruction>           # prompt the agent
#| agent-tags: critical, setup    # tag cells for filtering
#| agent-skip                     # skip in run-all
```

Use with `run-all`:
```bash
agent-repl run-all demo.ipynb --skip-tags setup,expensive
agent-repl run-all demo.ipynb --only-tags critical
```

## Installation

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl

# Dev dependency in another project
uv add --dev agent-repl --path /path/to/agent-repl

# Direct invocation without install
uv run /path/to/agent-repl/scripts/agent_repl.py --help
```

Set `AGENT_REPL_PORT=8899` to skip `-p` on every command. If only one Jupyter server is running, it's auto-selected.

## Documentation

- [Getting Started](docs/getting-started.md) — End-to-end tutorial
- [Command Reference](docs/commands.md) — All 20 commands with examples
- [Prompt Loop](docs/prompt-loop.md) — Notebook-as-conversation pattern
- [Cell Directives](docs/cell-directives.md) — `#| agent:` syntax and tags
- [Output Filtering](docs/output-filtering.md) — Dual-surface output model
- [Git Integration](docs/git-integration.md) — Clean notebooks for version control
- [Installation](docs/installation.md) — All install methods

### Examples
- [Data Analysis Session](docs/examples/data-analysis.md) — Agent does exploratory analysis
- [Agent Loop](docs/examples/agent-loop.md) — Automated watch + respond
- [Multi-Notebook Pipeline](docs/examples/multi-notebook.md) — Working across notebooks

## Architecture

```
src/agent_repl/
├── core/           # errors, models, HTTP client
├── server/         # server discovery, kernelspecs
├── notebook/       # cells, contents, edit, create, directives
├── execution/      # WS/ZMQ execution, streaming, context, variables
├── output/         # media filtering, ANSI normalization
├── cli/            # parser, command dispatch
├── watch.py        # prompt polling
└── git.py          # clean filter, git setup
```

## Acknowledgments

This project builds on the work of several open source projects:

- **[hamelnb](https://github.com/hamelsmu/hamelnb)** by [Hamel Husain](https://github.com/hamelsmu) — The foundation. agent-repl started as a fork of hamelnb's `jupyter_live_kernel.py`, which provides the core transport layer (WebSocket/ZMQ kernel communication, Contents API integration, session resolution, and stale-write guards). The execution pipeline, variable inspection, and run-all verification patterns all originate from hamelnb.

- **[Datalayer Jupyter MCP Server](https://github.com/datalayer/jupyter-mcp-server)** — Inspired the `insert-execute` pattern (insert + execute in one step), `-1` index for append, image output toggling, brief/full content modes, flexible cell range parsing, and the streaming execution approach with elapsed timestamps.

- **[nbdev](https://github.com/fastai/nbdev)** by [fast.ai](https://www.fast.ai/) — Inspired the cell directive system (`#| key: value` syntax), the processor pipeline pattern for output normalization before save, execution filtering via cell tags, and the approach to stripping ANSI codes and volatile repr IDs from outputs.

- **[nbdime](https://github.com/jupyter/nbdime)** — Informed the git integration design: clean/smudge filters for notebooks, output stripping for deterministic diffs, cell ID as primary matching key, and the approach to preserving stable metadata while removing volatile fields.

- **[Jupyter AI](https://github.com/jupyterlab/jupyter-ai)** — Inspired the notebook-as-conversation pattern (prompting from cells), the `ExecutionContext` concept (snapshot kernel state for agents), cell metadata namespace for AI provenance tracking, and progressive disclosure (minimal/brief/full detail levels).

## License

MIT

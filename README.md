# agent-repl

**Multiplayer Jupyter notebooks — AI agents and humans collaborate in real time.**

The notebook open in VS Code/Cursor is the human-facing surface. The CLI is the agent-facing surface. Both share the same kernel, the same file, and the same state.

## How It Works

A VS Code extension runs an HTTP bridge server. The CLI talks to it. The extension handles all notebook operations — reading cells, editing, executing code, managing kernels — through VS Code's native notebook API.

```
Human (VS Code / Cursor)
    ↕
VS Code Extension (bridge server)
    ↕  HTTP / JSON
Agent (CLI)
```

The extension auto-starts when you open a `.ipynb` file and writes a connection file so the CLI can discover it.

## Quick Start

```bash
# Install the CLI
uv tool install /path/to/agent-repl

# Create a notebook and start working
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
agent-repl cat analysis.ipynb
```

The `ix` command (insert-execute) adds a cell to the notebook and runs it. The cell appears in VS Code immediately — a human watching sees it show up with its output.

## Key Features

- **Live kernel access** — Execute code, read outputs, inspect state against a running Jupyter kernel from the CLI
- **Notebook-as-conversation** — Humans create prompt cells in VS Code, agents discover and respond via CLI
- **Fire-and-forget execution** — `ix` returns immediately with a `cell_id`; execution continues in the background
- **Smart output filtering** — Rich media (HTML, images, widgets) stripped for agents; notebook file keeps everything for humans
- **Stable cell IDs** — UUID-based cell identity survives moves, deletes, and reordering
- **Hot-reload** — Update extension routes without restarting the bridge

## The Prompt Loop

The notebook becomes a bidirectional conversation channel.

**Human clicks "Ask Agent" in VS Code toolbar**, creating a prompt cell.

**Agent discovers and responds:**
```bash
agent-repl prompts demo.ipynb
agent-repl respond demo.ipynb --to <cell_id> -s 'df.dropna(inplace=True)'
```

The `respond` command atomically: marks the prompt in-progress → inserts a response cell → executes it → marks the prompt answered.

## Commands

| Command | Description |
|---------|-------------|
| `cat` | Read notebook contents (cleaned for agents) |
| `status` | Kernel state + running/queued cells |
| `exec` | Execute a cell by `--cell-id` or inline code |
| `ix` | Insert cell + execute (fire-and-forget) |
| `edit` | Cell ops: `replace-source`, `insert`, `delete`, `move`, `clear-outputs` |
| `run-all` | Execute all cells |
| `restart` | Restart kernel |
| `restart-run-all` | Restart kernel + run all |
| `new` | Create a new notebook |
| `prompts` | List prompt cells |
| `respond` | Answer a prompt cell |
| `reload` | Hot-reload extension routes |

All commands output JSON. Pass `--pretty` for formatted output.

## Installation

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl

# Dev dependency in another project
uv add --dev agent-repl --path /path/to/agent-repl
```

The VS Code extension must also be installed. Build it with `cd extension && npm run compile && npx vsce package`, then install the `.vsix`.

## Documentation

- [Getting Started](docs/getting-started.md) — End-to-end tutorial
- [Command Reference](docs/commands.md) — All 12 commands with examples
- [Prompt Loop](docs/prompt-loop.md) — Notebook-as-conversation pattern
- [Architecture](docs/architecture.md) — How the bridge works
- [Installation](docs/installation.md) — Setup guide

## Architecture

```
src/agent_repl/           # Python CLI (~640 lines)
├── cli.py                # Command parser + handlers
├── client.py             # HTTP client + bridge discovery
├── __main__.py           # Entry point
└── __init__.py

extension/src/            # VS Code Extension (~1200 lines TypeScript)
├── extension.ts          # Lifecycle + activation
├── server.ts             # HTTP server
├── routes.ts             # API endpoints
├── discovery.ts          # Connection file I/O
├── notebook/             # Cell ops, ID management, outputs
├── execution/            # Queue, kernel state
├── prompts/              # Prompt UI
└── activity/             # Status panel
```

## License

MIT

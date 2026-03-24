# agent-repl

**Multiplayer Jupyter notebooks — AI agents and humans collaborate in real time.**

`agent-repl` owns the shared notebook runtime. The CLI is the agent-facing surface. If VS Code/Cursor is open, the notebook is the human-facing projection of that same runtime. The editor can be open or closed at the start.

## How It Works

A workspace runtime serves notebook operations directly. When the VS Code extension is open, it attaches as a live projection client so humans can watch cells appear, run, and update without becoming the source of truth.

```
Human (VS Code / Cursor, optional)
    ↕
Projection Client (optional)
    ↕  HTTP / JSON
agent-repl Runtime
    ↕
Agent (CLI)
```

The runtime can be started headlessly by the CLI, so notebook create/edit/execute can work even when the editor is closed. If the extension is open, it auto-attaches to the matching workspace so humans see the same notebook state live.

## Quick Start

```bash
# Install the CLI
uv tool install /path/to/agent-repl --reinstall
# or from inside this checkout
uv tool install . --reinstall

# Create a notebook and start working
agent-repl new analysis.ipynb
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
```

The `ix` command (insert-execute) adds a cell to the notebook and runs it. It returns the result directly. If VS Code is open, a human watching sees the cell show up with its output.

Agent-triggered execution defaults to `no-yank`, which means the steady-state path stays in the background without stealing editor focus. If you explicitly want VS Code's built-in execution behavior, set `agent-repl.executionMode` to `native`.

Creating a brand-new notebook and attaching a kernel should stay in the background. `agent-repl new` and `agent-repl select-kernel` prefer the workspace `.venv` automatically when it exists, and the JSON response says which kernel was selected. If no workspace `.venv` exists, `agent-repl new` should fail clearly unless you pass `--kernel` explicitly. Use `agent-repl select-kernel ... --interactive` only when you explicitly want the VS Code kernel picker. If create or kernel attach reveals a notebook, prompts the user, or asks for a kernel restart, treat that as a product bug.

## Key Features

- **Live kernel access** — Execute code, read outputs, inspect state against a running Jupyter kernel from the CLI
- **Notebook-as-conversation** — Humans create prompt cells in VS Code, agents discover and respond via CLI
- **Optional fire-and-forget execution** — `ix` waits by default; use `--no-wait` when you intentionally want an immediate return
- **Smart output filtering** — Rich media (HTML, images, widgets) stripped for agents; notebook file keeps everything for humans
- **Stable cell IDs** — UUID-based cell identity survives moves, deletes, and reordering
- **Hot-reload** — Update extension routes without restarting the bridge
- **Workspace-scoped core authority** — Session continuity, document state, runtime ownership, and file-sync boundaries now live in one shared workspace process
- **Editor projection attach** — The extension auto-attaches the editor window to the matching shared session when the bridge starts, so editor presence becomes part of the runtime contract
- **Deterministic launcher discovery** — Auto-attach prefers an explicit CLI command and workspace-local `.venv` launchers before PATH-based resolution
- **Explicit file sync boundaries** — Registered documents track bound file snapshots, detect external changes, and require explicit rebinding instead of silently accepting disk drift

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
| `new` | Create notebook (auto-selects `.venv` kernel) |
| `kernels` | List available notebook kernels |
| `select-kernel` | Select kernel for a notebook |
| `prompts` | List prompt cells |
| `respond` | Answer a prompt cell |
| `reload` | Hot-reload extension routes |

All commands output JSON. Pass `--pretty` for formatted output.

## Installation

```bash
# Global CLI tool (recommended)
uv tool install /path/to/agent-repl --reinstall

# Or from inside this repo checkout
uv tool install . --reinstall

# Dev dependency in another project
uv add --dev agent-repl --path /path/to/agent-repl
```

Before validating from another workspace, verify the PATH-installed CLI:

```bash
agent-repl --version
agent-repl --help
```

The VS Code extension must also be installed. Build and reinstall it with:

```bash
cd extension && npm run compile && npx --yes @vscode/vsce package --allow-missing-repository -o agent-repl-0.3.0.vsix
code --install-extension extension/agent-repl-0.3.0.vsix --force
```

Or manually run `cd extension && npm run compile && npx vsce package`, then install the `.vsix`.
For the packaged auto-attach smoke check, run `cd extension && npm run test:artifact`.
Recompiling the repo alone does not update an already-installed extension under `~/.vscode/extensions/`; reinstall the `.vsix` after rebuilding, or run the repo in an Extension Development Host.

## Documentation

- [Getting Started](docs/getting-started.md) — End-to-end tutorial
- [Command Reference](docs/commands.md) — All public CLI commands
- [Prompt Loop](docs/prompt-loop.md) — Notebook-as-conversation pattern
- [Architecture](docs/architecture.md) — How the current bridge works
- [North-Star Design Docs](docs/v2/README.md) — Architecture direction, reference stack, and review rubric
- [Core Authority](docs/v2/core-authority.md) — Canonical authority, sessions, actors, and continuity
- [Runtime and Execution](docs/v2/runtime-and-execution.md) — Run ownership, runtime lifecycle, and zombie-kernel philosophy
- [File Compatibility](docs/v2/file-compatibility.md) — `.ipynb` compatibility, richer state, and external sync boundaries
- [Collaboration](docs/v2/collaboration.md) — Branching, ownership, review, and sub-notebook collaboration
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

# Installation

Get agent-repl running and connected to a Jupyter kernel.

## Prerequisites

- **Python 3.10+**
- **uv** - [Install uv](https://docs.astral.sh/uv/getting-started/installation/) if you don't have it

## Install Methods

### Global CLI tool (recommended)

Install agent-repl as a standalone CLI tool available everywhere:

```bash
uv tool install /path/to/agent-repl
```

Verify it works:

```bash
agent-repl --help
```

Output:
```
usage: agent-repl [-h] {servers,notebooks,ls,contents,cat,execute,exec,...} ...

agent-repl: CLI for AI agents to work with live Jupyter notebook kernels.
```

### Dev dependency in another project

Add agent-repl to a project where an agent needs notebook access:

```bash
uv add --dev agent-repl --path /path/to/agent-repl
```

The `agent-repl` command is then available inside that project's virtual environment.

### Direct script invocation

Run without installing anything:

```bash
uv run /path/to/agent-repl/scripts/agent_repl.py --help
```

This uses uv's inline script metadata to install dependencies into a temporary environment.

## Starting JupyterLab

agent-repl needs a running Jupyter server to connect to. The `start` command launches one with authentication disabled (for local agent use):

```bash
agent-repl start
```

Output:
```json
{"pid": 12345, "command": "jupyter lab --IdentityProvider.token='' --ServerApp.password='' --no-browser"}
```

JupyterLab runs in the background. To run in the foreground instead:

```bash
agent-repl start --foreground
```

To use a specific port:

```bash
agent-repl start --port 8899
```

Confirm the server is running:

```bash
agent-repl servers
```

Output:
```json
{"servers": [{"url": "http://localhost:8888/", "pid": 12345, "notebook_dir": "/Users/you/project"}]}
```

## Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `AGENT_REPL_PORT` | Default server port (avoids passing `-p` on every command) | `export AGENT_REPL_PORT=8899` |

When only one Jupyter server is running, agent-repl auto-selects it. Set `AGENT_REPL_PORT` when you run multiple servers and want a consistent default.

## Verifying the Full Setup

Run through the core loop to confirm everything works:

```bash
# 1. Start JupyterLab
agent-repl start

# 2. Check the server is discovered
agent-repl servers

# 3. Create a notebook with a running kernel
agent-repl new test.ipynb

# 4. Execute code against the kernel
agent-repl ix test.ipynb -s 'print("agent-repl is working")'
```

You should see `"text": "agent-repl is working\n"` in the output. The notebook also appears in JupyterLab at `http://localhost:8888/lab` if you open it in a browser.

## Next Steps

- [Overview and core loop](index.md) - Understand the full command set

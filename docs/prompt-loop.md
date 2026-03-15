# Notebook-as-Conversation

Humans create prompt cells in VS Code. Agents discover and respond via CLI. The notebook becomes a shared conversation surface where both participants work in their natural interface.

## How It Works

| Role | Interface | Action |
|------|-----------|--------|
| **Human** | VS Code / Cursor | Clicks "Ask Agent" toolbar button to create a prompt cell |
| **Agent** | CLI | Discovers the prompt, generates a response |
| **Notebook** | Shared file | Stores the conversation as executable cells |

The human never leaves VS Code. The agent never needs a GUI. Both read and write the same `.ipynb` file through the bridge.

## Step by Step

### 1. Human creates a prompt

In VS Code, the human clicks the "Ask Agent" button in the notebook toolbar. This creates a markdown cell with `agent-repl` metadata:

```json
{
  "metadata": {
    "custom": {
      "agent-repl": {
        "cell_id": "abc123",
        "type": "prompt",
        "status": "pending"
      }
    }
  }
}
```

The cell's source text is the instruction for the agent.

### 2. Agent lists pending prompts

The agent checks the notebook for unresolved prompts:

```bash
agent-repl prompts demo.ipynb
```

```json
{
  "prompts": [
    {
      "cell_id": "abc123",
      "index": 3,
      "cell_type": "markdown",
      "source": "clean this dataframe — drop nulls, normalize column names",
      "metadata": {
        "custom": {
          "agent-repl": {
            "cell_id": "abc123",
            "type": "prompt",
            "status": "pending"
          }
        }
      }
    }
  ]
}
```

### 3. Agent responds

The agent inserts a response cell and executes it:

```bash
agent-repl respond demo.ipynb --to abc123 \
  -s 'df = df.dropna()
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
print(f"Shape: {df.shape}")'
```

The `respond` command does three things atomically:

1. Marks the prompt as `in-progress` via the bridge API
2. Inserts a response cell and executes it (via `insert-and-execute`)
3. Marks the prompt as `answered`

The response cell appears in VS Code right after the prompt cell.

## Prompt Status

Prompts move through three states:

| Status | Meaning |
|--------|---------|
| `pending` | Waiting for an agent response |
| `in-progress` | Agent is generating/executing a response |
| `answered` | Response cell has been inserted and executed |

The `prompts` command returns all cells with prompt metadata. Filter by status in your agent logic.

## Building an Agent Loop

A simple polling loop that watches for prompts and responds:

```python
import json
import subprocess
import time

NOTEBOOK = "analysis.ipynb"

def run(*args):
    result = subprocess.run(["agent-repl", *args], capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else {}

while True:
    data = run("prompts", NOTEBOOK)
    for prompt in data.get("prompts", []):
        meta = prompt.get("metadata", {}).get("custom", {}).get("agent-repl", {})
        if meta.get("status") != "pending":
            continue

        cell_id = meta["cell_id"]
        instruction = prompt["source"]

        # Generate response code (replace with your LLM)
        code = generate_code(instruction)

        run("respond", NOTEBOOK, "--to", cell_id, "-s", code)

    time.sleep(2)
```

## Next Steps

- [Command Reference](commands.md) — Full details on `prompts` and `respond`
- [Getting Started](getting-started.md) — End-to-end tutorial

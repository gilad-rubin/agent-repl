# Notebook-as-Conversation

Human writes prompt cells in JupyterLab. Agent discovers and responds via CLI. The notebook becomes a shared conversation surface where both participants work in their natural interface.

## How It Works

The prompt loop has three roles:

| Role | Interface | Action |
|------|-----------|--------|
| **Human** | JupyterLab | Writes a cell with `#| agent:` directive |
| **Agent** | CLI | Discovers the prompt, generates a response |
| **Notebook** | Shared file | Stores the conversation as executable cells |

The human never leaves JupyterLab. The agent never needs a browser. Both read and write the same `.ipynb` file.

## Step by Step

### 1. Human writes a prompt cell

In JupyterLab, the human creates a code cell with an `#| agent:` directive at the top:

```python
#| agent: clean this dataframe — drop nulls, normalize column names
df = pd.read_csv("sales.csv")
df.head()
```

Or a markdown cell with an HTML comment:

```markdown
<!-- agent: create a visualization of sales by region -->
```

The directive is the instruction. Any code below it is context the agent can read.

### 2. Agent lists pending prompts

The agent polls the notebook for unresolved prompts:

```bash
agent-repl prompts demo.ipynb
```

Output:

```json
{
  "prompts": [
    {
      "cell_id": "abc123",
      "index": 3,
      "instruction": "clean this dataframe — drop nulls, normalize column names",
      "cell_source": "#| agent: clean this dataframe ...\ndf = pd.read_csv(\"sales.csv\")\ndf.head()",
      "status": "pending",
      "context_above": [{"index": 2, "cell_type": "code", "source_preview": "import pandas as pd\n..."}],
      "context_below": []
    }
  ]
}
```

Each prompt includes the instruction, the full cell source, and surrounding context cells so the agent understands what came before and after.

### 3. Agent responds

The agent inserts a response cell directly after the prompt and executes it:

```bash
agent-repl respond demo.ipynb --to abc123 \
  -s 'df = df.dropna()
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
print(f"Shape: {df.shape}")
print(df.head())'
```

Output:

```json
{
  "operation": "respond",
  "prompt": {"cell_id": "abc123", "index": 3, "instruction": "clean this dataframe ..."},
  "insert": {"path": "demo.ipynb", "operation": "insert-cell", "cell_id": "def456", "index": 4},
  "execute": {"status": "ok", "events": [{"type": "stream", "name": "stdout", "text": "Shape: (1420, 8)\n..."}]}
}
```

The response cell is linked to the prompt via metadata (`responds_to: abc123`). This link is how `prompts` knows a prompt has been answered -- it checks whether the next cell carries that metadata.

## Watching for Prompts

For continuous monitoring, use `watch`. It polls the notebook and emits new prompts as JSONL:

```bash
agent-repl watch demo.ipynb
```

Each time the human saves a new prompt cell, a line appears on stdout:

```jsonl
{"cell_id":"abc123","index":3,"instruction":"clean this dataframe ...","status":"pending",...}
{"cell_id":"ghi789","index":6,"instruction":"plot sales by region","status":"pending",...}
```

`watch` tracks which prompts it has already emitted (by cell ID) and only outputs new ones.

### Flags

| Flag | Default | Effect |
|------|---------|--------|
| `--interval` | `2.0` | Seconds between polls |
| `--once` | off | Check once and exit (no loop) |
| `--context` | `1` | Number of context cells above/below each prompt |

### Piping to an agent loop

`watch` outputs one JSON object per line, making it straightforward to pipe into a processing loop:

```bash
agent-repl watch demo.ipynb | while IFS= read -r prompt; do
  cell_id=$(echo "$prompt" | jq -r .cell_id)
  instruction=$(echo "$prompt" | jq -r .instruction)

  # Your agent generates code from the instruction
  code=$(your-agent-generate "$instruction")

  agent-repl respond demo.ipynb --to "$cell_id" -s "$code"
done
```

The human writes prompts at their own pace in JupyterLab. The agent watches, responds, and the response appears in the notebook within seconds.

## Response Metadata

When `respond` inserts a cell, it attaches metadata under the `agent-repl` namespace:

```json
{
  "metadata": {
    "agent-repl": {
      "responds_to": "abc123",
      "type": "response",
      "timestamp": 1710345600.0
    }
  }
}
```

This metadata serves two purposes:

1. **Prompt resolution** -- `prompts` checks whether the cell immediately after a prompt has `responds_to` pointing to that prompt's cell ID. If so, the prompt's status is `"answered"`.
2. **Provenance** -- the notebook records which cells were human-authored and which were agent-generated.

Use `prompts --all` to see both pending and answered prompts:

```bash
agent-repl prompts demo.ipynb --all
```

## Example Walkthrough

A data scientist opens `analysis.ipynb` in JupyterLab and starts exploring a dataset.

**Cell 0** (human runs manually):

```python
import pandas as pd
df = pd.read_csv("sales.csv")
df.head()
```

**Cell 1** (human writes, does not run):

```python
#| agent: clean this dataframe — drop nulls, normalize column names, convert date column to datetime
df.head()
```

Meanwhile, the agent is watching:

```bash
agent-repl watch analysis.ipynb
```

The agent receives the prompt, reads the instruction and context, and responds:

```bash
agent-repl respond analysis.ipynb --to <cell_id> \
  -s 'df = df.dropna()
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
df["date"] = pd.to_datetime(df["date"])
print(f"Shape after cleaning: {df.shape}")
print(df.dtypes)'
```

The response cell appears at index 2 in the notebook. The human sees it in JupyterLab, reviews the output, and writes the next prompt:

**Cell 3** (human writes):

```python
#| agent: group by region and plot monthly revenue as a line chart
```

The watch loop picks it up, the agent responds, and the conversation continues. Each prompt-response pair is a self-contained, executable record of what was asked and what was done.

## Next Steps

- **Cell directives reference**: [Cell Directives](cell-directives.md) -- full syntax and available directives
- **Execution context**: Use `agent-repl context` to get a full snapshot of kernel state alongside prompts
- **Streaming**: Use `agent-repl exec --stream` for real-time output on long-running cells

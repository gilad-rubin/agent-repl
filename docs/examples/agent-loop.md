# Example: Automated Agent Loop

Build a loop where a human writes prompts in JupyterLab and an agent automatically detects, processes, and responds to them.

**Prerequisites**: [Quick Start](../quick-start.md), [Data Analysis Example](data-analysis.md)

## How It Works

The human-agent loop uses three commands:

1. **`watch`** -- polls the notebook for new `#| agent:` prompt cells, emitting JSONL
2. Your script pipes each prompt to an LLM (or any code generator)
3. **`respond`** -- inserts a response cell after the prompt and executes it

```
Human writes in JupyterLab          Agent runs in terminal
┌─────────────────────┐             ┌──────────────────────┐
│ #| agent: clean     │  ──watch──> │ detect new prompt    │
│ #| this dataframe   │             │ call LLM with prompt │
│ df = pd.read_csv()  │  <─respond─ │ insert + execute     │
└─────────────────────┘             └──────────────────────┘
```

## Writing Prompts

In JupyterLab, the human writes a code cell with the `#| agent:` directive:

```python
#| agent: clean this dataframe — drop nulls, normalize column names
df = pd.read_csv("sales.csv")
df.head()
```

Or a markdown cell with an HTML comment:

```markdown
<!-- agent: create a bar chart of revenue by region -->
```

Both formats are detected by `watch` and `prompts`.

## Watching for Prompts

`watch` polls the notebook and emits one JSONL line per new prompt:

```bash
agent-repl watch analysis.ipynb
```

```jsonl
{"cell_id":"a1b2c3d4","cell_source":"#| agent: clean this dataframe — drop nulls, normalize column names\ndf = pd.read_csv(\"sales.csv\")\ndf.head()","context_above":[],"context_below":[],"index":0,"instruction":"clean this dataframe — drop nulls, normalize column names","status":"pending"}
```

Key fields in each prompt event:

| Field | Description |
|-------|-------------|
| `cell_id` | Unique cell identifier, used with `respond --to` |
| `instruction` | The text after `#\| agent:` |
| `cell_source` | Full cell source, including the directive and any existing code |
| `context_above` | Brief summaries of cells above the prompt |
| `context_below` | Brief summaries of cells below the prompt |

Use `--once` to check once and exit (no polling):

```bash
agent-repl watch analysis.ipynb --once
```

## Responding to a Prompt

After generating code (manually or via an LLM), insert it as a response:

```bash
agent-repl respond analysis.ipynb \
  --to a1b2c3d4 \
  -s 'df = df.dropna()
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
print(f"Cleaned: {df.shape}")
df.head()'
```

```json
{
  "operation": "respond",
  "prompt": {
    "cell_id": "a1b2c3d4",
    "index": 0,
    "instruction": "clean this dataframe \u2014 drop nulls, normalize column names"
  },
  "insert": {
    "path": "analysis.ipynb",
    "operation": "insert-cell",
    "changed": true,
    "cell_id": "e5f6g7h8",
    "cell_count": 2
  },
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Cleaned: (1241, 8)\n"},
      {"type": "execute_result", "data": {"text/plain": "         date region   product  units  revenue   cost customer_id notes\n0  2024-01-03   West  Widget A     12   143.88  86.33       C-401      \n1  2024-01-03   East  Widget B      7    98.63  59.18       C-118      \n2  2024-01-05  South  Widget A      3    35.97  21.58       C-227      "}}
    ],
    "outputs_saved": true
  }
}
```

The response cell is linked to the prompt via metadata. The next `watch` call will see this prompt as "answered" and skip it.

## Bash Script: Minimal Agent Loop

A simple bash loop that pipes prompts to an LLM and responds:

```bash
#!/usr/bin/env bash
# agent-loop.sh — Watch a notebook and respond to prompts with an LLM
set -euo pipefail

NOTEBOOK="${1:?Usage: agent-loop.sh <notebook.ipynb>}"
export AGENT_REPL_PORT="${AGENT_REPL_PORT:-8888}"

echo "Watching $NOTEBOOK for prompts..."

agent-repl watch "$NOTEBOOK" | while IFS= read -r prompt; do
    cell_id=$(echo "$prompt" | jq -r '.cell_id')
    instruction=$(echo "$prompt" | jq -r '.instruction')
    cell_source=$(echo "$prompt" | jq -r '.cell_source')

    echo "--- Prompt [$cell_id]: $instruction"

    # Build LLM prompt with instruction and cell context
    llm_response=$(cat <<EOF | llm -m claude-sonnet  # or any CLI LLM tool
You are a data analysis assistant. The user wrote a Jupyter cell with this instruction:

Instruction: $instruction

Cell source:
$cell_source

Respond with ONLY Python code. No markdown, no explanation.
EOF
    )

    echo "--- Responding with ${#llm_response} chars of code"

    # Insert and execute the response
    agent-repl respond "$NOTEBOOK" --to "$cell_id" -s "$llm_response"
done
```

Run it:

```bash
chmod +x agent-loop.sh
./agent-loop.sh analysis.ipynb
```

The script blocks on `watch`, processing each prompt as it appears. The human writes prompts in JupyterLab and sees responses appear in real time.

## Python Script: Agent Loop with Error Handling

A more robust version in Python with retry logic and context awareness:

```python
#!/usr/bin/env python3
"""agent_loop.py — Automated agent loop for agent-repl notebooks."""
import json
import subprocess
import sys


def run_agent_repl(*args: str) -> dict:
    """Run an agent-repl command and return parsed JSON."""
    result = subprocess.run(
        ["agent-repl", *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        error = result.stderr.strip()
        raise RuntimeError(f"agent-repl failed: {error}")
    return json.loads(result.stdout)


def generate_code(instruction: str, cell_source: str, context: dict) -> str:
    """Call your LLM to generate response code.

    Replace this with your actual LLM integration:
    OpenAI, Anthropic, local model, etc.
    """
    # Example: call the Anthropic API
    import anthropic

    client = anthropic.Anthropic()
    variables = context.get("variables", [])
    var_summary = ", ".join(
        f"{v['name']} ({v['type']})" for v in variables[:10]
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                f"You are a data analysis assistant working in a Jupyter notebook.\n\n"
                f"Kernel variables: {var_summary}\n\n"
                f"The user wrote this cell:\n```python\n{cell_source}\n```\n\n"
                f"Instruction: {instruction}\n\n"
                f"Respond with ONLY executable Python code. No markdown fences."
            ),
        }],
    )
    return message.content[0].text


def process_prompt(notebook: str, prompt: dict) -> None:
    """Handle a single prompt: get context, generate code, respond."""
    cell_id = prompt["cell_id"]
    instruction = prompt["instruction"]
    cell_source = prompt["cell_source"]

    print(f"  Prompt [{cell_id}]: {instruction}")

    # Get kernel context for better LLM responses
    context = run_agent_repl("context", notebook)

    # Generate and insert the response
    code = generate_code(instruction, cell_source, context)
    print(f"  Generated {len(code)} chars of code")

    result = run_agent_repl(
        "respond", notebook, "--to", cell_id, "-s", code,
    )

    status = (result.get("execute") or {}).get("status", "unknown")
    print(f"  Execution: {status}")

    if status != "ok":
        events = (result.get("execute") or {}).get("events", [])
        errors = [e for e in events if e.get("type") == "error"]
        for err in errors:
            print(f"  Error: {err.get('ename')}: {err.get('evalue')}")


def main() -> None:
    notebook = sys.argv[1] if len(sys.argv) > 1 else "analysis.ipynb"
    print(f"Watching {notebook} for prompts...")

    proc = subprocess.Popen(
        ["agent-repl", "watch", notebook],
        stdout=subprocess.PIPE, text=True,
    )

    try:
        for line in proc.stdout:
            prompt = json.loads(line.strip())
            process_prompt(notebook, prompt)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        proc.terminate()


if __name__ == "__main__":
    main()
```

Run it:

```bash
export AGENT_REPL_PORT=8888
python agent_loop.py analysis.ipynb
```

```
Watching analysis.ipynb for prompts...
  Prompt [a1b2c3d4]: clean this dataframe — drop nulls, normalize column names
  Generated 142 chars of code
  Execution: ok
  Prompt [f9e8d7c6]: create a scatter plot of revenue vs margin
  Generated 298 chars of code
  Execution: ok
```

## Key Differences: `watch` vs `prompts`

| Command | Behavior | Use case |
|---------|----------|----------|
| `prompts` | One-shot scan, returns all prompts | Check current state |
| `watch` | Long-running poll, emits new prompts as JSONL | Automated loop |
| `watch --once` | Single poll, emits pending prompts, exits | Cron jobs, CI |

## Tips

- **Context matters**: Use `agent-repl context` before generating code. The variable list and cell summaries help the LLM write better code.
- **Error recovery**: If a response cell errors, the human can edit the prompt and write a new `#| agent:` directive. The old response stays in the notebook for reference.
- **Polling interval**: `watch` defaults to 2-second polls. Use `--interval 5` for less frequent checks on large notebooks.
- **Source files**: Pass code from a file with `--source-file response.py` instead of inline `-s` for long responses.

## Next Steps

- **Data analysis workflow**: [Data Analysis](data-analysis.md) -- see the full analysis loop in action
- **Multiple notebooks**: [Multi-Notebook Workflows](multi-notebook.md) -- coordinate prompts across notebooks

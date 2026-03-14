# Cell Directives

Cell directives are structured comments that give agent-repl metadata about a cell. They control prompting, execution filtering, and tagging -- all without changing what the cell does when run normally.

## Syntax

### Code cells

Directives go at the top of the cell, before any code. Each directive is a line starting with `#|`:

```python
#| agent: summarize this dataframe
#| agent-tags: data-prep, critical
df.describe()
```

Two forms:

| Form | Syntax | Example |
|------|--------|---------|
| **Key-value** | `#| key: value` | `#| agent: clean this data` |
| **Flag** | `#| key` | `#| agent-skip` |

Directives must appear before the first non-directive, non-empty line. Once the parser hits a line that does not start with `#|`, it stops looking.

### Markdown cells

Only the `agent` directive is supported in markdown, using an HTML comment:

```markdown
<!-- agent: create a summary table of the key findings -->

This section covers the analysis results.
```

The comment can appear anywhere in the cell.

## Available Directives

### `agent`

Marks a cell as a prompt for an AI agent. The value is the instruction.

```python
#| agent: normalize column names and drop rows where revenue is null
df = pd.read_csv("sales.csv")
```

The agent sees both the instruction and the code below it as context. Use `prompts` to discover these cells and `respond` to answer them. See [Notebook-as-Conversation](prompt-loop.md) for the full workflow.

In markdown cells:

```markdown
<!-- agent: write a function that calculates moving averages -->
```

### `agent-tags`

Assigns tags to a cell for filtering during `run-all`. Tags are comma-separated:

```python
#| agent-tags: setup, slow
import torch
model = load_model("bert-base")
```

Tags have no effect during normal cell execution. They only matter when running `run-all` or `restart-run-all` with `--skip-tags` or `--only-tags`.

### `agent-skip`

A flag (no value) that tells `run-all` to skip this cell entirely:

```python
#| agent-skip
# This cell is for interactive exploration only
df.sample(10)
```

Cells with `agent-skip` are skipped regardless of any tag filters. The skip reason appears in the `run-all` output as `"reason": "agent-skip"`.

## Tag Filtering with `run-all`

Tags give you selective execution. Two flags control which cells run:

### `--skip-tags`: exclude cells by tag

```bash
agent-repl run-all demo.ipynb --skip-tags setup,expensive
```

Any cell tagged with `setup` or `expensive` is skipped. Untagged cells run normally.

### `--only-tags`: include only cells with matching tags

```bash
agent-repl run-all demo.ipynb --only-tags critical
```

Only cells tagged `critical` run. Untagged cells are skipped (reason: `"not-in-only-tags"`).

### How the filters interact

The evaluation order is:

1. `agent-skip` -- always skipped, no matter what
2. `--only-tags` -- if set, cell must have at least one matching tag to run
3. `--skip-tags` -- if the cell has any matching tag, it is skipped

This means `agent-skip` takes priority over both tag filters.

### Practical example

A notebook with four cells:

```python
# Cell 0
#| agent-tags: setup
import pandas as pd
```

```python
# Cell 1
#| agent-tags: critical
df = pd.read_csv("data.csv")
```

```python
# Cell 2
#| agent-skip
df.sample(5)  # interactive exploration
```

```python
# Cell 3
#| agent-tags: critical, analysis
summary = df.describe()
print(summary)
```

| Command | Cells executed | Cells skipped |
|---------|---------------|---------------|
| `run-all demo.ipynb` | 0, 1, 3 | 2 (agent-skip) |
| `run-all demo.ipynb --skip-tags setup` | 1, 3 | 0 (in-skip-tags), 2 (agent-skip) |
| `run-all demo.ipynb --only-tags critical` | 1, 3 | 0 (not-in-only-tags), 2 (agent-skip) |
| `run-all demo.ipynb --only-tags analysis` | 3 | 0 (not-in-only-tags), 1 (not-in-only-tags), 2 (agent-skip) |

Both `--skip-tags` and `--only-tags` work identically with `restart-run-all`.

## Directives and Prompts Together

A cell can have both a prompt directive and tags:

```python
#| agent: optimize this query — it takes 30 seconds on the full dataset
#| agent-tags: performance, critical
results = db.execute("SELECT * FROM orders WHERE ...")
```

The `agent` directive makes the cell discoverable via `prompts`. The `agent-tags` control whether `run-all` executes it. These are independent -- tagging a prompt cell does not affect whether it shows up in `prompts`.

## Parsing Rules

- Directives are parsed from the cell source text, not from cell metadata
- Only lines at the top of the cell (before any code) are checked
- Empty lines between directives are allowed
- The same key can appear multiple times (values are collected into a list)
- Directive parsing is the same for `code` and `raw` cell types
- Markdown cells only support the `<!-- agent: ... -->` syntax

## Next Steps

- **Prompt workflow**: [Notebook-as-Conversation](prompt-loop.md) -- how agents discover and respond to prompts
- **Execution**: `agent-repl run-all --help` for the full flag reference
- **Verification**: Use `restart-run-all --save-outputs` to verify a notebook runs cleanly from scratch

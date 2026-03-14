# Output Filtering

agent-repl maintains two surfaces: the notebook file (for humans in JupyterLab) and CLI output (for agents). The notebook keeps full rich outputs -- images, HTML, widgets. The CLI strips those down to text that agents can reason about.

## Two Surfaces, One Notebook

When you execute code, results are saved to the notebook with all MIME types intact. When an agent reads those results via `cat` or `exec`, rich media is replaced with compact placeholders.

The notebook file always has the full data. Filtering only affects what the CLI returns.

```bash
agent-repl exec analysis.ipynb -c 'df.plot(kind="bar")'
```

What the agent sees:

```json
{
  "outputs": [
    {
      "data": {
        "text/plain": "<Axes>",
        "image/png": "[image: image/png]"
      }
    }
  ]
}
```

What stays in the notebook file:

```json
{
  "outputs": [
    {
      "data": {
        "text/plain": "<Axes>",
        "image/png": "iVBORw0KGgoAAAANSUhEUg..."
      }
    }
  ]
}
```

The agent gets a placeholder (`[image: image/png]`). The notebook keeps the full base64-encoded image.

## What Gets Stripped

Three rules govern CLI filtering:

| Output Type | Rule | Agent Sees |
|-------------|------|------------|
| HTML when text/plain exists | Dropped entirely | Only the text/plain value |
| Images (PNG, JPEG, SVG) | Replaced with placeholder | `[image: image/png]` |
| Jupyter widgets | Replaced with placeholder | `[widget]` |

HTML is only dropped when a `text/plain` alternative exists in the same MIME bundle. If HTML is the only representation, it passes through.

## What Gets Normalized on Save

Every time agent-repl saves a notebook, outputs are cleaned automatically. This happens regardless of filtering -- it applies to the notebook file itself.

| Artifact | Before | After |
|----------|--------|-------|
| ANSI escape codes | `\x1b[31mError\x1b[0m` | `Error` |
| Object repr addresses | `<MyObj at 0x7f3b2c>` | `<MyObj>` |
| Colab metadata | `application/vnd.google.colaboratory.intrinsic+json` key present | Key removed |
| Trailing whitespace in base64 images | `iVBORw0KGgo...\n` | `iVBORw0KGgo...` |

Normalization is idempotent -- running it twice produces the same result. This keeps notebook diffs clean even when outputs are preserved.

## Disabling Filtering with `--raw-output`

Pass `--raw-output` to any command that returns outputs. The CLI will return the full MIME bundles, including HTML, raw image data, and widget state.

```bash
# Default: filtered for agents
agent-repl cat analysis.ipynb --detail full

# Raw: full MIME bundles, nothing stripped
agent-repl cat analysis.ipynb --detail full --raw-output
```

This flag affects `cat`, `exec`, `ix`, `run-all`, `restart-run-all`, `respond`, and streaming execution.

## Progressive Detail with `--detail`

The `--detail` flag on `cat` controls how much cell content is returned. This is independent of output filtering -- it controls the shape of the response.

| Level | Source | Outputs | Use When |
|-------|--------|---------|----------|
| `minimal` | Line count only | None | Scanning notebook structure |
| `brief` (default) | First 3 lines | None | Orienting within a notebook |
| `full` | Complete source | Full outputs (filtered) | Reading cell results |

```bash
# Quick scan: what cells exist?
agent-repl cat analysis.ipynb --detail minimal
```

```json
{"index": 0, "cell_id": "abc123", "cell_type": "code", "line_count": 5}
{"index": 1, "cell_id": "def456", "cell_type": "markdown", "line_count": 2}
```

```bash
# Default: source preview, no outputs
agent-repl cat analysis.ipynb
```

```json
{"index": 0, "cell_id": "abc123", "cell_type": "code", "source_preview": "import pandas as pd\ndf = pd.read_csv('data.csv')\ndf.head()\n..."}
```

```bash
# Full: complete source + filtered outputs
agent-repl cat analysis.ipynb --detail full
```

```json
{"index": 0, "cell_id": "abc123", "cell_type": "code", "source": "import pandas as pd\ndf = pd.read_csv('data.csv')\ndf.head()", "outputs": [{"data": {"text/plain": "   col_a  col_b\n0      1      2"}}]}
```

The progression lets agents start cheap (minimal), then drill into specific cells when needed.

## Next Steps

- **Git-clean notebooks**: [Git Integration](git-integration.md) -- strip outputs entirely for version control
- **Execute code**: See `agent-repl exec --help` for execution options
- **Read the SKILL.md**: Full command reference in the project root

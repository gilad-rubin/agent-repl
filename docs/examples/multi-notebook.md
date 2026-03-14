# Example: Multi-Notebook Workflows

Coordinate work across multiple notebooks: a data preparation notebook, an analysis notebook, and a reporting notebook. Use `context` to understand state, streaming for long computations, and `git-setup` for version control.

**Prerequisites**: [Quick Start](../quick-start.md), [Data Analysis Example](data-analysis.md)

## Setup: Default Server

Set `AGENT_REPL_PORT` once so every command targets the same server:

```bash
export AGENT_REPL_PORT=8888
agent-repl servers
```

```json
{
  "servers": [
    {"url": "http://localhost:8888/", "token": "", "pid": 48201, "notebook_dir": "/Users/analyst/project"}
  ]
}
```

No `-p` needed for the rest of this session.

## Create the Notebooks

Three notebooks, each with its own kernel and responsibility:

```bash
agent-repl new prep.ipynb
agent-repl new analysis.ipynb
agent-repl new report.ipynb
```

```json
{"operation": "create-notebook", "path": "prep.ipynb", "kernel_name": "python3", "kernel_started": true, "cell_count": 0}
{"operation": "create-notebook", "path": "analysis.ipynb", "kernel_name": "python3", "kernel_started": true, "cell_count": 0}
{"operation": "create-notebook", "path": "report.ipynb", "kernel_name": "python3", "kernel_started": true, "cell_count": 0}
```

Verify all three are running:

```bash
agent-repl ls
```

```json
{
  "notebooks": [
    {"path": "prep.ipynb", "name": "prep.ipynb", "kernel_id": "k-prep-001", "kernel_name": "python3", "execution_state": "idle"},
    {"path": "analysis.ipynb", "name": "analysis.ipynb", "kernel_id": "k-analysis-002", "kernel_name": "python3", "execution_state": "idle"},
    {"path": "report.ipynb", "name": "report.ipynb", "kernel_id": "k-report-003", "kernel_name": "python3", "execution_state": "idle"}
  ]
}
```

Three separate kernels, three isolated namespaces. Data passes between notebooks via files.

## Notebook 1: Data Preparation

Build the prep pipeline. Tag the setup cell so it can be skipped during re-runs:

```bash
agent-repl ix prep.ipynb -s '
#| agent-tags: setup
import pandas as pd
import json
from pathlib import Path
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "prep-001", "cell_count": 1},
  "execute": {"status": "ok", "events": [], "outputs_saved": true}
}
```

Load and clean the raw data:

```bash
agent-repl ix prep.ipynb -s '
raw = pd.read_csv("raw_sales.csv")
print(f"Raw: {raw.shape[0]} rows, {raw.isnull().sum().sum()} total nulls")

# Clean
df = raw.dropna(subset=["revenue", "date"])
df["date"] = pd.to_datetime(df["date"])
df["profit"] = df["revenue"] - df["cost"]
df.columns = [c.strip().lower() for c in df.columns]

print(f"Clean: {df.shape[0]} rows, {df.isnull().sum().sum()} nulls")
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "prep-002", "cell_count": 2},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Raw: 15832 rows, 247 total nulls\nClean: 15614 rows, 0 nulls\n"}
    ],
    "outputs_saved": true
  }
}
```

Save the cleaned data for downstream notebooks:

```bash
agent-repl ix prep.ipynb -s '
output_path = Path("clean_sales.parquet")
df.to_parquet(output_path, index=False)
print(f"Saved {len(df)} rows to {output_path}")
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "prep-003", "cell_count": 3},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Saved 15614 rows to clean_sales.parquet\n"}
    ],
    "outputs_saved": true
  }
}
```

## Use Context Before Acting

Before building the analysis notebook, check what state each notebook is in with `context`. This is the single call an agent makes to orient itself:

```bash
agent-repl context prep.ipynb
```

```json
{
  "notebook": {
    "path": "prep.ipynb",
    "cell_count": 3,
    "cells": [
      {"index": 0, "cell_id": "prep-001", "cell_type": "code", "source_preview": "#| agent-tags: setup\nimport pandas as pd\nimport json\n..."},
      {"index": 1, "cell_id": "prep-002", "cell_type": "code", "source_preview": "raw = pd.read_csv(\"raw_sales.csv\")\nprint(f\"Raw: {raw.shape[0]} rows, ...\n..."},
      {"index": 2, "cell_id": "prep-003", "cell_type": "code", "source_preview": "output_path = Path(\"clean_sales.parquet\")\ndf.to_parquet(output_path, index=False)\n..."}
    ]
  },
  "pending_prompts": [],
  "kernel": {
    "id": "k-prep-001",
    "name": "python3",
    "execution_state": "idle"
  },
  "variables": [
    {"name": "df", "type": "DataFrame", "module": "pandas.core.frame"},
    {"name": "raw", "type": "DataFrame", "module": "pandas.core.frame"},
    {"name": "output_path", "type": "PosixPath", "module": "pathlib"}
  ]
}
```

One call returns: cell summaries, variable list, kernel state, and pending prompts. An agent uses this to understand what happened and decide what to do next.

## Notebook 2: Analysis with Streaming

Load the cleaned data in the analysis notebook:

```bash
agent-repl ix analysis.ipynb -s '
import pandas as pd
df = pd.read_parquet("clean_sales.parquet")
print(f"Loaded: {df.shape}")
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "ana-001", "cell_count": 1},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Loaded: (15614, 7)\n"}
    ],
    "outputs_saved": true
  }
}
```

For long-running computations, use `--stream` to get real-time JSONL output instead of waiting for the full result:

```bash
agent-repl exec analysis.ipynb --stream -c '
import time

regions = df["region"].unique()
results = {}

for region in regions:
    subset = df[df["region"] == region]
    # Simulate expensive computation
    time.sleep(1)
    results[region] = {
        "revenue": float(subset["revenue"].sum()),
        "profit": float(subset["profit"].sum()),
        "margin": round(float(subset["profit"].sum() / subset["revenue"].sum() * 100), 1),
    }
    print(f"Done: {region} — revenue={results[region][\"revenue\"]:,.0f}")

print(f"\nAll {len(regions)} regions computed.")
'
```

Events stream as they happen, each with an elapsed timestamp:

```jsonl
{"elapsed":1.03,"name":"stdout","text":"Done: West — revenue=1,247,832\n","type":"stream"}
{"elapsed":2.05,"name":"stdout","text":"Done: East — revenue=982,441\n","type":"stream"}
{"elapsed":3.08,"name":"stdout","text":"Done: South — revenue=743,219\n","type":"stream"}
{"elapsed":4.10,"name":"stdout","text":"Done: North — revenue=1,102,556\n","type":"stream"}
{"elapsed":4.11,"name":"stdout","text":"\nAll 4 regions computed.\n","type":"stream"}
{"elapsed":4.12,"data":{"text/plain":""},"type":"execute_result"}
```

Streaming is useful for two reasons:
- The agent sees intermediate progress and can report it
- Long-running cells do not time out silently -- you see output as it arrives

## Save Streaming Results to the Notebook

Streaming does not auto-save outputs (unlike `ix`). To persist the results, re-run the cell with `exec` and `--save-outputs`:

```bash
agent-repl ix analysis.ipynb -s '
import json
with open("regional_results.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"Saved results for {len(results)} regions")
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "ana-002", "cell_count": 2},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Saved results for 4 regions\n"}
    ],
    "outputs_saved": true
  }
}
```

## Notebook 3: Reporting

The reporting notebook consumes outputs from both upstream notebooks:

```bash
agent-repl ix report.ipynb -s '
import pandas as pd
import json

df = pd.read_parquet("clean_sales.parquet")
with open("regional_results.json") as f:
    regional = json.load(f)

print(f"Data: {len(df)} rows")
print(f"Regions: {list(regional.keys())}")
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "rpt-001", "cell_count": 1},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Data: 15614 rows\nRegions: ['West', 'East', 'South', 'North']\n"}
    ],
    "outputs_saved": true
  }
}
```

Generate a summary table:

```bash
agent-repl ix report.ipynb -s '
summary = pd.DataFrame(regional).T
summary.index.name = "region"
summary = summary.sort_values("revenue", ascending=False)
print(summary.to_string())
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "rpt-002", "cell_count": 2},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "          revenue      profit  margin\nregion                                \nWest   1247832.0    497213.0    39.8\nNorth  1102556.0    421876.0    38.3\nEast    982441.0    389542.0    39.7\nSouth   743219.0    298412.0    40.2\n"}
    ],
    "outputs_saved": true
  }
}
```

## Version Control with git-setup

Configure git filters so notebooks commit cleanly (outputs stripped, metadata normalized):

```bash
agent-repl git-setup
```

```json
{
  "gitattributes": ".gitattributes",
  "git_config_clean": "git config filter.agent-repl-clean.clean 'agent-repl clean %f'",
  "git_config_smudge": "git config filter.agent-repl-clean.smudge cat"
}
```

Run the git config commands it outputs:

```bash
git config filter.agent-repl-clean.clean 'agent-repl clean %f'
git config filter.agent-repl-clean.smudge cat
```

Now every `git add *.ipynb` automatically strips outputs and volatile metadata. Diffs show only source changes:

```bash
agent-repl clean prep.ipynb | head -20
```

```json
{
 "cells": [
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": "#| agent-tags: setup\nimport pandas as pd\nimport json\nfrom pathlib import Path"
  },
  ...
 ]
}
```

Outputs gone, execution counts nulled, metadata cleaned. The `.gitattributes` filter applies this on every commit automatically.

## Verify All Notebooks

Run-all each notebook to confirm they are all reproducible:

```bash
agent-repl restart-run-all prep.ipynb --save-outputs
agent-repl restart-run-all analysis.ipynb --save-outputs
agent-repl restart-run-all report.ipynb --save-outputs
```

Check status from each result:

```json
{"operation": "restart-run-all", "run_all": {"status": "ok", "path": "prep.ipynb", "executed_cell_count": 3, "skipped_cell_count": 0, "failed_cell": null}}
{"operation": "restart-run-all", "run_all": {"status": "ok", "path": "analysis.ipynb", "executed_cell_count": 2, "skipped_cell_count": 0, "failed_cell": null}}
{"operation": "restart-run-all", "run_all": {"status": "ok", "path": "report.ipynb", "executed_cell_count": 2, "skipped_cell_count": 0, "failed_cell": null}}
```

All three notebooks pass. The pipeline is reproducible.

## Skip Tags for Faster Re-Runs

Tag expensive setup cells and skip them during development:

```bash
agent-repl run-all prep.ipynb --skip-tags setup --save-outputs
```

```json
{
  "operation": "run-all",
  "status": "ok",
  "executed_cell_count": 2,
  "skipped_cell_count": 1,
  "cells": [
    {"index": 0, "cell_id": "prep-001", "status": "skipped", "reason": "in-skip-tags", "source_preview": "#| agent-tags: setup import pandas as pd ..."},
    {"index": 1, "cell_id": "prep-002", "status": "ok", "source_preview": "raw = pd.read_csv(\"raw_sales.csv\") ..."},
    {"index": 2, "cell_id": "prep-003", "status": "ok", "source_preview": "output_path = Path(\"clean_sales.parquet\") ..."}
  ]
}
```

Cell 0 was skipped because it has the `setup` tag. The existing imports in the kernel from the previous run are still available.

## Multi-Notebook Data Flow

The pattern used here:

```
prep.ipynb                analysis.ipynb             report.ipynb
┌──────────────┐          ┌──────────────┐          ┌──────────────┐
│ raw CSV      │          │ parquet      │          │ parquet      │
│   ↓ clean    │──file──> │   ↓ compute  │──file──> │ + JSON       │
│ parquet out  │          │ JSON out     │          │   ↓ report   │
└──────────────┘          └──────────────┘          └──────────────┘
```

Each notebook owns one stage. Data passes as files (parquet, JSON). Each kernel is isolated -- no shared state to worry about.

## What You Built

This workflow demonstrated:

1. **`AGENT_REPL_PORT`** -- set once, used everywhere
2. **Multiple notebooks** -- separate concerns with isolated kernels
3. **`context`** -- orient yourself before acting on a notebook
4. **`--stream`** -- real-time JSONL output for long computations
5. **`git-setup`** -- clean diffs for notebook version control
6. **`--skip-tags`** -- selective execution for faster development cycles

## Next Steps

- **Agent automation**: [Agent Loop](agent-loop.md) -- automate the prompt-response cycle
- **Data analysis**: [Data Analysis](data-analysis.md) -- deep dive into a single notebook workflow

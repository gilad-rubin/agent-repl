# Example: Data Analysis Session

Walk through a complete data analysis workflow where an agent creates a notebook, loads data, cleans it, builds visualizations, and verifies everything runs end-to-end.

**Prerequisites**: [Quick Start](../quick-start.md), a running JupyterLab server (`agent-repl start`)

## Setup

Start JupyterLab and confirm the server is reachable:

```bash
agent-repl start
```

```json
{"pid": 48201, "command": "jupyter lab --IdentityProvider.token='' --ServerApp.password='' --no-browser"}
```

```bash
agent-repl servers
```

```json
{
  "servers": [
    {
      "url": "http://localhost:8888/",
      "token": "",
      "pid": 48201,
      "notebook_dir": "/Users/analyst/projects"
    }
  ]
}
```

Set the port so every subsequent command auto-targets this server:

```bash
export AGENT_REPL_PORT=8888
```

## Create the Notebook

```bash
agent-repl new analysis.ipynb
```

```json
{
  "operation": "create-notebook",
  "path": "analysis.ipynb",
  "kernel_name": "python3",
  "kernel_started": true,
  "cell_count": 0
}
```

The kernel is running and ready for code.

## Load and Inspect the Data

Insert a cell that imports pandas and loads a CSV:

```bash
agent-repl ix analysis.ipynb -s '
import pandas as pd

df = pd.read_csv("sales.csv")
print(f"Shape: {df.shape}")
print(f"Columns: {list(df.columns)}")
df.head(3)
'
```

```json
{
  "operation": "insert-execute",
  "insert": {
    "path": "analysis.ipynb",
    "operation": "insert-cell",
    "changed": true,
    "cell_id": "a1b2c3d4",
    "cell_count": 1
  },
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "Shape: (1247, 8)\nColumns: ['date', 'region', 'product', 'units', 'revenue', 'cost', 'customer_id', 'notes']\n"},
      {"type": "execute_result", "data": {"text/plain": "         date region  product  units  revenue   cost customer_id           notes\n0  2024-01-03   West  Widget A     12   143.88  86.33       C-401             NaN\n1  2024-01-03   East  Widget B      7    98.63  59.18       C-118  repeat customer\n2  2024-01-05  South  Widget A      3    35.97  21.58       C-227             NaN"}}
    ],
    "outputs_saved": true
  }
}
```

The dataset has 1247 rows and 8 columns. Notice the `NaN` in `notes` -- there are nulls to handle.

## Inspect Variables

Check what the kernel holds in memory:

```bash
agent-repl vars analysis.ipynb list
```

```json
{
  "operation": "variables-list",
  "kernel_id": "k-9f3a1b2c",
  "kernel_name": "python3",
  "variables": [
    {"name": "df", "type": "DataFrame", "module": "pandas.core.frame"},
    {"name": "pd", "type": "module", "module": "pandas"}
  ]
}
```

Get a closer look at the dataframe structure:

```bash
agent-repl vars analysis.ipynb preview --name df
```

```json
{
  "operation": "variable-preview",
  "kernel_id": "k-9f3a1b2c",
  "variable": {
    "name": "df",
    "type": "DataFrame",
    "module": "pandas.core.frame",
    "preview": "<pandas.core.frame.DataFrame>"
  }
}
```

For richer inspection, execute code directly:

```bash
agent-repl exec analysis.ipynb -c 'print(df.dtypes.to_string())'
```

```json
{
  "status": "ok",
  "events": [
    {"type": "stream", "name": "stdout", "text": "date            object\nregion          object\nproduct         object\nunits            int64\nrevenue        float64\ncost           float64\ncustomer_id     object\nnotes           object\n"}
  ]
}
```

The `date` column is a string -- it should be datetime. And `notes` has nulls to handle.

## Clean the Data

Insert a cleaning cell that fixes both issues:

```bash
agent-repl ix analysis.ipynb -s '
# Parse dates, drop rows with missing revenue, fill notes
df["date"] = pd.to_datetime(df["date"])
df = df.dropna(subset=["revenue"])
df["notes"] = df["notes"].fillna("")

# Normalize column names
df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

print(f"After cleaning: {df.shape[0]} rows")
print(f"Null counts:\n{df.isnull().sum().to_string()}")
'
```

```json
{
  "operation": "insert-execute",
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
      {"type": "stream", "name": "stdout", "text": "After cleaning: 1241 rows\nNull counts:\ndate           0\nregion         0\nproduct        0\nunits          0\nrevenue        0\ncost           0\ncustomer_id    0\nnotes          0\n"}
    ],
    "outputs_saved": true
  }
}
```

Six rows dropped (missing revenue), zero nulls remaining.

## Add Computed Columns

```bash
agent-repl ix analysis.ipynb -s '
df["profit"] = df["revenue"] - df["cost"]
df["margin"] = (df["profit"] / df["revenue"] * 100).round(1)
df["month"] = df["date"].dt.to_period("M")

print(df[["date", "region", "product", "revenue", "profit", "margin"]].head(3).to_string())
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "i9j0k1l2", "cell_count": 3},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "stream", "name": "stdout", "text": "       date region   product  revenue  profit  margin\n0 2024-01-03   West  Widget A   143.88   57.55    40.0\n1 2024-01-03   East  Widget B    98.63   39.45    40.0\n2 2024-01-05  South  Widget A    35.97   14.39    40.0\n"}
    ],
    "outputs_saved": true
  }
}
```

## Create Visualizations

Build a chart. The CLI strips image data by default, showing a placeholder instead of raw base64:

```bash
agent-repl ix analysis.ipynb -s '
import matplotlib.pyplot as plt

monthly = df.groupby("month")["revenue"].sum()
monthly.plot(kind="bar", figsize=(10, 5), title="Monthly Revenue")
plt.ylabel("Revenue ($)")
plt.tight_layout()
plt.show()
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "m3n4o5p6", "cell_count": 4},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "display_data", "data": {"text/plain": "[image: image/png]"}}
    ],
    "outputs_saved": true
  }
}
```

The `[image: image/png]` placeholder means the chart rendered successfully. The full image is saved in the notebook file -- open JupyterLab to see it. Use `--raw-output` if you need the base64 data in the CLI.

Add a second chart for regional comparison:

```bash
agent-repl ix analysis.ipynb -s '
regional = df.groupby("region")[["revenue", "cost", "profit"]].sum()
regional.plot(kind="barh", figsize=(8, 4), title="Revenue, Cost, and Profit by Region")
plt.xlabel("Amount ($)")
plt.tight_layout()
plt.show()
'
```

```json
{
  "operation": "insert-execute",
  "insert": {"changed": true, "cell_id": "q7r8s9t0", "cell_count": 5},
  "execute": {
    "status": "ok",
    "events": [
      {"type": "display_data", "data": {"text/plain": "[image: image/png]"}}
    ],
    "outputs_saved": true
  }
}
```

## Review the Notebook

Check what the notebook looks like at this point:

```bash
agent-repl cat analysis.ipynb
```

```json
{
  "path": "analysis.ipynb",
  "name": "analysis.ipynb",
  "cells": [
    {"index": 0, "cell_id": "a1b2c3d4", "cell_type": "code", "source_preview": "import pandas as pd\n\ndf = pd.read_csv(\"sales.csv\")\n...", "execution_count": 1},
    {"index": 1, "cell_id": "e5f6g7h8", "cell_type": "code", "source_preview": "# Parse dates, drop rows with missing revenue, fill notes\ndf[\"date\"] = pd.to_datetime(df[\"date\"])\ndf = df.dropna(subset=[\"revenue\"])\n...", "execution_count": 2},
    {"index": 2, "cell_id": "i9j0k1l2", "cell_type": "code", "source_preview": "df[\"profit\"] = df[\"revenue\"] - df[\"cost\"]\ndf[\"margin\"] = (df[\"profit\"] / df[\"revenue\"] * 100).round(1)\ndf[\"month\"] = df[\"date\"].dt.to_period(\"M\")\n...", "execution_count": 3},
    {"index": 3, "cell_id": "m3n4o5p6", "cell_type": "code", "source_preview": "import matplotlib.pyplot as plt\n\nmonthly = df.groupby(\"month\")[\"revenue\"].sum()\n...", "execution_count": 4},
    {"index": 4, "cell_id": "q7r8s9t0", "cell_type": "code", "source_preview": "regional = df.groupby(\"region\")[[\"revenue\", \"cost\", \"profit\"]].sum()\nregional.plot(kind=\"barh\", figsize=(8, 4), title=\"Revenue, Cost, and Profit by Region\")\nplt.xlabel(\"Amount ($)\")\n...", "execution_count": 5}
  ]
}
```

Five cells, all executed in order. The `source_preview` shows the first 3 lines of each cell -- use `--detail full` for complete source and outputs.

## Verify with Run-All

Restart the kernel and re-execute every cell to confirm the notebook is reproducible:

```bash
agent-repl restart-run-all analysis.ipynb --save-outputs
```

```json
{
  "operation": "restart-run-all",
  "restart": {
    "operation": "restart-kernel",
    "kernel_id": "k-9f3a1b2c",
    "kernel_name": "python3"
  },
  "run_all": {
    "status": "ok",
    "path": "analysis.ipynb",
    "executed_cell_count": 5,
    "skipped_cell_count": 0,
    "failed_cell": null,
    "outputs_saved": true,
    "cells": [
      {"index": 0, "cell_id": "a1b2c3d4", "status": "ok", "source_preview": "import pandas as pd ..."},
      {"index": 1, "cell_id": "e5f6g7h8", "status": "ok", "source_preview": "# Parse dates, drop rows with ..."},
      {"index": 2, "cell_id": "i9j0k1l2", "status": "ok", "source_preview": "df[\"profit\"] = df[\"revenue\"] ..."},
      {"index": 3, "cell_id": "m3n4o5p6", "status": "ok", "source_preview": "import matplotlib.pyplot as plt ..."},
      {"index": 4, "cell_id": "q7r8s9t0", "status": "ok", "source_preview": "regional = df.groupby(\"region\") ..."}
    ]
  }
}
```

All 5 cells pass with `"status": "ok"`. The notebook is reproducible from a fresh kernel.

## What You Built

This session covered the full agent-repl data analysis loop:

1. **Create** a notebook with `new`
2. **Build incrementally** with `ix` (insert + execute)
3. **Inspect state** with `vars list` and `vars preview`
4. **Handle rich output** -- images render in JupyterLab, CLI shows placeholders
5. **Review** the notebook structure with `cat`
6. **Verify reproducibility** with `restart-run-all`

## Next Steps

- **Work with prompts**: [Agent Loop](agent-loop.md) -- build an automated watch-and-respond loop
- **Multiple notebooks**: [Multi-Notebook Workflows](multi-notebook.md) -- coordinate across notebooks
- **Edit cells**: See the [edit commands](../../SKILL.md) for replacing, moving, and deleting cells

# CLI JSON API

**JSON contract** - Every public `agent-repl` command prints JSON to stdout.

**Notebook-first** - Core notebook commands are designed around notebook paths and stable `cell_id` values.

**Diagnostics stay explicit** - When a command cannot complete safely, it should fail in the CLI rather than prompting through the editor.

## Common Success Shape

There is no single global schema, but most responses include some combination of:

| Field | Meaning |
|---|---|
| `status` | High-level outcome such as `ok`, `error`, or `timeout` |
| `path` | Notebook path relative to the workspace |
| `cell_id` | Stable UUID-like cell identifier |
| `cell_index` | Positional cell index when relevant |
| `outputs` | Jupyter-style output objects |
| `execution_count` | Jupyter execution count when available |
| `kernel_state` | `idle`, `busy`, or `not_open` |
| `ready` | Whether a newly created notebook is immediately usable |

## Notebook Output Shape

`cat` returns cells like:

```json
{
  "path": "analysis.ipynb",
  "cells": [
    {
      "index": 0,
      "cell_id": "f5d5...",
      "cell_type": "code",
      "source": "x = 2\nx * 3",
      "execution_count": 1,
      "outputs": [
        {
          "output_type": "execute_result",
          "data": {
            "text/plain": "6"
          },
          "metadata": {}
        }
      ]
    }
  ]
}
```

## Execution Responses

`ix`, `exec`, and related commands typically include:

| Field | Meaning |
|---|---|
| `status` | `ok`, `error`, or `timeout` |
| `cell_id` | Executed cell identifier |
| `cell_index` | Executed cell position |
| `outputs` | Resulting cell outputs |
| `execution_count` | Jupyter execution count |
| `execution_mode` | Backend used to execute the cell |
| `execution_preference` | Requested execution preference |

## Error Shape

Commands fail with a non-zero exit code and emit JSON-shaped error text on stderr. Typical form:

```json
{"error":"No workspace .venv kernel was detected for this workspace. Re-run with --kernel <python-path>."}
```

How to fix:

- read the exact `error` string
- prefer changing the command inputs rather than clicking through UI
- use `status` or `cat` only when you need diagnostics

## Command Families

### Headless core notebook commands

- `new`
- `ix`
- `edit`
- `exec`
- `cat`
- `status`
- `run-all`
- `restart`
- `restart-run-all`

### Editor-assisted commands

- `kernels`
- `select-kernel`
- `prompts`
- `respond`
- `reload`

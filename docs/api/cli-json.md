# CLI JSON API

**Success is JSON** - successful public `agent-repl` commands print JSON to stdout.

**Notebook-first** - the public surface is organized around notebook paths and stable `cell_id` values.

**Diagnostics stay explicit** - when a command cannot complete safely, it should fail in the CLI instead of prompting through the editor.

## Common Success Shape

There is no single global schema, but many responses include some combination of:

| Field | Meaning |
|---|---|
| `status` | High-level outcome such as `ok`, `error`, or `timeout` |
| `path` | Notebook path relative to the workspace |
| `cell_id` | Stable UUID-like cell identifier |
| `cell_index` | Positional cell index when relevant |
| `outputs` | Jupyter-style output objects |
| `execution_count` | Jupyter execution count when available |
| `kernel_state` | Notebook state such as `idle`, `busy`, `not_open`, or a canvas-specific open state |
| `ready` | Whether a newly created notebook is immediately usable |

## Notebook Structure Shape

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
| `execution_mode` | Backend that executed the cell |
| `execution_preference` | Requested execution preference |
| `operation` | Operation type such as `insert-execute` |

## Creation and Open Responses

`new` commonly includes:

| Field | Meaning |
|---|---|
| `status` | `ok` on success |
| `path` | Notebook path |
| `kernel_status` | Kernel selection result |
| `ready` | Whether the notebook is immediately usable |
| `kernel` | Selected kernel record |
| `message` | Human-readable kernel selection summary |
| `mode` | Runtime mode, usually `headless` |
| `open` | Present when `--open` was requested |

`open` commonly includes:

| Field | Meaning |
|---|---|
| `status` | `ok` on success |
| `path` | Notebook path |
| `target` | `vscode` or `browser` |
| `editor` | `canvas` or `jupyter` |
| `view_type` | VS Code custom editor or notebook view type |
| `url` | Browser URL when `target == "browser"` |

## Reload Response

`reload` returns:

```json
{
  "status": "ok",
  "message": "Routes hot-reloaded",
  "extension_root": "/Users/you/.vscode/extensions/giladrubin.agent-repl-0.3.0",
  "routes_module": "/Users/you/.vscode/extensions/giladrubin.agent-repl-0.3.0/out/routes.js"
}
```

Use this to confirm which installed extension build is actually active.

## Error Shape

Many command failures use an `error` field, for example:

```json
{"error":"No workspace .venv kernel was detected for this workspace. Re-run with --kernel <python-path>."}
```

Some failures still come from `argparse` or explicit `SystemExit`, so not every non-zero exit is guaranteed to be JSON-shaped.

How to fix:

- read the exact `error` string first
- prefer changing command inputs instead of clicking through UI
- use `status` or `cat` only when you need extra diagnostics

## Public Command Families

### Headless notebook commands

- `new`
- `ix`
- `edit`
- `exec`
- `cat`
- `status`
- `run-all`
- `restart`
- `restart-run-all`

### Bridge-backed or editor-assisted commands

- `open`
- `kernels`
- `select-kernel --interactive`
- `prompts`
- `respond`
- `reload`

# Command Reference

**Headless core path** - the public notebook commands prefer the shared runtime in `src/agent_repl/core/`, even when the editor is closed.

**Canvas-aware opening** - `new --open` and `open` default to the Agent REPL canvas in VS Code, with optional browser and native Jupyter targets.

**Shared human session by default** - when `ix`, `edit`, `exec`, `run-all`, or `restart-run-all` do not receive `--session-id`, they reuse the active human workspace session when one exists; otherwise they start a human CLI session and use that ownership for the operation.

**Structured success output** - public subcommands return JSON on success. Use `--pretty` when you want indented output.

## Minimal Happy Path

```bash
agent-repl new tmp/validation.ipynb
agent-repl ix tmp/validation.ipynb -s 'x = 2\nx * 3'
```

That is the default path. Use `cat` or `status` only when you need diagnostics.

If you want to connect an external MCP client, start with `agent-repl mcp setup`.

If you want onboarding help after installing the CLI, start with `agent-repl setup` or `agent-repl doctor`.

## Core Notebook Commands

### `new`

Create a notebook and prepare the runtime.

```bash
agent-repl new PATH [--kernel PYTHON] [--cells-json JSON] [--open] [--target vscode|browser] [--editor canvas|jupyter] [--browser-url URL]
```

Examples:

```bash
agent-repl new analysis.ipynb
agent-repl new analysis.ipynb --open
agent-repl new analysis.ipynb --open --target browser
agent-repl new analysis.ipynb --cells-json '[{"type":"markdown","source":"# Notes"},{"type":"code","source":"print(1)"}]'
```

Notes:

- uses the workspace `.venv` automatically when it exists
- kernel discovery prefers the workspace `.venv` first
- returns `ready: true` when the notebook is immediately usable
- does not auto-run starter cells from `--cells-json`
- `--open` uses the extension bridge after the notebook is created
- `--target vscode` is the default
- `--editor canvas` is the default VS Code target
- `--target browser` opens the standalone browser canvas URL

### `open`

Open an existing notebook in an editor.

```bash
agent-repl open PATH [--target vscode|browser] [--editor canvas|jupyter] [--browser-url URL]
```

Examples:

```bash
agent-repl open analysis.ipynb
agent-repl open analysis.ipynb --editor jupyter
agent-repl open analysis.ipynb --target browser
```

Notes:

- defaults to VS Code
- defaults to the Agent REPL canvas inside VS Code
- `--editor jupyter` explicitly chooses the native notebook UI
- `--target browser` opens the standalone preview canvas

### `ix`

Insert a new cell, run it, and return the result.

```bash
agent-repl ix PATH (-s SOURCE | --source-file FILE | --cells-json JSON | --cells-file FILE | stdin) [--at-index N] [--timeout SECONDS] [--no-wait] [--session-id ID]
```

Examples:

```bash
agent-repl ix analysis.ipynb -s 'import pandas as pd; print(pd.__version__)'
agent-repl ix analysis.ipynb --source-file /tmp/cell.py
agent-repl ix analysis.ipynb -s 'df.head()' --session-id sess-human
agent-repl ix analysis.ipynb --cells-json '[{"type":"markdown","source":"# Step 1"},{"type":"code","source":"x = 2\nx * 3"}]'
```

Notes:

- waits for completion by default
- `--no-wait` returns after the execution record is created
- `--at-index` controls insertion point; `-1` means append
- when `--session-id` is omitted, `ix` reuses the active human workspace session when possible
- `--session-id` overrides the default session reuse and attributes the run to that collaboration session
- `--cells-json` / `--cells-file` run a batch sequentially so each code cell still projects as inserted-then-running
- batch `ix` stops on the first code cell that returns `status: "error"`
- batch `ix` does not support `--no-wait`
- infrastructure failures roll back the inserted cell
- Python exceptions do not roll back the cell; they remain as notebook error output

### `edit`

Edit notebook cells.

```bash
agent-repl edit PATH replace-source --cell-id ID -s 'new code'
agent-repl edit PATH insert (-s SOURCE | --source-file FILE | --cells-json JSON | --cells-file FILE) [--cell-type code|markdown] [--at-index N]
agent-repl edit PATH delete --cell-id ID
agent-repl edit PATH move --cell-id ID --to-index N
agent-repl edit PATH clear-outputs --all
```

Examples:

```bash
agent-repl edit analysis.ipynb insert -s 'print(1)'
agent-repl edit analysis.ipynb insert --cells-json '[{"type":"markdown","source":"# Notes"},{"type":"code","source":"print(1)"}]'
```

Use `--cell-id` when possible. It is safer than positional indexes.

When `--session-id` is omitted, `edit` reuses the active human workspace session when possible.

Use `--session-id` when you want to override that default attribution.

### `exec`

Run an existing cell, or insert and run inline code.

```bash
agent-repl exec PATH --cell-id ID [--timeout SECONDS] [--no-wait] [--session-id ID]
agent-repl exec PATH -c 'probe code' [--session-id ID]
```

Notes:

- `exec --cell-id` reruns an existing cell
- `exec -c` inserts a real persistent code cell and runs it
- when `--session-id` is omitted, `exec` reuses the active human workspace session when possible
- `--session-id` attributes the execution to that collaboration session

### `cat`

Inspect notebook structure and outputs.

```bash
agent-repl cat PATH [--no-outputs]
```

Use this when you need:

- live `cell_id` values from the notebook
- a source or output inspection pass
- prompt metadata

### `status`

Inspect notebook execution state.

```bash
agent-repl status PATH
```

Use this when:

- a run is long-lived
- you want to confirm the runtime is idle
- you need queue, busy, or open-state diagnostics

### `run-all`

Execute every code cell.

```bash
agent-repl run-all PATH
```

Notes:

- reuses the active human workspace session when possible before executing the notebook

### `restart`

Restart the notebook runtime.

```bash
agent-repl restart PATH
```

### `restart-run-all`

Restart the notebook runtime and execute every code cell.

```bash
agent-repl restart-run-all PATH
```

Notes:

- reuses the active human workspace session when possible before restarting and executing the notebook

### `select-kernel`

Choose a kernel for a notebook.

```bash
agent-repl select-kernel PATH [--kernel-id ID] [--interactive]
```

Examples:

```bash
agent-repl select-kernel analysis.ipynb --kernel-id /opt/miniconda3/bin/python3
agent-repl select-kernel analysis.ipynb --interactive
```

Notes:

- without `--interactive`, the public path goes through the shared runtime
- `--interactive` explicitly uses the VS Code kernel picker through the bridge
- changing kernels restarts the notebook runtime when needed

## Onboarding Commands

### `setup`

Run onboarding checks and optional workspace setup actions.

```bash
agent-repl setup [--workspace-root PATH] [--configure-editor-default] [--with-mcp] [--mcp-smoke-test|--no-mcp-smoke-test] [--server-name NAME] [--smoke-test] [--smoke-test-path PATH]
```

Examples:

```bash
agent-repl setup
agent-repl setup --configure-editor-default
agent-repl setup --with-mcp --smoke-test
```

Notes:

- returns structured JSON so a coding agent can drive onboarding safely
- `--configure-editor-default` writes `.vscode/settings.json` for the current workspace
- `--with-mcp` reuses the public MCP onboarding flow
- `--smoke-test` creates and executes a notebook smoke test in the workspace

### `doctor`

Inspect CLI, workspace, editor, and optional MCP readiness.

```bash
agent-repl doctor [--workspace-root PATH] [--probe-mcp] [--smoke-test] [--smoke-test-path PATH]
```

Examples:

```bash
agent-repl doctor
agent-repl doctor --probe-mcp
agent-repl doctor --smoke-test
```

Notes:

- reports available installers, workspace `.venv` detection, and kernel readiness
- shows whether the workspace is already configured to open `*.ipynb` in the Agent REPL canvas
- `--probe-mcp` starts or reuses the workspace daemon and reports the canonical public MCP endpoint
- `--smoke-test` creates and executes a notebook smoke test in the workspace

### `editor configure`

Update workspace settings for VS Code-family editors.

```bash
agent-repl editor configure --default-canvas [--workspace-root PATH]
```

Example:

```bash
agent-repl editor configure --default-canvas
```

Notes:

- writes or updates `.vscode/settings.json`
- sets `workbench.editorAssociations["*.ipynb"] = "agent-repl.canvasEditor"`
- is idempotent and preserves unrelated workspace settings

## MCP Commands

### `mcp setup`

Start or reuse the workspace-scoped MCP server and print the canonical connection details.

```bash
agent-repl mcp setup [--workspace-root PATH] [--server-name NAME]
```

This returns:

- the canonical `/mcp` URL
- the legacy `/mcp/mcp` URL for compatibility
- the exact `Authorization` header value
- a standard `mcpServers` config block

### `mcp status`

Show the current MCP endpoint and daemon status for the workspace.

```bash
agent-repl mcp status [--workspace-root PATH]
```

### `mcp config`

Print only the ready-to-paste `mcpServers` config block.

```bash
agent-repl mcp config [--workspace-root PATH] [--server-name NAME]
```

Example:

```bash
agent-repl mcp config --server-name analysis-repl
```

### `mcp smoke-test`

Verify the public MCP surface with a real client round-trip.

```bash
agent-repl mcp smoke-test [--workspace-root PATH]
```

The smoke test checks the core daemon, lists MCP tools, lists resources, and reads the `agent-repl://status` resource.

## Editor-Assisted Commands

### `kernels`

List available editor-backed kernels.

```bash
agent-repl kernels
```

This command requires a matching extension bridge.

### `prompts`

List prompt cells already present in the notebook.

```bash
agent-repl prompts PATH
```

### `respond`

Answer a prompt cell from the CLI.

```bash
agent-repl respond PATH --to CELL_ID (-s SOURCE | --source-file FILE | stdin)
```

`respond` currently:

- marks the prompt in progress
- inserts a response cell
- executes it
- marks the prompt answered

### `reload`

Hot-reload installed extension routes.

```bash
agent-repl reload --pretty
```

This returns the live `extension_root` and `routes_module` so you can confirm which installed build is active.

It does not fully restart the VS Code extension host.

## Shared Input Rules

Source-accepting commands support three input modes:

1. `-s 'inline code'`
2. `--source-file /path/to/file`
3. stdin when neither flag is provided

## Public Command Summary

| Command | Works with editor closed | Uses extension bridge |
|---|---|---|
| `new` | Yes | Only when `--open` is used |
| `open` | No | Yes |
| `ix` | Yes | No on the normal path |
| `edit` | Yes | No on the normal path |
| `exec` | Yes | No on the normal path |
| `cat` | Yes | No on the normal path |
| `status` | Yes | No on the normal path |
| `run-all` | Yes | No on the normal path |
| `restart` | Yes | No on the normal path |
| `restart-run-all` | Yes | No on the normal path |
| `kernels` | No | Yes |
| `select-kernel` | Yes | Only with `--interactive` |
| `prompts` | Yes | No on the normal path |
| `respond` | Yes | Yes |
| `setup` | Yes | No |
| `doctor` | Yes | No |
| `editor configure` | Yes | No |
| `mcp setup` | Yes | No |
| `mcp status` | Yes | No |
| `mcp config` | Yes | No |
| `mcp smoke-test` | Yes | No |
| `reload` | No | Yes |

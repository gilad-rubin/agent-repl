# MCP

**Canonical endpoint** - the public MCP endpoint for a workspace is `/mcp`.

**Copy-paste first** - use the CLI to print a ready-to-paste config block instead of reading runtime metadata files manually.

**Workspace-scoped server** - the MCP server is served by the same workspace daemon that owns notebooks, runtimes, sessions, and execution state.

## Quick Start

Start or reuse the workspace server:

```bash
agent-repl mcp setup
```

Verify the MCP round-trip:

```bash
agent-repl mcp smoke-test
```

Print only the MCP config block:

```bash
agent-repl mcp config
```

## What `setup` Returns

`agent-repl mcp setup` returns:

- the canonical MCP URL, for example `http://127.0.0.1:56557/mcp`
- the legacy compatibility URL `http://127.0.0.1:56557/mcp/mcp`
- the exact `Authorization` header value
- a standard `mcpServers` JSON block

The standard config shape is:

```json
{
  "mcpServers": {
    "agent-repl": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:56557/mcp",
      "headers": {
        "Authorization": "token <workspace-token>"
      }
    }
  }
}
```

## Main Commands

### `mcp setup`

```bash
agent-repl mcp setup [--workspace-root PATH] [--server-name NAME]
```

Use this first. It starts or reuses the daemon and prints everything needed to connect.

### `mcp status`

```bash
agent-repl mcp status [--workspace-root PATH]
```

Use this when you want to confirm the current endpoint, token header, and daemon counts without reformatting the config block.

### `mcp config`

```bash
agent-repl mcp config [--workspace-root PATH] [--server-name NAME]
```

Use this when you only want the `mcpServers` block.

### `mcp smoke-test`

```bash
agent-repl mcp smoke-test [--workspace-root PATH]
```

This verifies:

- the workspace daemon is reachable
- MCP tools can be listed
- MCP resources can be listed
- `agent-repl://status` can be read successfully

## Resources and Tools

The MCP server exposes notebook, runtime, session, branch, and lease operations as tools.

It also exposes:

- `agent-repl://status` - current workspace status as JSON

## Notes

- `/mcp` is the canonical public endpoint
- `/mcp/mcp` remains available as a compatibility alias
- the server uses Streamable HTTP transport
- auth is local bearer-style header auth with `Authorization: token <token>`

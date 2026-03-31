# V1 Architecture Behavior Locks

These behaviors are architectural commitments from the v1 modernization. They should not regress during future work.

## Checkpoint Safety

| Behavior | Why It Matters | Current Evidence | Lock Status |
|---|---|---|---|
| Checkpoint restore refuses while notebook is executing | Restoring mid-execution would corrupt execution state and produce undefined kernel behavior | `CheckpointService.restore_checkpoint` checks execution ledger busy state | Strong |
| Checkpoint create captures full notebook cell state including outputs | Checkpoints must be a complete safety primitive, not a partial save | `CheckpointService.create_checkpoint` serializes all cells via `CoreState` | Strong |

## MCP Surface

| Behavior | Why It Matters | Current Evidence | Lock Status |
|---|---|---|---|
| MCP exposes 6 bundled tools, not a flat tool surface | Agents need a manageable operating manual, not a phone book of tiny tools. See [mcp_dos_and_donts.md](/dev/mcp_dos_and_donts.md) | `mcp_adapter.py`: `notebook-observe`, `notebook-edit`, `notebook-execute`, `notebook-runtime`, `workspace-files`, `checkpoint` | Strong |
| MCP tools call the same CoreState methods as CLI and REST | Tool parity prevents behavioral drift between surfaces | All 6 tools delegate to `CoreState` service methods | Strong |

## WebSocket Sync

| Behavior | Why It Matters | Current Evidence | Lock Status |
|---|---|---|---|
| WebSocket is the only sync transport for activity, execution, and presence | Polling was explicitly rejected as a primary sync model in the v1 architecture | HTTP polling removed from `proxy.ts`, `standalone-host.ts`, `session.ts`, `jupyterlab-preview.tsx`. `DaemonWebSocket` in `shared/wsClient.ts` | Strong |
| WebSocket uses nonce auth, not token-in-URL | Tokens in WebSocket URLs leak into server logs and browser history | `POST /api/ws-nonce` creates single-use nonce, upgrade via `ws://host:port/ws?nonce=<nonce>` | Strong |
| WebSocket reconnects with exponential backoff and resubscribes | Connection drops must not cause data loss or require manual refresh | `DaemonWebSocket` tests: reconnect, resubscribe, cursor-based replay. [ws-client.test.js](/extension/tests/ws-client.test.js) | Strong |

## Execution Path

| Behavior | Why It Matters | Current Evidence | Lock Status |
|---|---|---|---|
| All workspace notebook execution routes through daemon HTTP | The v1 architecture commits to one execution path for all surfaces | No `notebook.cell.execute` or `kernel.executeCode` in execution paths. [queue-no-yank.test.js](/extension/tests/queue-no-yank.test.js) | Strong |
| Extension queue dispatches to `daemonPost`, not VS Code commands | Prevents execution path divergence between editor and headless | `execution/queue.ts` uses `daemonPost` from `session.ts`. [queue-focus-restore.test.js](/extension/tests/queue-focus-restore.test.js) | Strong |
| HeadlessNotebookProjection.executeCells uses daemon HTTP | Projection execution must not bypass the unified path | `session.ts` calls `daemonPost('/api/notebooks/execute-cell')`. [session-auto-attach.test.js](/extension/tests/session-auto-attach.test.js) | Strong |

## Notes

- These locks represent the outcome of the v1 architecture modernization (Waves 1-4).
- Any change that reintroduces polling, native VS Code execution, or flat MCP tools should be treated as an architecture regression and explicitly justified.

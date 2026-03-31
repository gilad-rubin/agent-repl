# Behavior Locks

This folder captures product behaviors that are important enough to preserve during modernization even when they are encoded mainly in code or tests instead of long-form docs.

Use these behavior locks as rewrite guardrails:

- if a user would notice it, give it a named regression test
- if it is surprising or non-obvious, write down the behavior in this folder
- do not delete or change one of these behaviors silently during refactors

## Why This Exists

`agent-repl` has many decisions that live in interaction code:

- browser keyboard shortcuts
- command-mode vs edit-mode transitions
- trailing-cell behavior after `Shift+Enter`
- session reuse and auto-attach policy
- background-safe execution behavior
- CLI JSON contract details used by other layers

Those decisions are part of the product, even if they are not yet well documented.

## Current Locked Areas

### Browser Preview and Shared Canvas

| Behavior | Why it matters | Locked by |
|---|---|---|
| `Cmd/Ctrl+S` saves the active draft without blur | Users expect notebook save shortcuts to work while editing | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L203) |
| `Cmd/Ctrl+B` toggles the browser explorer and does not insert a cell | Browser shell navigation should win over notebook command mode | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L304), [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L39) |
| Explorer notebook switching changes the visible notebook without stale mock state leaking through | Preview navigation is part of the browser workflow | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L324) |
| Command-mode `b` then `Enter` inserts below and enters edit mode | This is the notebook authoring flow users feel immediately | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L344), [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L27) |
| Command-mode `a` then `Enter` inserts above and enters edit mode | Above/below insertion symmetry should survive controller rewrites | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L382), [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L190) |
| `m` and `y` switch the selected cell between markdown and code | Cell-type toggles are classic notebook behavior and easy to regress | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L408), [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L58) |
| `Escape` leaves edit mode so notebook command keys take over again | This is the seam between typing and notebook commands | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L446), [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L138) |
| `Shift+Enter` runs the latest in-editor source without waiting for a separate draft flush | Prevents stale-source execution races | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L703) |
| `Shift+Enter` advances focus immediately, including on the last code cell | This is a notebook-native interaction expectation | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L415), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L733) |
| Restart + `Shift+Enter` can reuse an existing blank trailing code cell and still recreate the next trailing cell | This is the most fragile trailing-cell workflow in the browser preview today | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L774), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L824) |
| Arrow-up/arrow-down preserve edit-mode movement across adjacent cells | This affects keyboard-heavy editing flow | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L582), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L610) |
| `dd` deletes the selected cell and chooses the next sensible focus target | Focus/fallback rules are user-visible behavior, not internal detail | [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L638), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L659) |

### Editor Proxy and Runtime Integration

| Behavior | Why it matters | Locked by |
|---|---|---|
| Editor proxy refreshes contents/runtime after restart-and-run-all in a stable way | Keeps canvas state coherent after high-level actions | [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L254) |
| Self-conflict fallback paths for execute-all and restart-and-run-all are preserved | These are workflow decisions, not just transport details | [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L375), [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L493) |
| Session auto-attach keeps reusing and heartbeating the intended session shape | Session continuity is a user-visible collaboration decision | [session-auto-attach.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/session-auto-attach.test.js#L629) |
| Background notebook operations should not force save or steal focus unexpectedly | Background-safe execution is a product promise | [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L696), [queue-no-yank.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/queue-no-yank.test.js#L188) |

### V1 Architecture (Modernization)

| Behavior | Why it matters | Locked by |
|---|---|---|
| Checkpoint restore refuses while notebook is executing | Restoring mid-execution corrupts state | [v1-architecture.md](v1-architecture.md) |
| MCP exposes 6 bundled tools, not flat | Agents need manageable tools, not a phone book | [v1-architecture.md](v1-architecture.md), [mcp_dos_and_donts.md](/dev/mcp_dos_and_donts.md) |
| WebSocket is the only sync transport | Polling was explicitly rejected for v1 | [v1-architecture.md](v1-architecture.md), [ws-client.test.js](/extension/tests/ws-client.test.js) |
| All execution routes through daemon HTTP | One execution path for all surfaces | [v1-architecture.md](v1-architecture.md), [queue-no-yank.test.js](/extension/tests/queue-no-yank.test.js) |

### CLI and Core Contracts

| Behavior | Why it matters | Locked by |
|---|---|---|
| Public CLI commands keep stable JSON success/error shapes | Other layers and scripts depend on those machine contracts | [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L2323), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L2660) |
| Default owner-session reuse prefers an existing reusable human session | This is collaboration policy, not incidental implementation | [cli.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/cli.py#L79) |
| Bridge/core error shaping preserves helpful HTTP detail instead of generic failures | Tooling and debugging quality depend on this contract | [client.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/client.py#L238), [core/client.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/client.py#L618) |
| Conflict payloads preserve operation-specific detail such as `execute-cell` | Conflict handling needs structured diagnostics to remain debuggable | [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L735) |

## Operating Rule

Before refactoring a behavior-heavy area:

1. add or tighten the regression test first
2. update this inventory if the behavior is important or surprising
3. only then change the implementation

If a rewrite intentionally changes one of these behaviors, record that explicitly in the PR and replace the old lock with the new one instead of letting it drift silently.

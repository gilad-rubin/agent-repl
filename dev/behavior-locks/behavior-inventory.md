# Behavior Inventory

This inventory tracks product decisions that are important to preserve during modernization, even when they are not fully documented elsewhere.

Each item should stay backed by executable tests.

## Browser Canvas

### Save Shortcut Flushes Without Blur

- Behavior: `Cmd/Ctrl+S` saves the active draft in the browser preview without requiring the editor to blur first.
- Why it matters: users expect browser save to commit exactly what is visible in the editor, not a stale pre-blur version.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L3525), [standalone-host.ts](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/standalone-host.ts#L477)
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L203)

### Explorer Toggle Shortcut Never Types Into The Cell

- Behavior: `Cmd/Ctrl+B` toggles the browser explorer and must not insert a literal `b` into the focused editor or mutate notebook content.
- Why it matters: this is a browser-only shortcut override that is easy to lose during keyboard-handler rewrites.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L3525)
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L298), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L322)

### Explorer Notebook Switching Resets The Visible Preview Notebook

- Behavior: switching notebooks from the browser explorer updates the active notebook and visible cell set rather than blending old and new notebook state.
- Why it matters: this preserves preview trustworthiness and avoids stale runtime/editor state leaking between notebooks.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L889)
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L341), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L360)

### Explorer Switching Clears Running State From The Previous Notebook

- Behavior: if the current preview notebook is running, switching to another notebook clears the old notebook's running state instead of leaking stop/status state into the new notebook.
- Why it matters: rewrites can preserve notebook switching while still accidentally carrying runtime state, which makes the browser preview feel untrustworthy.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L900)
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L360)

### Escape Restores Command-Mode Routing

- Behavior: `Escape` exits edit-mode routing so the next command key is interpreted as a notebook command rather than literal text input.
- Why it matters: command/edit-mode separation is a core notebook interaction model and easy to regress during React/input refactors.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L3552)
- Tests: [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L145), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L379)

### Command-Mode Insert + Enter Opens The New Cell In Edit Mode

- Behavior: `b` in command mode inserts a cell below, and `Enter` upgrades the pending insertion into edit mode.
- Why it matters: this is a notebook-native gesture that depends on pending-cell activation logic, not just insertion.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L3552)
- Tests: [notebook-command-controller.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/notebook-command-controller.test.js#L31), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L344)

### Shift+Enter Uses The Latest In-Editor Source

- Behavior: `Shift+Enter` executes the current draft source without waiting for a separate save or blur path.
- Why it matters: this prevents stale-draft races and makes execution reflect what the user actually sees.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L1028), [standalone-host.ts](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/standalone-host.ts#L506)
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L707)

### Shift+Enter Advances Selection Before Execution Finishes

- Behavior: when a cell runs via `Shift+Enter`, the next cell becomes selected immediately before the execution completes.
- Why it matters: this affects perceived responsiveness and notebook fluency, not just final execution outcome.
- Code: [main.tsx](/Users/giladrubin/python_workspace/agent-repl/extension/webview-src/main.tsx#L3552)
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L415)

### Shift+Enter On The Last Code Cell Creates Or Reuses The Trailing Cell Inline

- Behavior: `Shift+Enter` on the last runnable code cell inserts or reuses the trailing code cell inline and focuses it before execution finishes.
- Why it matters: trailing-cell rules are one of the easiest notebook behaviors to accidentally simplify away.
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L733), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L781), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L824)

### Edit-Mode Arrow Navigation Crosses Cell Boundaries Without Dropping Edit Mode

- Behavior: arrow-down at the end of a code cell and arrow-up at the start of a code cell move into adjacent cells while preserving edit-mode interaction.
- Why it matters: these are subtle notebook ergonomics that are often lost when editors are re-mounted or focus management changes.
- Tests: [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L576), [preview-webview.smoke.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/preview-webview.smoke.js#L604)

## Extension / Editor Integration

### Self-Conflict Fallback Preserves Background-Safe Execution

- Behavior: when run-all or restart-run-all hits a self-conflict path, the extension falls back to owned-cell execution instead of surfacing a user-visible conflict unnecessarily.
- Why it matters: this is a subtle collaboration-vs-usability rule that could disappear during service-layer cleanup.
- Code: [proxy.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/editor/proxy.ts#L408)
- Tests: [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L467)

### Session Auto-Attach Reuses Existing Human Sessions

- Behavior: VS Code auto-attach prefers reusing the best existing human session instead of creating duplicates.
- Why it matters: collaboration continuity depends on this ranking behavior even though users never see the selection algorithm directly.
- Code: [session.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/session.ts#L241)
- Tests: [session-auto-attach.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/session-auto-attach.test.js)

## CLI / Core

### CLI Prefers Reusable Human Sessions

- Behavior: public CLI notebook commands prefer joining an attached human editor session before creating a new CLI-owned session.
- Why it matters: this is a collaboration-product decision, not an implementation accident.
- Code: [cli.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/cli.py#L79)
- Tests: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py)

### Bridge/Core Discovery Is Part Of The Product Contract

- Behavior: stale bridge/core files are ignored or cleaned up, workspace matching is required, and stale daemons are restarted when code changes.
- Why it matters: a transport rewrite can easily preserve endpoint behavior while breaking “how the tool finds the right runtime.”
- Code: [client.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/client.py#L37), [core/client.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/core/client.py#L29)
- Tests: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L47), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L88), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L127), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L139), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L160), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L179), [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L252)

## Working Rule

When a modernization slice touches any of the behaviors above:

- update or add the backing test first
- keep the behavior inventory entry current
- call out intentional behavior changes explicitly in the task, PR, and docs

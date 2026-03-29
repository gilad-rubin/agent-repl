# CLI and Core Behavior Locks

These behaviors matter because they shape how the runtime feels from scripts, agents, and diagnostics flows, even when they are not obvious from high-level docs.

| Behavior | Why It Matters | Current Evidence | Lock Status |
|---|---|---|---|
| CLI restarts a stale daemon and reports that fact to stderr | This is part of the operational contract for local development and should not disappear during host rewrites | Implementation: [cli.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/cli.py#L31) and [cli.py](/Users/giladrubin/python_workspace/agent-repl/src/agent_repl/cli.py#L55) | Covered in code; could use tighter regression coverage |
| `cat` prefers the core notebook projection surface when available | Public reads should come from the runtime-first surface, not whichever older bridge path happens to answer first | Test: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L3207) | Strong |
| `status` prefers the core notebook projection surface when available | Status semantics should stay runtime-first as the architecture consolidates | Test: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L3234) | Strong |
| `new` prefers the core notebook projection surface | Notebook creation behavior should remain aligned with the shared runtime authority | Test: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L3566) | Strong |
| `restart-run-all` prefers the core execution surface | Rewrites should not accidentally route restart flows back through older bridge-first behavior | Test: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L3789) | Strong |
| `restart-run-all` reuses the preferred human session | Session attribution during restart flows is a behavior decision, not just a helper implementation | Test: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L3808) | Strong |
| Preferred reusable human-session selection is resolved by the core, not re-ranked per client | Session ownership and attribution should stay consistent across CLI, VS Code, and browser surfaces during consolidation | Tests: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L1818) and [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L4004) | Strong |
| Visible-cell execution updates source and outputs against the live runtime | This is important for projection-based execution semantics and easy to break while refactoring execution ownership | Tests: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L1077) and [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L1176) | Strong |
| Visible-cell execution has deliberate conflict semantics for leased cells | Collaboration rules should remain explicit until YDoc replaces them | Tests: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L1605) and [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L1909) | Strong |
| Session presence upsert/clear is part of the machine-readable CLI/core surface | Extension and browser helpers depend on these diagnostic/automation commands staying stable | Tests: [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L4036) and [test_agent_repl.py](/Users/giladrubin/python_workspace/agent-repl/tests/test_agent_repl.py#L4060) | Strong |

## Notes

- CLI/core behavior locks are especially important because some of these decisions are only visible in tests and helper scripts, not in user-facing docs.
- If any of these behaviors intentionally change during consolidation, update this file and the corresponding tests in the same change.

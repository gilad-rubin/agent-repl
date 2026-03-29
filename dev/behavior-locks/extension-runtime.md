# Extension Runtime Behavior Locks

These are extension-specific behaviors that are easy to erase during consolidation because they live in proxy logic, route glue, or auto-attach flows.

| Behavior | Why It Matters | Current Evidence | Lock Status |
|---|---|---|---|
| Session auto-attach reuses the preferred human session when possible | Users should converge on one shared workspace session instead of silently forking new ones | Test: [session-auto-attach.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/session-auto-attach.test.js#L284). Related implementation: [session.ts](/Users/giladrubin/python_workspace/agent-repl/extension/src/session.ts#L244) | Strong |
| Extension lifecycle auto-attaches on start and detaches on stop | Session continuity should survive window lifecycle without leaking stale attachments | Test: [extension-session-lifecycle.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/extension-session-lifecycle.test.js#L172) | Strong |
| Standalone browser attach also reuses the preferred human session | Browser preview should join the same human collaboration context rather than inventing its own | Test: [standalone-server.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/standalone-server.test.js#L92) | Strong |
| Background create route keeps notebook work in the background when quiet attach succeeds | Background-safe behavior is part of the product and should not regress into UI focus theft | Test: [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L52) | Strong |
| Execute-all route stays in the background and uses the internal executor instead of revealing UI | Bulk execution should not surface notebook UI unexpectedly | Test: [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L297) | Strong |
| Insert-and-execute can open a closed notebook in the background without revealing it | This keeps automation and agent flows background-safe | Tests: [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L379) and [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L463) | Strong |
| Restart routes use background shutdown and quiet reattach without opening notebook UI | Restart behavior should stay product-safe for background flows | Test: [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L542) | Strong |
| Open route defaults to the Agent REPL canvas editor | Rewrites should not accidentally fall back to the native editor path | Test: [routes-background.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/routes-background.test.js#L722) | Strong |
| Editor proxy refreshes contents and runtime after restart-and-run-all | Users see the right post-run state only if those follow-up refreshes stay intact | Tests: [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L254) and [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L467) | Strong |
| On self lease conflict, restart-and-run-all falls back to restart plus owned-cell execution | This is a product decision, not an implementation accident, and should survive service-layer consolidation | Test: [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L443) | Strong |
| Self-conflict fallback stops after the first failed cell | Failure handling during owned-cell fallback has deliberate semantics that should not silently widen or narrow | Test: [editor-proxy.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/editor-proxy.test.js#L500) | Strong |
| Projection sync forwards notebook activity and keeps notebook presence updated | Collaboration continuity depends on projection and presence staying in sync | Test: [session-auto-attach.test.js](/Users/giladrubin/python_workspace/agent-repl/extension/tests/session-auto-attach.test.js#L678) | Strong |

## Notes

- These are exactly the sorts of behaviors that get lost when proxy logic is “simplified” without a preservation pass.
- Any service-layer rewrite should prove these through adapter/integration tests, not only through unit tests.

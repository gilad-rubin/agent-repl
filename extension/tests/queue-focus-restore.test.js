const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadQueueModule(vscode, overrides = {}) {
    const modulePath = path.resolve(__dirname, '../out/execution/queue.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
        }
        if (request === '../notebook/identity') {
            return {
                resolveCell: () => 0,
                getCellId: () => 'cell-1',
            };
        }
        if (request === '../notebook/outputs') {
            return {
                AGENT_REPL_OUTPUT_METADATA_KEY: 'agent-repl-output',
                toJupyter: () => [],
                stripForAgent: value => value,
                toVSCode: value => value,
            };
        }
        if (request === '../notebook/resolver') {
            return {
                resolveNotebook: () => overrides.doc,
                ...overrides.resolver,
            };
        }
        if (request === '../session') {
            return {
                discoverDaemon: () => overrides.daemon ?? { url: 'http://127.0.0.1:9999', token: 'tok' },
                daemonPost: overrides.daemonPost ?? (async () => ({ status: 'ok' })),
                workspaceRootForPath: () => '/workspace',
                sessionIdForWorkspaceState: () => undefined,
            };
        }
        return originalLoad.call(this, request, parent, isMain);
    };

    delete require.cache[modulePath];
    try {
        return require(modulePath);
    } finally {
        Module._load = originalLoad;
    }
}

test('executeCell routes through daemon HTTP and returns the result', async () => {
    const doc = {
        uri: { fsPath: '/workspace/demo.ipynb' },
        cellAt: () => ({
            document: { getText: () => 'print(1)' },
            executionSummary: null,
        }),
    };

    let daemonCalls = [];
    const queue = loadQueueModule(
        {
            workspace: { getConfiguration: () => ({ get: (_n, fb) => fb }) },
            extensions: { getExtension: () => undefined },
        },
        {
            doc,
            daemonPost: async (_daemon, endpoint, body) => {
                daemonCalls.push({ endpoint, body });
                return { status: 'ok', outputs: [], execution_count: 1 };
            },
        },
    );

    const result = await queue.executeCell('/workspace/demo.ipynb', { cell_id: 'cell-1' }, 20);
    assert.equal(result.status, 'ok');
    assert.equal(result.execution_mode, 'daemon');
    assert.equal(daemonCalls.length, 1);
    assert.equal(daemonCalls[0].endpoint, '/api/notebooks/execute-cell');
    assert.equal(daemonCalls[0].body.wait, true);
});

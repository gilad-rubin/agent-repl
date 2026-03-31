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
                toJupyter: (cell) => cell.outputs ?? [],
                stripForAgent: (value) => value,
                toVSCode: (value) => value,
            };
        }
        if (request === '../notebook/resolver') {
            return {
                resolveNotebook: () => overrides.doc,
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

test('daemon-routed execution does not use VS Code notebook commands', async () => {
    const doc = {
        uri: { fsPath: '/workspace/demo.ipynb' },
        cellAt: () => ({
            document: { getText: () => 'print(21)' },
            outputs: [],
            executionSummary: null,
        }),
        cellCount: 1,
    };

    let notebookCommandCalled = false;
    const vscode = {
        workspace: {
            getConfiguration: () => ({ get: (_name, fallback) => fallback }),
        },
        commands: {
            executeCommand: async () => {
                notebookCommandCalled = true;
                throw new Error('should not call notebook.cell.execute');
            },
        },
        extensions: { getExtension: () => undefined },
    };

    const queue = loadQueueModule(vscode, {
        doc,
        daemonPost: async (_daemon, endpoint, body) => {
            assert.equal(endpoint, '/api/notebooks/execute-cell');
            return { status: 'ok', outputs: [], execution_count: 1 };
        },
    });
    queue.resetExecutionState();

    const result = await queue.executeCell('/workspace/demo.ipynb', { cell_id: 'cell-1' }, 20);
    assert.equal(result.status, 'ok');
    assert.equal(result.execution_mode, 'daemon');
    assert.equal(notebookCommandCalled, false, 'should not use native VS Code execution');
});

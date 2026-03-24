const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadQueueModule(vscode, resolverOverrides = {}) {
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
                resolveNotebook: resolverOverrides.resolveNotebook,
                ensureNotebookEditor: async () => ({}),
                captureEditorFocus: () => ({ kind: 'none' }),
                restoreEditorFocus: async () => {},
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

test('status is tracked per notebook path rather than by global busy state', async () => {
    let onChange;
    const docs = new Map([
        ['/tmp/a.ipynb', { uri: { fsPath: '/tmp/a.ipynb' }, cellAt: () => ({ document: { getText: () => 'print(1)' } }) }],
        ['/tmp/b.ipynb', { uri: { fsPath: '/tmp/b.ipynb' }, cellAt: () => ({ document: { getText: () => 'print(2)' } }) }],
    ]);

    const queue = loadQueueModule(
        {
            workspace: {
                onDidChangeNotebookDocument(callback) {
                    onChange = callback;
                    return { dispose() {} };
                },
                getConfiguration: () => ({
                    get: (_name, fallback) => fallback,
                }),
            },
            extensions: {
                getExtension: () => undefined,
            },
        },
        {
            resolveNotebook: (targetPath) => docs.get(targetPath),
        },
    );

    queue.resetExecutionState();
    queue.initExecutionMonitor();

    onChange({
        notebook: { uri: { fsPath: '/tmp/a.ipynb' } },
        cellChanges: [{
            cell: {
                index: 0,
                document: { getText: () => 'print(1)' },
            },
            executionSummary: { timing: { startTime: 1 } },
        }],
    });

    const status = await queue.getStatus('/tmp/b.ipynb');
    assert.equal(status.busy, false);
    assert.equal(status.kernel_state, 'idle');
});

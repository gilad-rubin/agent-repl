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
                resolveNotebook: () => resolverOverrides.doc,
                ensureNotebookEditor: async () => ({}),
                captureEditorFocus: () => ({ kind: 'none' }),
                restoreEditorFocus: async () => {},
                ...resolverOverrides,
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

test('startExecution returns a started execution id immediately for poll-based callers', async () => {
    const cell = {
        document: { getText: () => 'print(1)' },
        executionSummary: null,
    };
    const doc = {
        uri: { fsPath: '/tmp/demo.ipynb' },
        cellAt: () => cell,
    };

    const queue = loadQueueModule(
        {
            NotebookRange: class NotebookRange {
                constructor(start, end) {
                    this.start = start;
                    this.end = end;
                }
            },
            workspace: {
                getConfiguration: () => ({
                    get: (_name, fallback) => fallback,
                }),
            },
            window: {
                showNotebookDocument: async () => {},
            },
            commands: {
                executeCommand: async () => {
                    setTimeout(() => {
                        cell.executionSummary = { success: true, executionOrder: 1 };
                    }, 0);
                },
            },
            extensions: {
                getExtension: () => undefined,
            },
        },
        {
            doc,
            resolveNotebook: () => doc,
        },
    );

    const result = await queue.startExecution('/tmp/demo.ipynb', { cell_id: 'cell-1' }, 20);
    assert.equal(result.status, 'started');
    assert.equal(result.cell_id, 'cell-1');
    assert.equal(typeof result.execution_id, 'string');
});

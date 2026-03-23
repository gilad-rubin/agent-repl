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

test('executeCell still reports success when focus restore fails after notebook-command execution', async () => {
    const cell = {
        document: { getText: () => 'print(1)' },
        executionSummary: null,
    };
    const doc = {
        uri: { fsPath: '/tmp/demo.ipynb' },
        cellAt: () => cell,
    };

    let executed = false;
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
                executeCommand: async (command) => {
                    assert.equal(command, 'notebook.cell.execute');
                    executed = true;
                    cell.executionSummary = { success: true, executionOrder: 1 };
                },
            },
            extensions: {
                getExtension: () => undefined,
            },
        },
        {
            doc,
            resolveNotebook: () => doc,
            ensureNotebookEditor: async () => ({}),
            captureEditorFocus: () => ({
                kind: 'text',
                document: { uri: { scheme: 'file' } },
                selection: { anchor: 0, active: 0 },
                viewColumn: 1,
            }),
            restoreEditorFocus: async () => {
                throw new Error('focus restore failed');
            },
        },
    );

    const result = await queue.executeCell('/tmp/demo.ipynb', { cell_id: 'cell-1' }, 20);
    assert.equal(executed, true);
    assert.equal(result.status, 'ok');
    assert.equal(result.execution_mode, 'notebook-command');
    assert.equal(result.focus_restore_warning, 'focus restore failed');
});

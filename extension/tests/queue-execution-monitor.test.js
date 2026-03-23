const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadQueueModule(vscode) {
    const modulePath = path.resolve(__dirname, '../out/execution/queue.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
        }
        if (request === '../notebook/identity') {
            return {
                resolveCell: () => 0,
                getCellId: () => undefined,
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
                resolveNotebook: () => {
                    throw new Error('not used in this test');
                },
                ensureNotebookEditor: async () => {
                    throw new Error('not used in this test');
                },
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

function makeChange(summary) {
    return {
        notebook: { uri: { fsPath: '/tmp/demo.ipynb' } },
        cellChanges: [{
            cell: {
                index: 1,
                document: { getText: () => 'import numpy as np' },
            },
            executionSummary: summary,
        }],
    };
}

test('execution monitor treats success summaries as completed executions', () => {
    let onChange;
    const queue = loadQueueModule({
        workspace: {
            onDidChangeNotebookDocument(callback) {
                onChange = callback;
                return { dispose() {} };
            },
        },
    });

    queue.resetExecutionState();
    queue.initExecutionMonitor();

    onChange(makeChange({ timing: { startTime: 1 } }));
    assert.equal(queue.getKernelState(), 'busy');

    onChange(makeChange({ success: true, executionOrder: 2 }));
    assert.equal(queue.getKernelState(), 'idle');
});

test('execution monitor does not treat cleared execution summaries as a new running cell', () => {
    let onChange;
    const queue = loadQueueModule({
        workspace: {
            onDidChangeNotebookDocument(callback) {
                onChange = callback;
                return { dispose() {} };
            },
        },
    });

    queue.resetExecutionState();
    queue.initExecutionMonitor();

    onChange(makeChange(null));
    assert.equal(queue.getKernelState(), 'idle');
});

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
                toJupyter: (cell) => cell.outputs ?? [],
                stripForAgent: (value) => value,
                toVSCode: (value) => value,
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

test('no-yank execution passes a cancellation token to the Jupyter kernel API', async () => {
    const cell = {
        kind: 2,
        metadata: {},
        outputs: [],
        executionSummary: null,
        document: {
            getText: () => 'print(21)',
            languageId: 'python',
        },
    };
    const doc = {
        uri: { fsPath: '/tmp/demo.ipynb' },
        cellAt: () => cell,
        cellCount: 1,
    };

    class NotebookCellData {
        constructor(kind, source, languageId) {
            this.kind = kind;
            this.source = source;
            this.languageId = languageId;
            this.outputs = [];
            this.metadata = {};
            this.executionSummary = undefined;
        }
    }

    class WorkspaceEdit {
        constructor() {
            this.ops = [];
        }

        set(_uri, edits) {
            this.ops.push(...edits);
        }
    }

    const vscode = {
        NotebookCellData,
        WorkspaceEdit,
        NotebookCellOutput: class NotebookCellOutput {
            constructor(items, metadata = {}) {
                this.items = items;
                this.metadata = metadata;
            }
        },
        NotebookCellOutputItem: {
            error(error) {
                return { mime: 'application/x.notebook.error', data: Buffer.from(error.message) };
            },
        },
        NotebookEdit: {
            replaceCells(range, cells) {
                return { type: 'replaceCells', range, cells };
            },
            updateCellMetadata(index, metadata) {
                return { type: 'updateCellMetadata', index, metadata };
            },
        },
        NotebookRange: class NotebookRange {
            constructor(start, end) {
                this.start = start;
                this.end = end;
            }
        },
        CancellationTokenSource: class CancellationTokenSource {
            constructor() {
                this.token = {
                    isCancellationRequested: false,
                    onCancellationRequested: () => ({ dispose() {} }),
                };
            }
            dispose() {}
        },
        workspace: {
            getConfiguration: () => ({
                get: (_name, fallback) => fallback,
            }),
            async applyEdit(edit) {
                for (const op of edit.ops) {
                    if (op.type === 'replaceCells') {
                        const replacement = op.cells[0];
                        cell.outputs = replacement.outputs;
                        cell.metadata = replacement.metadata;
                        cell.executionSummary = replacement.executionSummary ?? null;
                    } else if (op.type === 'updateCellMetadata') {
                        cell.metadata = op.metadata;
                    }
                }
                return true;
            },
        },
        window: {
            showNotebookDocument: async () => {},
        },
        commands: {
            executeCommand: async () => {
                throw new Error('should not fall back to notebook command');
            },
        },
        extensions: {
            getExtension: () => ({
                isActive: true,
                exports: {
                    kernels: {
                        async getKernel() {
                            return {
                                status: 'idle',
                                async *executeCode(source, token) {
                                    assert.equal(source, 'print(21)');
                                    assert.equal(typeof token?.onCancellationRequested, 'function');
                                    yield {
                                        items: [{ mime: 'text/plain', data: Buffer.from('21') }],
                                        metadata: {},
                                    };
                                },
                            };
                        },
                    },
                },
            }),
        },
    };

    const queue = loadQueueModule(vscode, { doc });
    queue.resetExecutionState();
    queue.resetJupyterApiCache();

    const result = await queue.executeCell('/tmp/demo.ipynb', { cell_id: 'cell-1' }, 20);

    assert.equal(result.status, 'ok');
    assert.equal(result.execution_mode, 'jupyter-kernel-api');
    assert.equal(result.execution_preference, 'no-yank');
});

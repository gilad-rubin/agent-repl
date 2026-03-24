const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadOperations(vscode) {
    const modulePath = path.resolve(__dirname, '../out/notebook/operations.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
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

test('replace-source clears stale outputs while preserving metadata', async () => {
    let appliedEdits = null;

    class WorkspaceEdit {
        set(uri, edits) {
            appliedEdits = { uri, edits };
        }
    }

    const vscode = {
        NotebookCellKind: {
            Code: 1,
            Markup: 2,
        },
        NotebookCellData: class NotebookCellData {
            constructor(kind, value, languageId) {
                this.kind = kind;
                this.value = value;
                this.languageId = languageId;
                this.metadata = undefined;
                this.outputs = undefined;
                this.executionSummary = undefined;
            }
        },
        NotebookRange: class NotebookRange {
            constructor(start, end) {
                this.start = start;
                this.end = end;
            }
        },
        NotebookEdit: {
            replaceCells(range, cells) {
                return { kind: 'replaceCells', range, cells };
            },
        },
        WorkspaceEdit,
        workspace: {
            applyEdit: async () => true,
        },
    };

    const operations = loadOperations(vscode);
    const cell = {
        kind: vscode.NotebookCellKind.Code,
        document: {
            getText: () => 'x = 1',
            languageId: 'python',
        },
        metadata: {
            custom: {
                'agent-repl': {
                    cell_id: 'cell-1',
                },
            },
        },
        outputs: [{ old: true }],
        executionSummary: { executionOrder: 3 },
    };
    const doc = {
        uri: { fsPath: '/tmp/demo.ipynb' },
        cellCount: 1,
        isUntitled: true,
        cellAt: () => cell,
    };

    const [result] = await operations.applyEdits(doc, [
        { op: 'replace-source', cell_id: 'cell-1', source: 'x = 2' },
    ]);

    assert.equal(result.changed, true);
    assert.equal(appliedEdits.edits[0].kind, 'replaceCells');
    assert.deepEqual(appliedEdits.edits[0].cells[0].outputs, []);
    assert.deepEqual(appliedEdits.edits[0].cells[0].metadata, cell.metadata);
});

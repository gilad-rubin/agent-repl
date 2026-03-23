const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadResolver(vscode) {
    const modulePath = path.resolve(__dirname, '../out/notebook/resolver.js');
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

test('captureEditorFocus prefers the notebook editor over notebook-cell text editors', () => {
    const notebook = { uri: { fsPath: '/tmp/demo.ipynb', toString: () => '/tmp/demo.ipynb' } };
    const resolver = loadResolver({
        window: {
            activeTextEditor: {
                document: { uri: { scheme: 'vscode-notebook-cell' } },
                selection: { anchor: 0, active: 0 },
                viewColumn: 1,
            },
            activeNotebookEditor: {
                notebook,
                selections: [{ start: 0, end: 1 }],
                viewColumn: 2,
            },
        },
    });

    const focus = resolver.captureEditorFocus();
    assert.equal(focus.kind, 'notebook');
    assert.equal(focus.document, notebook);
});

test('restoreEditorFocus ignores notebook-cell text documents', async () => {
    let showTextCalled = false;
    const resolver = loadResolver({
        window: {
            showTextDocument: async () => {
                showTextCalled = true;
            },
            showNotebookDocument: async () => {},
        },
    });

    await resolver.restoreEditorFocus({
        kind: 'text',
        document: { uri: { scheme: 'vscode-notebook-cell' } },
        selection: { anchor: 0, active: 0 },
        viewColumn: 1,
    });

    assert.equal(showTextCalled, false);
});

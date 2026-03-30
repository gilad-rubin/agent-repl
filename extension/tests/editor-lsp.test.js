const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');

function loadLspModule() {
    const modulePath = path.resolve(__dirname, '../out/editor/lsp.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                Uri: {
                    file(targetPath) {
                        return {
                            fsPath: targetPath,
                            toString() {
                                return `file://${targetPath}`;
                            },
                        };
                    },
                },
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

const {
    buildWorkspaceSearchPaths,
    buildVirtualNotebookDocument,
    buildWorkspaceConfiguration,
    defaultWorkspaceSettings,
    mapDiagnosticsToCells,
    offsetToPosition,
    positionToOffset,
    PyrightNotebookLspClient,
} = loadLspModule();

test('buildVirtualNotebookDocument keeps code cells in order and skips markdown cells', () => {
    const document = buildVirtualNotebookDocument('/workspace', '/workspace/demo.ipynb', [
        { index: 0, cell_id: 'code-1', cell_type: 'code', source: 'import pandas as pd\n' },
        { index: 1, cell_id: 'markdown-1', cell_type: 'markdown', source: '# heading' },
        { index: 2, cell_id: 'code-2', cell_type: 'code', source: 'pd.DataFrame()\n' },
    ], 3);

    assert.equal(document.version, 3);
    assert.equal(document.codeCells.length, 2);
    assert.match(document.text, /agent-repl cell code-1/);
    assert.match(document.text, /agent-repl cell code-2/);
    assert.doesNotMatch(document.text, /markdown-1/);
    assert.equal(document.codeCells[0].cell_id, 'code-1');
    assert.equal(document.codeCells[1].cell_id, 'code-2');
    assert.equal(document.filePath, '/workspace/demo.ipynb.agent-repl.py');
    assert.equal(document.shadowFilePath, '/workspace/.agent-repl/pyright/demo.ipynb.agent-repl.py');
    assert.equal(document.uri, 'file:///workspace/.agent-repl/pyright/demo.ipynb.agent-repl.py');
});

test('mapDiagnosticsToCells maps virtual-document diagnostics back to the owning cell offsets', () => {
    const document = buildVirtualNotebookDocument('/workspace', '/workspace/demo.ipynb', [
        { index: 0, cell_id: 'code-1', cell_type: 'code', source: 'import pandas as pd\n' },
        { index: 1, cell_id: 'code-2', cell_type: 'code', source: 'pd.DataFrame(\n' },
    ], 1);

    const secondCell = document.codeCells[1];
    const lineOffset = document.text.slice(0, secondCell.contentFrom).split('\n').length - 1;
    const diagnosticsByCell = mapDiagnosticsToCells(document, {
        uri: document.uri,
        diagnostics: [{
            severity: 1,
            message: 'Expected closing parenthesis',
            source: 'pyright',
            range: {
                start: { line: lineOffset, character: 12 },
                end: { line: lineOffset, character: 13 },
            },
        }],
    });

    assert.deepEqual(diagnosticsByCell['code-1'], []);
    assert.equal(diagnosticsByCell['code-2'].length, 1);
    assert.equal(diagnosticsByCell['code-2'][0].from, 12);
    assert.equal(diagnosticsByCell['code-2'][0].to, 13);
    assert.equal(diagnosticsByCell['code-2'][0].message, 'Expected closing parenthesis');
    assert.equal(diagnosticsByCell['code-2'][0].source, 'pyright');
});

test('offsetToPosition round-trips offsets inside the virtual notebook document', () => {
    const document = buildVirtualNotebookDocument('/workspace', '/workspace/demo.ipynb', [
        { index: 0, cell_id: 'code-1', cell_type: 'code', source: 'import os\nos.getcwd()\n' },
    ], 1);

    const segment = document.codeCells[0];
    const cellOffset = segment.source.indexOf('getcwd');
    const absoluteOffset = segment.contentFrom + cellOffset;
    const position = offsetToPosition(document.lineStarts, document.text.length, absoluteOffset);
    const roundTrip = positionToOffset(document.lineStarts, document.text.length, position);

    assert.equal(roundTrip, absoluteOffset);
    assert.equal(position.line >= 0, true);
    assert.equal(position.character >= 0, true);
});

test('workspace settings prefer notebook and workspace paths for notebook analysis', () => {
    const searchPaths = buildWorkspaceSearchPaths('/workspace', '/workspace/notebooks/demo.ipynb');
    assert.deepEqual(searchPaths, ['/workspace/notebooks', '/workspace']);

    const settings = defaultWorkspaceSettings('/workspace', '/workspace/notebooks/demo.ipynb');
    const pythonAnalysis = settings.python.analysis;

    assert.equal(
        pythonAnalysis.diagnosticSeverityOverrides.reportUnusedExpression,
        'none',
    );
    assert.deepEqual(
        pythonAnalysis.extraPaths,
        ['/workspace/notebooks', '/workspace'],
    );

    const [analysisConfig] = buildWorkspaceConfiguration({
        items: [{ section: 'python.analysis' }],
    }, settings);
    assert.deepEqual(analysisConfig, pythonAnalysis);
});

test('resolveDefinitionAt maps same-notebook definitions back to cell offsets', async () => {
    const client = new PyrightNotebookLspClient(
        '/workspace',
        '/workspace/demo.ipynb',
        () => {},
        () => {},
        'pyright-langserver',
    );
    const document = buildVirtualNotebookDocument('/workspace', '/workspace/demo.ipynb', [
        { index: 0, cell_id: 'code-1', cell_type: 'code', source: 'def hello():\n    pass\n\nhello()\n' },
    ], 1);
    const definitionOffset = document.codeCells[0].source.indexOf('hello');
    const absoluteDefinitionOffset = document.codeCells[0].contentFrom + definitionOffset;
    const definitionPosition = offsetToPosition(
        document.lineStarts,
        document.text.length,
        absoluteDefinitionOffset,
    );

    client.ready = true;
    client.virtualDocument = document;
    client.request = async () => ({
        uri: document.uri,
        range: {
            start: definitionPosition,
            end: { line: definitionPosition.line, character: definitionPosition.character + 'hello'.length },
        },
    });

    const target = await client.resolveDefinitionAt(
        'code-1',
        document.codeCells[0].source.lastIndexOf('hello'),
    );

    assert.deepEqual(target, {
        kind: 'cell',
        cellId: 'code-1',
        from: definitionOffset,
        to: definitionOffset + 'hello'.length,
    });
});

test('resolveDefinitionAt returns workspace file targets for external definitions', async () => {
    const client = new PyrightNotebookLspClient(
        '/workspace',
        '/workspace/demo.ipynb',
        () => {},
        () => {},
        'pyright-langserver',
    );
    const document = buildVirtualNotebookDocument('/workspace', '/workspace/demo.ipynb', [
        { index: 0, cell_id: 'code-1', cell_type: 'code', source: 'from test import hello\nhello()\n' },
    ], 1);

    client.ready = true;
    client.virtualDocument = document;
    client.request = async () => ({
        uri: 'file:///workspace/test.py',
        range: {
            start: { line: 0, character: 4 },
            end: { line: 0, character: 9 },
        },
    });

    const target = await client.resolveDefinitionAt(
        'code-1',
        document.codeCells[0].source.lastIndexOf('hello'),
    );

    assert.deepEqual(target, {
        kind: 'file',
        uri: 'file:///workspace/test.py',
        filePath: '/workspace/test.py',
        range: {
            start: { line: 0, character: 4 },
            end: { line: 0, character: 9 },
        },
    });
});

test('dispose cleans up the workspace-local Pyright shadow file', () => {
    const client = new PyrightNotebookLspClient(
        '/workspace',
        '/workspace/notebooks/demo.ipynb',
        () => {},
        () => {},
        'pyright-langserver',
    );
    const document = buildVirtualNotebookDocument('/workspace', '/workspace/notebooks/demo.ipynb', [], 1);
    const deletedPaths = [];
    const originalUnlinkSync = fs.unlinkSync;
    fs.unlinkSync = (targetPath) => {
        deletedPaths.push(targetPath);
    };

    try {
        client.virtualDocument = document;
        client.dispose();
    } finally {
        fs.unlinkSync = originalUnlinkSync;
    }

    assert.deepEqual(deletedPaths, ['/workspace/.agent-repl/pyright/notebooks/demo.ipynb.agent-repl.py']);
});

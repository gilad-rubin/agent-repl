const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    buildVirtualDocument,
    mapDiagnosticsToCells,
    cellOffsetToVirtualOffset,
    computeLineStarts,
    offsetToPosition,
    positionToOffset,
} = require(path.resolve(__dirname, '../out/shared/notebookVirtualDocument.js'));

test('buildVirtualDocument combines code cells with headers', () => {
    const doc = buildVirtualDocument([
        { index: 0, cell_id: 'c1', cell_type: 'code', source: 'x = 1' },
        { index: 1, cell_id: 'c2', cell_type: 'markdown', source: '# Title' },
        { index: 2, cell_id: 'c3', cell_type: 'code', source: 'y = 2' },
    ], 1);

    assert.equal(doc.codeCells.length, 2);
    assert.equal(doc.codeCells[0].cell_id, 'c1');
    assert.equal(doc.codeCells[1].cell_id, 'c3');
    assert.ok(doc.text.includes('# %% [agent-repl cell c1]'));
    assert.ok(doc.text.includes('x = 1'));
    assert.ok(doc.text.includes('y = 2'));
    assert.ok(!doc.text.includes('# Title'));
});

test('buildVirtualDocument skips markdown cells', () => {
    const doc = buildVirtualDocument([
        { index: 0, cell_id: 'md', cell_type: 'markdown', source: '## Heading' },
    ], 1);

    assert.equal(doc.codeCells.length, 0);
    assert.equal(doc.text, '');
});

test('mapDiagnosticsToCells maps diagnostic to correct cell', () => {
    const doc = buildVirtualDocument([
        { index: 0, cell_id: 'c1', cell_type: 'code', source: 'bad syntax' },
        { index: 1, cell_id: 'c2', cell_type: 'code', source: 'good code' },
    ], 1);

    const startPos = offsetToPosition(doc.lineStarts, doc.text.length, doc.codeCells[0].contentFrom);
    const endPos = offsetToPosition(doc.lineStarts, doc.text.length, doc.codeCells[0].contentFrom + 3);

    const result = mapDiagnosticsToCells(doc, [{
        range: { start: startPos, end: endPos },
        severity: 1,
        message: 'Syntax error',
        source: 'Pyright',
    }]);

    assert.equal(result['c1'].length, 1);
    assert.equal(result['c1'][0].from, 0);
    assert.equal(result['c1'][0].to, 3);
    assert.equal(result['c1'][0].severity, 'error');
    assert.equal(result['c2'].length, 0);
});

test('cellOffsetToVirtualOffset converts cell-relative to virtual offset', () => {
    const doc = buildVirtualDocument([
        { index: 0, cell_id: 'c1', cell_type: 'code', source: 'hello world' },
    ], 1);

    const virtualOffset = cellOffsetToVirtualOffset(doc, 'c1', 5);
    assert.equal(virtualOffset, doc.codeCells[0].contentFrom + 5);
});

test('cellOffsetToVirtualOffset returns null for unknown cell', () => {
    const doc = buildVirtualDocument([
        { index: 0, cell_id: 'c1', cell_type: 'code', source: 'x = 1' },
    ], 1);

    assert.equal(cellOffsetToVirtualOffset(doc, 'unknown', 0), null);
});

test('computeLineStarts finds all newline positions', () => {
    const starts = computeLineStarts('abc\ndef\nghi');
    assert.deepEqual(starts, [0, 4, 8]);
});

test('positionToOffset and offsetToPosition round-trip', () => {
    const text = 'line one\nline two\nline three';
    const starts = computeLineStarts(text);
    const pos = offsetToPosition(starts, text.length, 14);
    const offset = positionToOffset(starts, text.length, pos);
    assert.equal(offset, 14);
});

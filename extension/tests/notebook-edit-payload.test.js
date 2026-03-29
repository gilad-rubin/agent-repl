const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    buildReplaceSourceOperation,
    buildReplaceSourceOperations,
} = require(path.resolve(__dirname, '../out/shared/notebookEditPayload.js'));

test('buildReplaceSourceOperation includes cell_index only when provided', () => {
    assert.deepEqual(
        buildReplaceSourceOperation({
            cell_id: 'cell-1',
            cell_index: 2,
            source: 'x = 1',
        }),
        {
            op: 'replace-source',
            cell_id: 'cell-1',
            cell_index: 2,
            source: 'x = 1',
        },
    );

    assert.deepEqual(
        buildReplaceSourceOperation({
            cell_id: 'cell-2',
            source: 'y = 2',
        }),
        {
            op: 'replace-source',
            cell_id: 'cell-2',
            source: 'y = 2',
        },
    );
});

test('buildReplaceSourceOperations maps draft changes into notebook edit operations', () => {
    assert.deepEqual(
        buildReplaceSourceOperations([
            { cell_id: 'cell-1', source: 'x = 1' },
            { cell_id: 'cell-2', source: 'y = 2', cell_index: 4 },
        ]),
        [
            { op: 'replace-source', cell_id: 'cell-1', source: 'x = 1' },
            { op: 'replace-source', cell_id: 'cell-2', cell_index: 4, source: 'y = 2' },
        ],
    );
});

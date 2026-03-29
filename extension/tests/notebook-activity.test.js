const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    collectInlineCellSourceUpdates,
    isNotebookStructureReloadEvent,
    shouldReloadStandaloneNotebookContents,
} = require(path.resolve(__dirname, '../out/shared/notebookActivity.js'));

test('isNotebookStructureReloadEvent matches structural notebook mutations only', () => {
    assert.equal(isNotebookStructureReloadEvent('cell-inserted'), true);
    assert.equal(isNotebookStructureReloadEvent('cell-removed'), true);
    assert.equal(isNotebookStructureReloadEvent('notebook-reset-needed'), true);
    assert.equal(isNotebookStructureReloadEvent('cell-source-updated'), false);
});

test('shouldReloadStandaloneNotebookContents reloads for source and structural events', () => {
    assert.equal(
        shouldReloadStandaloneNotebookContents([{ type: 'cell-source-updated' }]),
        true,
    );
    assert.equal(
        shouldReloadStandaloneNotebookContents([{ type: 'cell-inserted' }]),
        true,
    );
    assert.equal(
        shouldReloadStandaloneNotebookContents([{ type: 'presence-updated' }]),
        false,
    );
});

test('collectInlineCellSourceUpdates returns only concrete cell payloads', () => {
    assert.deepEqual(
        collectInlineCellSourceUpdates([
            { type: 'cell-source-updated', data: { cell: { cell_id: 'cell-1', source: 'x = 1' } } },
            { type: 'cell-source-updated', data: {} },
            { type: 'cell-removed', data: { cell_id: 'cell-2' } },
        ]),
        [{ cell_id: 'cell-1', source: 'x = 1' }],
    );
});

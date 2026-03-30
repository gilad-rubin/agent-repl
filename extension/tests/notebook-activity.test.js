const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    buildActivityPollResult,
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

test('buildActivityPollResult preserves proxy-style inline source updates without forcing a reload', () => {
    const result = buildActivityPollResult({
        recent_events: [
            { event_id: '1', path: 'demo.ipynb', type: 'cell-source-updated', detail: '', actor: 'human', session_id: 'sess', cell_id: 'cell-1', cell_index: 0, data: { cell: { cell_id: 'cell-1', source: 'print(1)' } }, timestamp: 1 },
        ],
        runtime: {
            busy: true,
        },
    }, {
        cursorFallback: 7,
        includeDetachedRuntime: true,
        inlineSourceUpdates: true,
        reloadOnSourceUpdates: false,
    });

    assert.deepEqual(result.sourceUpdates, [{ cell_id: 'cell-1', source: 'print(1)' }]);
    assert.equal(result.shouldReloadContents, false);
    assert.equal(result.shouldSyncLsp, true);
    assert.equal(result.activityUpdate?.cursor, 7);
    assert.equal(result.activityUpdate?.events[0]?.event_type, 'cell-source-updated');
  });

test('buildActivityPollResult preserves standalone reload-on-source-update behavior', () => {
    const result = buildActivityPollResult({
        recent_events: [
            { event_id: '1', path: 'demo.ipynb', type: 'cell-source-updated', detail: '', actor: 'human', session_id: 'sess', cell_id: 'cell-1', cell_index: 0, data: { cell: { cell_id: 'cell-1', source: 'print(1)' } }, timestamp: 1 },
        ],
    }, {
        cursorFallback: 3,
        reloadOnSourceUpdates: true,
        inlineSourceUpdates: false,
    });

    assert.deepEqual(result.sourceUpdates, [{ cell_id: 'cell-1', source: 'print(1)' }]);
    assert.equal(result.shouldReloadContents, true);
    assert.equal(result.shouldSyncLsp, false);
    assert.equal(result.activityUpdate?.cursor, 3);
  });

test('buildActivityPollResult skips activity updates when there are no events and no runtime payload', () => {
    const result = buildActivityPollResult({
        recent_events: [],
    }, {
        cursorFallback: 11,
        includeDetachedRuntime: true,
    });

    assert.deepEqual(result.sourceUpdates, []);
    assert.equal(result.shouldReloadContents, false);
    assert.equal(result.shouldSyncLsp, false);
    assert.equal(result.activityUpdate, null);
});

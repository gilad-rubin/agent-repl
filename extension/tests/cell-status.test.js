const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const modulePath = path.resolve(__dirname, '../out/shared/cellStatus.js');

function loadCellStatusModule() {
    delete require.cache[modulePath];
    return require(modulePath);
}

test('deriveCellStatusKind prefers running over queued when both flags are present', () => {
    const { deriveCellStatusKind } = loadCellStatusModule();

    const status = deriveCellStatusKind({
        isQueued: true,
        isExecuting: true,
        isPaused: false,
        hasLocalFailure: false,
        hasCompletedThisSession: false,
        hasLiveRuntimeContext: true,
        hasRuntimeMatchedFailure: false,
        hasRuntimeMatchedCompletion: false,
    });

    assert.equal(status, 'running');
});

test('deriveCellStatusKind prefers paused over completed when a cell was skipped after failure', () => {
    const { deriveCellStatusKind } = loadCellStatusModule();

    const status = deriveCellStatusKind({
        isQueued: false,
        isExecuting: false,
        isPaused: true,
        hasLocalFailure: false,
        hasCompletedThisSession: true,
        hasLiveRuntimeContext: true,
        hasRuntimeMatchedFailure: false,
        hasRuntimeMatchedCompletion: false,
    });

    assert.equal(status, 'paused');
});

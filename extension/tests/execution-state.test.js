const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const modulePath = path.resolve(__dirname, '../out/shared/executionState.js');

function loadExecutionStateModule() {
    delete require.cache[modulePath];
    return require(modulePath);
}

test('resolveIdleExecutionTransition pauses queued cells after a failure instead of completing them', () => {
    const { resolveIdleExecutionTransition } = loadExecutionStateModule();

    const result = resolveIdleExecutionTransition({
        queuedIds: ['cell-6'],
        executingIds: ['cell-5'],
        failedCellIds: ['cell-5'],
    });

    assert.deepEqual(result, {
        completedIds: [],
        pausedIds: ['cell-6'],
    });
});

test('resolveIdleExecutionTransition only completes executing cells when no failure occurred', () => {
    const { resolveIdleExecutionTransition } = loadExecutionStateModule();

    const result = resolveIdleExecutionTransition({
        queuedIds: [],
        executingIds: ['cell-4'],
        failedCellIds: [],
    });

    assert.deepEqual(result, {
        completedIds: ['cell-4'],
        pausedIds: [],
    });
});

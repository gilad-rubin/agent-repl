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

test('queueExecutionBuckets removes newly queued cells from executing, failed, and paused sets', () => {
    const { queueExecutionBuckets } = loadExecutionStateModule();

    const result = queueExecutionBuckets({
        queuedIds: ['cell-1'],
        executingIds: ['cell-2'],
        failedCellIds: ['cell-2', 'cell-3'],
        pausedCellIds: ['cell-3', 'cell-4'],
    }, ['cell-2', 'cell-3']);

    assert.deepEqual(result, {
        queuedIds: ['cell-1', 'cell-2', 'cell-3'],
        executingIds: [],
        failedCellIds: [],
        pausedCellIds: ['cell-4'],
    });
});

test('startExecutionBuckets promotes cells into executing while clearing queued, failed, and paused entries', () => {
    const { startExecutionBuckets } = loadExecutionStateModule();

    const result = startExecutionBuckets({
        queuedIds: ['cell-1', 'cell-2'],
        executingIds: ['cell-3'],
        failedCellIds: ['cell-2', 'cell-4'],
        pausedCellIds: ['cell-1', 'cell-5'],
    }, ['cell-1', 'cell-4']);

    assert.deepEqual(result, {
        queuedIds: ['cell-2'],
        executingIds: ['cell-3', 'cell-1', 'cell-4'],
        failedCellIds: ['cell-2'],
        pausedCellIds: ['cell-5'],
    });
});

test('syncExecutionBuckets keeps server-owned queued and running ids while clearing stale failures and pauses', () => {
    const { syncExecutionBuckets } = loadExecutionStateModule();

    const result = syncExecutionBuckets({
        queuedIds: ['old-queued'],
        executingIds: ['old-running'],
        failedCellIds: ['cell-queued', 'cell-failed'],
        pausedCellIds: ['cell-running', 'cell-paused'],
    }, {
        queuedIds: ['cell-queued'],
        executingIds: ['cell-running'],
    });

    assert.deepEqual(result, {
        queuedIds: ['cell-queued'],
        executingIds: ['cell-running'],
        failedCellIds: ['cell-failed'],
        pausedCellIds: ['cell-paused'],
    });
});

test('reduceActivityExecution promotes output and started events into executing and flags structural reloads', () => {
    const { reduceActivityExecution } = loadExecutionStateModule();

    const result = reduceActivityExecution({
        queuedIds: ['cell-queued', 'cell-waiting'],
        executingIds: [],
        failedCellIds: ['cell-failed'],
        pausedCellIds: ['cell-paused'],
    }, [
        { event_type: 'cell-output-appended', cell_id: 'cell-queued' },
        { event_type: 'execution-started', cell_id: 'cell-paused' },
        { event_type: 'cell-inserted' },
    ]);

    assert.deepEqual(result, {
        buckets: {
            queuedIds: ['cell-waiting'],
            executingIds: ['cell-queued', 'cell-paused'],
            failedCellIds: ['cell-failed'],
            pausedCellIds: [],
        },
        startedIds: ['cell-queued', 'cell-paused'],
        finishedIds: [],
        needsFullReload: true,
    });
});

test('reduceActivityExecution clears execution buckets on finished events', () => {
    const { reduceActivityExecution } = loadExecutionStateModule();

    const result = reduceActivityExecution({
        queuedIds: ['cell-1'],
        executingIds: ['cell-2'],
        failedCellIds: [],
        pausedCellIds: ['cell-3'],
    }, [
        { event_type: 'execution-finished', cell_id: 'cell-1' },
        { event_type: 'execution-finished', cell_id: 'cell-2' },
        { event_type: 'execution-finished', cell_id: 'cell-3' },
    ]);

    assert.deepEqual(result, {
        buckets: {
            queuedIds: [],
            executingIds: [],
            failedCellIds: [],
            pausedCellIds: [],
        },
        startedIds: [],
        finishedIds: ['cell-1', 'cell-2', 'cell-3'],
        needsFullReload: false,
    });
});

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

test('primeBulkExecutionBuckets starts the first bulk cell immediately while queueing the rest', () => {
    const { primeBulkExecutionBuckets } = loadExecutionStateModule();

    const result = primeBulkExecutionBuckets({
        queuedIds: ['old-queued'],
        executingIds: ['old-running'],
        failedCellIds: ['cell-1', 'cell-2', 'cell-3'],
        pausedCellIds: ['cell-1', 'cell-3', 'cell-paused'],
    }, ['cell-1', 'cell-2', 'cell-3']);

    assert.deepEqual(result, {
        queuedIds: ['old-queued', 'cell-2', 'cell-3'],
        executingIds: ['old-running', 'cell-1'],
        failedCellIds: [],
        pausedCellIds: ['cell-paused'],
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

test('reduceCommandExecution promotes a directly started cell into executing and clears queued failure state', () => {
    const { reduceCommandExecution } = loadExecutionStateModule();

    const result = reduceCommandExecution({
        queuedIds: ['cell-1', 'cell-2'],
        executingIds: ['cell-3'],
        failedCellIds: ['cell-1', 'cell-4'],
        pausedCellIds: ['cell-1', 'cell-5'],
    }, {
        type: 'execute-started',
        cell_id: 'cell-1',
    });

    assert.deepEqual(result, {
        buckets: {
            queuedIds: ['cell-2'],
            executingIds: ['cell-3', 'cell-1'],
            failedCellIds: ['cell-4'],
            pausedCellIds: ['cell-5'],
        },
        startedIds: ['cell-1'],
        completedIds: [],
        failedIds: [],
    });
});

test('reduceCommandExecution marks failed direct executions without leaving stale queued or paused state', () => {
    const { reduceCommandExecution } = loadExecutionStateModule();

    const result = reduceCommandExecution({
        queuedIds: ['cell-1'],
        executingIds: ['cell-2'],
        failedCellIds: ['cell-3'],
        pausedCellIds: ['cell-2', 'cell-4'],
    }, {
        type: 'execute-finished',
        cell_id: 'cell-2',
        ok: false,
    });

    assert.deepEqual(result, {
        buckets: {
            queuedIds: ['cell-1'],
            executingIds: [],
            failedCellIds: ['cell-3', 'cell-2'],
            pausedCellIds: ['cell-4'],
        },
        startedIds: [],
        completedIds: [],
        failedIds: ['cell-2'],
    });
});

test('reduceCommandExecution marks successful direct executions as completed and clears old failures', () => {
    const { reduceCommandExecution } = loadExecutionStateModule();

    const result = reduceCommandExecution({
        queuedIds: ['cell-1'],
        executingIds: ['cell-2'],
        failedCellIds: ['cell-2', 'cell-3'],
        pausedCellIds: ['cell-4'],
    }, {
        type: 'execute-finished',
        cell_id: 'cell-2',
        ok: true,
    });

    assert.deepEqual(result, {
        buckets: {
            queuedIds: ['cell-1'],
            executingIds: [],
            failedCellIds: ['cell-3'],
            pausedCellIds: ['cell-4'],
        },
        startedIds: [],
        completedIds: ['cell-2'],
        failedIds: [],
    });
});

test('reduceRuntimeExecution syncs server-owned running and queued ids while only marking newly running cells as started', () => {
    const { reduceRuntimeExecution } = loadExecutionStateModule();

    const result = reduceRuntimeExecution({
        queuedIds: ['cell-queued-old'],
        executingIds: ['cell-running-still'],
        failedCellIds: ['cell-running-new', 'cell-failed'],
        pausedCellIds: ['cell-queued-new', 'cell-paused'],
    }, {
        busy: true,
        running_cell_ids: ['cell-running-still', 'cell-running-new'],
        queued_cell_ids: ['cell-queued-new', 'cell-running-new'],
    });

    assert.deepEqual(result, {
        buckets: {
            queuedIds: ['cell-queued-new'],
            executingIds: ['cell-running-still', 'cell-running-new'],
            failedCellIds: ['cell-failed'],
            pausedCellIds: ['cell-paused'],
        },
        startedIds: ['cell-running-new'],
        completedIds: [],
        pausedIds: [],
    });
});

test('reduceRuntimeExecution resolves queued and running work into completed and paused buckets when runtime goes idle', () => {
    const { reduceRuntimeExecution } = loadExecutionStateModule();

    const result = reduceRuntimeExecution({
        queuedIds: ['cell-queued'],
        executingIds: ['cell-running', 'cell-failed'],
        failedCellIds: ['cell-failed'],
        pausedCellIds: ['cell-paused-existing', 'cell-running'],
    }, {
        busy: false,
        current_execution: null,
        running_cell_ids: [],
        queued_cell_ids: [],
    });

    assert.deepEqual(result, {
        buckets: {
            queuedIds: [],
            executingIds: [],
            failedCellIds: ['cell-failed'],
            pausedCellIds: ['cell-paused-existing', 'cell-queued'],
        },
        startedIds: [],
        completedIds: ['cell-running'],
        pausedIds: ['cell-queued'],
    });
});

export type ExecutionBuckets = {
    queuedIds: string[];
    executingIds: string[];
    failedCellIds: string[];
    pausedCellIds: string[];
};

export type IdleExecutionTransitionInputs = {
    queuedIds: string[];
    executingIds: string[];
    failedCellIds: string[];
};

export type IdleExecutionTransition = {
    completedIds: string[];
    pausedIds: string[];
};

export type ActivityExecutionEventLike = {
    event_type?: string;
    type?: string;
    cell_id?: string | null;
};

export type ActivityExecutionReduction = {
    buckets: ExecutionBuckets;
    startedIds: string[];
    finishedIds: string[];
    needsFullReload: boolean;
};

export function queueExecutionBuckets(
    current: ExecutionBuckets,
    cellIds: string[],
): ExecutionBuckets {
    if (cellIds.length === 0) {
        return current;
    }
    const nextQueuedIds = Array.from(new Set(cellIds));
    return {
        queuedIds: Array.from(new Set([...current.queuedIds, ...nextQueuedIds])),
        executingIds: current.executingIds.filter((cellId) => !nextQueuedIds.includes(cellId)),
        failedCellIds: current.failedCellIds.filter((cellId) => !nextQueuedIds.includes(cellId)),
        pausedCellIds: current.pausedCellIds.filter((cellId) => !nextQueuedIds.includes(cellId)),
    };
}

export function startExecutionBuckets(
    current: ExecutionBuckets,
    cellIds: string[],
): ExecutionBuckets {
    if (cellIds.length === 0) {
        return current;
    }
    const nextExecutingIds = Array.from(new Set(cellIds));
    return {
        queuedIds: current.queuedIds.filter((cellId) => !nextExecutingIds.includes(cellId)),
        executingIds: Array.from(new Set([...current.executingIds, ...nextExecutingIds])),
        failedCellIds: current.failedCellIds.filter((cellId) => !nextExecutingIds.includes(cellId)),
        pausedCellIds: current.pausedCellIds.filter((cellId) => !nextExecutingIds.includes(cellId)),
    };
}

export function syncExecutionBuckets(
    current: ExecutionBuckets,
    active: {
        queuedIds: string[];
        executingIds: string[];
    },
): ExecutionBuckets {
    const activeIds = new Set([...active.queuedIds, ...active.executingIds]);
    return {
        queuedIds: [...active.queuedIds],
        executingIds: [...active.executingIds],
        failedCellIds: current.failedCellIds.filter((cellId) => !activeIds.has(cellId)),
        pausedCellIds: current.pausedCellIds.filter((cellId) => !activeIds.has(cellId)),
    };
}

export function reduceActivityExecution(
    current: ExecutionBuckets,
    events: ActivityExecutionEventLike[],
): ActivityExecutionReduction {
    const nextQueued = new Set(current.queuedIds);
    const nextExecuting = new Set(current.executingIds);
    const nextPaused = new Set(current.pausedCellIds);
    const startedIds: string[] = [];
    const finishedIds: string[] = [];
    let needsFullReload = false;

    for (const event of events) {
        const eventType = event.event_type ?? event.type;
        const cellId = typeof event.cell_id === 'string' && event.cell_id ? event.cell_id : null;

        if (
            (eventType === 'cell-output-appended' || eventType === 'cell-outputs-updated' || eventType === 'execution-started') &&
            cellId
        ) {
            nextQueued.delete(cellId);
            nextExecuting.add(cellId);
            nextPaused.delete(cellId);
            if (!startedIds.includes(cellId)) {
                startedIds.push(cellId);
            }
            continue;
        }

        if (eventType === 'execution-finished' && cellId) {
            nextQueued.delete(cellId);
            nextExecuting.delete(cellId);
            nextPaused.delete(cellId);
            if (!finishedIds.includes(cellId)) {
                finishedIds.push(cellId);
            }
            continue;
        }

        if (
            eventType === 'cell-inserted' ||
            eventType === 'cell-removed' ||
            eventType === 'notebook-reset-needed'
        ) {
            needsFullReload = true;
        }
    }

    return {
        buckets: {
            queuedIds: [...nextQueued],
            executingIds: [...nextExecuting],
            failedCellIds: current.failedCellIds,
            pausedCellIds: [...nextPaused],
        },
        startedIds,
        finishedIds,
        needsFullReload,
    };
}

export function resolveIdleExecutionTransition({
    queuedIds,
    executingIds,
    failedCellIds,
}: IdleExecutionTransitionInputs): IdleExecutionTransition {
    const failedSet = new Set(failedCellIds);
    const completedIds = executingIds.filter((cellId) => !failedSet.has(cellId));
    const pausedIds = failedCellIds.length > 0
        ? queuedIds.filter((cellId) => !failedSet.has(cellId))
        : [];
    return { completedIds, pausedIds };
}

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

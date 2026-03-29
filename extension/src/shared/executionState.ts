export type IdleExecutionTransitionInputs = {
    queuedIds: string[];
    executingIds: string[];
    failedCellIds: string[];
};

export type IdleExecutionTransition = {
    completedIds: string[];
    pausedIds: string[];
};

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

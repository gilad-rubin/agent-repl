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

export type ActivityRuntimeExecutionReduction = {
    buckets: ExecutionBuckets;
    startedIds: string[];
    completedIds: string[];
    pausedIds: string[];
    needsFullReload: boolean;
};

export type CommandExecutionMessageLike = {
    type?: string;
    cell_id?: string | null;
    ok?: boolean | null;
};

export type CommandExecutionReduction = {
    buckets: ExecutionBuckets;
    startedIds: string[];
    completedIds: string[];
    failedIds: string[];
};

export type RuntimeExecutionLike = {
    busy?: boolean;
    current_execution?: unknown;
    running_cell_ids?: string[];
    queued_cell_ids?: string[];
};

export type RuntimeExecutionReduction = {
    buckets: ExecutionBuckets;
    startedIds: string[];
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

export function primeBulkExecutionBuckets(
    current: ExecutionBuckets,
    cellIds: string[],
): ExecutionBuckets {
    if (cellIds.length === 0) {
        return current;
    }

    const [firstCellId, ...remainingCellIds] = Array.from(new Set(cellIds));
    const queued = queueExecutionBuckets(current, remainingCellIds);
    return startExecutionBuckets(queued, [firstCellId]);
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

export function reduceActivityAndRuntimeExecution(
    current: ExecutionBuckets,
    events: ActivityExecutionEventLike[],
    runtime?: RuntimeExecutionLike,
): ActivityRuntimeExecutionReduction {
    const activityReduction = reduceActivityExecution(current, events);
    if (!runtime) {
        return {
            buckets: activityReduction.buckets,
            startedIds: activityReduction.startedIds,
            completedIds: activityReduction.finishedIds,
            pausedIds: [],
            needsFullReload: activityReduction.needsFullReload,
        };
    }

    const runtimeReduction = reduceRuntimeExecution(activityReduction.buckets, runtime);
    return {
        buckets: runtimeReduction.buckets,
        startedIds: Array.from(new Set([
            ...activityReduction.startedIds,
            ...runtimeReduction.startedIds,
        ])),
        completedIds: Array.from(new Set([
            ...activityReduction.finishedIds,
            ...runtimeReduction.completedIds,
        ])),
        pausedIds: runtimeReduction.pausedIds,
        needsFullReload: activityReduction.needsFullReload,
    };
}

export function reduceCommandExecution(
    current: ExecutionBuckets,
    message: CommandExecutionMessageLike,
): CommandExecutionReduction {
    const cellId = typeof message.cell_id === 'string' && message.cell_id ? message.cell_id : null;
    if (!cellId) {
        return {
            buckets: current,
            startedIds: [],
            completedIds: [],
            failedIds: [],
        };
    }

    if (message.type === 'execute-started') {
        return {
            buckets: startExecutionBuckets(current, [cellId]),
            startedIds: [cellId],
            completedIds: [],
            failedIds: [],
        };
    }

    const failed = message.type === 'execute-failed' || (message.type === 'execute-finished' && message.ok === false);
    if (message.type === 'execute-finished' || message.type === 'execute-failed') {
        return {
            buckets: {
                queuedIds: current.queuedIds.filter((entry) => entry !== cellId),
                executingIds: current.executingIds.filter((entry) => entry !== cellId),
                failedCellIds: failed
                    ? current.failedCellIds.includes(cellId)
                        ? current.failedCellIds
                        : [...current.failedCellIds, cellId]
                    : current.failedCellIds.filter((entry) => entry !== cellId),
                pausedCellIds: current.pausedCellIds.filter((entry) => entry !== cellId),
            },
            startedIds: [],
            completedIds: failed ? [] : [cellId],
            failedIds: failed ? [cellId] : [],
        };
    }

    return {
        buckets: current,
        startedIds: [],
        completedIds: [],
        failedIds: [],
    };
}

export function reduceRuntimeExecution(
    current: ExecutionBuckets,
    runtime: RuntimeExecutionLike,
): RuntimeExecutionReduction {
    const executingIds = Array.isArray(runtime.running_cell_ids)
        ? runtime.running_cell_ids
        : [];
    const queuedIds = Array.isArray(runtime.queued_cell_ids)
        ? runtime.queued_cell_ids.filter((cellId) => !executingIds.includes(cellId))
        : [];

    if (executingIds.length > 0 || queuedIds.length > 0) {
        const buckets = syncExecutionBuckets(current, {
            queuedIds,
            executingIds,
        });
        return {
            buckets,
            startedIds: executingIds.filter((cellId) => !current.executingIds.includes(cellId)),
            completedIds: [],
            pausedIds: [],
        };
    }

    if (!runtime.busy && !runtime.current_execution && (current.executingIds.length > 0 || current.queuedIds.length > 0)) {
        const { completedIds, pausedIds } = resolveIdleExecutionTransition({
            queuedIds: current.queuedIds,
            executingIds: current.executingIds,
            failedCellIds: current.failedCellIds,
        });
        return {
            buckets: {
                queuedIds: [],
                executingIds: [],
                failedCellIds: current.failedCellIds,
                pausedCellIds: Array.from(new Set([
                    ...current.pausedCellIds.filter((cellId) => !current.executingIds.includes(cellId)),
                    ...pausedIds,
                ])),
            },
            startedIds: [],
            completedIds,
            pausedIds,
        };
    }

    return {
        buckets: current,
        startedIds: [],
        completedIds: [],
        pausedIds: [],
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

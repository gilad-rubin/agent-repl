export type CellStatusKind = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | null;

export type CellStatusInputs = {
    isQueued: boolean;
    isExecuting: boolean;
    isPaused: boolean;
    hasLocalFailure: boolean;
    hasCompletedThisSession: boolean;
    hasLiveRuntimeContext: boolean;
    hasRuntimeMatchedFailure: boolean;
    hasRuntimeMatchedCompletion: boolean;
};

export function deriveCellStatusKind({
    isQueued,
    isExecuting,
    isPaused,
    hasLocalFailure,
    hasCompletedThisSession,
    hasLiveRuntimeContext,
    hasRuntimeMatchedFailure,
    hasRuntimeMatchedCompletion,
}: CellStatusInputs): CellStatusKind {
    if (isExecuting) {
        return 'running';
    }
    if (isQueued) {
        return 'queued';
    }
    if (hasLocalFailure) {
        return 'failed';
    }
    if (isPaused) {
        return 'paused';
    }
    if (hasCompletedThisSession) {
        return 'completed';
    }
    if (!hasLiveRuntimeContext) {
        return null;
    }
    if (hasRuntimeMatchedFailure) {
        return 'failed';
    }
    if (hasRuntimeMatchedCompletion) {
        return 'completed';
    }
    return null;
}

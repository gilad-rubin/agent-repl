import { buildActivitySnapshot, type ActivityEnvelopeLike, type ActivitySnapshot } from './runtimeSnapshot';

type ActivityEventLike = {
    type: string;
    data?: unknown;
};

export type CellSourceUpdatedEvent = ActivityEventLike & {
    type: 'cell-source-updated';
    data?: {
        cell?: unknown;
    };
};

export type ActivityPollResult = {
    sourceUpdates: unknown[];
    shouldReloadContents: boolean;
    shouldSyncLsp: boolean;
    activityUpdate: ActivitySnapshot | null;
};

export function isNotebookStructureReloadEvent(eventType: string): boolean {
    return (
        eventType === 'cell-inserted'
        || eventType === 'cell-removed'
        || eventType === 'notebook-reset-needed'
    );
}

export function shouldReloadStandaloneNotebookContents(events: ActivityEventLike[]): boolean {
    return events.some((event) => (
        event.type === 'cell-source-updated'
        || event.type === 'cell-executed'
        || event.type === 'cell-output-appended'
        || event.type === 'cell-outputs-updated'
        || event.type === 'execution-started'
        || event.type === 'execution-finished'
        || event.type === 'execution'
        || isNotebookStructureReloadEvent(event.type)
    ));
}

export function collectInlineCellSourceUpdates(events: ActivityEventLike[]): unknown[] {
    return events.flatMap((event) => {
        if (event.type !== 'cell-source-updated') {
            return [];
        }
        const data = (event as CellSourceUpdatedEvent).data;
        return data?.cell !== undefined ? [data.cell] : [];
    });
}

export function buildActivityPollResult(
    payload: ActivityEnvelopeLike,
    options?: {
        cursorFallback?: number;
        includeDetachedRuntime?: boolean;
        reloadOnSourceUpdates?: boolean;
        inlineSourceUpdates?: boolean;
    },
): ActivityPollResult {
    const events = payload.recent_events ?? [];
    const sourceUpdates = collectInlineCellSourceUpdates(events);
    const shouldReloadContents = events.some((event) => (
        isNotebookStructureReloadEvent(event.type)
        || (options?.reloadOnSourceUpdates === true && event.type === 'cell-source-updated')
    ));
    const shouldSyncLsp = options?.inlineSourceUpdates === true
        && sourceUpdates.length > 0
        && !shouldReloadContents;
    const activityUpdate = events.length === 0 && !payload.runtime
        ? null
        : buildActivitySnapshot(payload, {
            cursorFallback: options?.cursorFallback,
            includeDetachedRuntime: options?.includeDetachedRuntime,
        });
    return {
        sourceUpdates,
        shouldReloadContents,
        shouldSyncLsp,
        activityUpdate,
    };
}

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

export function isNotebookStructureReloadEvent(eventType: string): boolean {
    return (
        eventType === 'cell-inserted'
        || eventType === 'cell-removed'
        || eventType === 'notebook-reset-needed'
    );
}

export function shouldReloadStandaloneNotebookContents(events: ActivityEventLike[]): boolean {
    return events.some((event) => (
        event.type === 'cell-source-updated' || isNotebookStructureReloadEvent(event.type)
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

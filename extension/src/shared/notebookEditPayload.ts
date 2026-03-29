export type ReplaceSourceChange = {
    cell_id: string;
    source: string;
    cell_index?: number;
};

export function buildReplaceSourceOperation(change: ReplaceSourceChange): {
    op: 'replace-source';
    cell_id: string;
    source: string;
    cell_index?: number;
} {
    return {
        op: 'replace-source',
        cell_id: change.cell_id,
        ...(typeof change.cell_index === 'number' ? { cell_index: change.cell_index } : {}),
        source: change.source,
    };
}

export function buildReplaceSourceOperations(
    changes: ReplaceSourceChange[],
): Array<ReturnType<typeof buildReplaceSourceOperation>> {
    return changes.map((change) => buildReplaceSourceOperation(change));
}

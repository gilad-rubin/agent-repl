import * as vscode from 'vscode';
import * as crypto from 'crypto';

const NS = 'agent-repl';

/** Get the stable agent-repl cell ID. Falls back to Jupyter's ID, then undefined. */
export function getCellId(cell: vscode.NotebookCell): string | undefined {
    const m = cell.metadata as Record<string, any> | undefined;
    return m?.custom?.[NS]?.cell_id ?? m?.custom?.id ?? m?.id;
}

/** True if the cell has an ID in our controlled namespace. */
function hasOwnId(cell: vscode.NotebookCell): boolean {
    return !!(cell.metadata as any)?.custom?.[NS]?.cell_id;
}

/** Resolve cell_id or cell_index to a cell index. Prefers cell_id. */
export function resolveCell(
    doc: vscode.NotebookDocument,
    sel: { cell_id?: string; cell_index?: number }
): number {
    if (sel.cell_id) {
        for (let i = 0; i < doc.cellCount; i++) {
            if (getCellId(doc.cellAt(i)) === sel.cell_id) { return i; }
        }
        const fallbackMatch = /^index-(\d+)$/.exec(sel.cell_id);
        if (fallbackMatch) {
            const fallbackIndex = Number.parseInt(fallbackMatch[1], 10);
            if (fallbackIndex >= 0 && fallbackIndex < doc.cellCount) {
                return fallbackIndex;
            }
        }
        const err = new Error(`No cell matched id '${sel.cell_id}'`) as any;
        err.statusCode = 404;
        throw err;
    }
    if (sel.cell_index !== undefined) {
        if (sel.cell_index < 0 || sel.cell_index >= doc.cellCount) {
            const err = new Error(`Cell index ${sel.cell_index} out of range (${doc.cellCount} cells)`) as any;
            err.statusCode = 400;
            throw err;
        }
        return sel.cell_index;
    }
    throw new Error('Provide cell_id or cell_index');
}

/** Build metadata with an agent-repl cell ID. Merges with existing. */
export function withCellId(cellId: string, existing?: Record<string, any>): Record<string, any> {
    const custom = { ...(existing?.custom ?? {}), [NS]: { ...(existing?.custom?.[NS] ?? {}), cell_id: cellId } };
    return { ...(existing ?? {}), custom };
}

/** Generate a UUID for a new cell. */
export function newCellId(): string {
    return crypto.randomUUID();
}

/** Stamp any cells missing our agent-repl ID. Idempotent. */
export async function ensureIds(doc: vscode.NotebookDocument): Promise<void> {
    const edit = new vscode.WorkspaceEdit();
    let dirty = false;
    for (let i = 0; i < doc.cellCount; i++) {
        const cell = doc.cellAt(i);
        if (!hasOwnId(cell)) {
            const id = newCellId();
            edit.set(doc.uri, [vscode.NotebookEdit.updateCellMetadata(i, withCellId(id, cell.metadata as Record<string, any>))]);
            dirty = true;
        }
    }
    if (dirty) { await vscode.workspace.applyEdit(edit); }
}

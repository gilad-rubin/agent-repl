import * as vscode from 'vscode';
import { resolveCell, newCellId, withCellId, getCellId } from './identity';

export type EditOp =
    | { op: 'replace-source'; cell_id?: string; cell_index?: number; source: string }
    | { op: 'insert'; cell_type: 'code' | 'markdown' | 'raw'; source: string; at_index: number; metadata?: Record<string, any> }
    | { op: 'delete'; cell_id?: string; cell_index?: number }
    | { op: 'move'; cell_id?: string; cell_index?: number; to_index: number }
    | { op: 'clear-outputs'; cell_id?: string; cell_index?: number; all?: boolean };

export interface EditResult {
    op: string;
    changed: boolean;
    cell_id?: string;
    cell_count: number;
}

/**
 * Apply a batch of edit operations to a notebook.
 * Structural edits (insert/delete/move/clear) are batched into one WorkspaceEdit.
 * Source edits use text edits on cell documents (preserves metadata+outputs).
 */
export async function applyEdits(
    doc: vscode.NotebookDocument,
    operations: EditOp[]
): Promise<EditResult[]> {
    const results: EditResult[] = [];

    for (const op of operations) {
        const result = await applySingle(doc, op);
        results.push(result);
    }

    // Auto-save
    if (results.some(r => r.changed) && !doc.isUntitled) {
        await doc.save();
    }

    return results;
}

async function applySingle(doc: vscode.NotebookDocument, op: EditOp): Promise<EditResult> {
    switch (op.op) {
        case 'replace-source': return replaceSource(doc, op);
        case 'insert': return insertCell(doc, op);
        case 'delete': return deleteCell(doc, op);
        case 'move': return moveCell(doc, op);
        case 'clear-outputs': return clearOutputs(doc, op);
        default: throw new Error(`Unknown op: ${(op as any).op}`);
    }
}

async function replaceSource(
    doc: vscode.NotebookDocument,
    op: { cell_id?: string; cell_index?: number; source: string }
): Promise<EditResult> {
    const idx = resolveCell(doc, { cell_id: op.cell_id, cell_index: op.cell_index });
    const cell = doc.cellAt(idx);
    const cellData = new vscode.NotebookCellData(cell.kind, op.source, cell.document.languageId);
    cellData.metadata = cell.metadata;
    cellData.outputs = [];
    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.replaceCells(new vscode.NotebookRange(idx, idx + 1), [cellData])]);
    const ok = await vscode.workspace.applyEdit(edit);
    return { op: 'replace-source', changed: ok, cell_id: getCellId(cell), cell_count: doc.cellCount };
}

async function insertCell(
    doc: vscode.NotebookDocument,
    op: { cell_type: string; source: string; at_index: number; metadata?: Record<string, any> }
): Promise<EditResult> {
    const index = op.at_index === -1 ? doc.cellCount : op.at_index;
    const kind = op.cell_type === 'code' ? vscode.NotebookCellKind.Code : vscode.NotebookCellKind.Markup;
    const lang = op.cell_type === 'code' ? 'python' : 'markdown';
    const cellId = newCellId();
    const cellData = new vscode.NotebookCellData(kind, op.source, lang);
    cellData.metadata = withCellId(cellId, op.metadata);

    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.insertCells(index, [cellData])]);
    const ok = await vscode.workspace.applyEdit(edit);
    return { op: 'insert', changed: ok, cell_id: cellId, cell_count: doc.cellCount };
}

async function deleteCell(
    doc: vscode.NotebookDocument,
    op: { cell_id?: string; cell_index?: number }
): Promise<EditResult> {
    const idx = resolveCell(doc, { cell_id: op.cell_id, cell_index: op.cell_index });
    const cellId = getCellId(doc.cellAt(idx));
    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.deleteCells(new vscode.NotebookRange(idx, idx + 1))]);
    const ok = await vscode.workspace.applyEdit(edit);
    return { op: 'delete', changed: ok, cell_id: cellId, cell_count: doc.cellCount };
}

async function moveCell(
    doc: vscode.NotebookDocument,
    op: { cell_id?: string; cell_index?: number; to_index: number }
): Promise<EditResult> {
    const from = resolveCell(doc, { cell_id: op.cell_id, cell_index: op.cell_index });
    const to = op.to_index === -1 ? doc.cellCount - 1 : op.to_index;
    if (from === to) {
        return { op: 'move', changed: false, cell_id: getCellId(doc.cellAt(from)), cell_count: doc.cellCount };
    }

    // Capture cell data
    const cell = doc.cellAt(from);
    const cellId = getCellId(cell);
    const cellData = new vscode.NotebookCellData(cell.kind, cell.document.getText(), cell.document.languageId);
    cellData.metadata = cell.metadata;
    cellData.outputs = [...cell.outputs];

    // Atomic: delete then insert in one WorkspaceEdit
    const adjustedTo = to > from ? to : to;
    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [
        vscode.NotebookEdit.deleteCells(new vscode.NotebookRange(from, from + 1)),
        vscode.NotebookEdit.insertCells(adjustedTo > from ? adjustedTo : adjustedTo, [cellData])
    ]);
    const ok = await vscode.workspace.applyEdit(edit);
    return { op: 'move', changed: ok, cell_id: cellId, cell_count: doc.cellCount };
}

async function clearOutputs(
    doc: vscode.NotebookDocument,
    op: { cell_id?: string; cell_index?: number; all?: boolean }
): Promise<EditResult> {
    const edit = new vscode.WorkspaceEdit();
    if (op.all) {
        for (let i = 0; i < doc.cellCount; i++) {
            const cell = doc.cellAt(i);
            if (cell.kind === vscode.NotebookCellKind.Code && cell.outputs.length > 0) {
                const cd = new vscode.NotebookCellData(cell.kind, cell.document.getText(), cell.document.languageId);
                cd.metadata = cell.metadata;
                cd.outputs = [];
                edit.set(doc.uri, [vscode.NotebookEdit.replaceCells(new vscode.NotebookRange(i, i + 1), [cd])]);
            }
        }
    } else {
        const idx = resolveCell(doc, { cell_id: op.cell_id, cell_index: op.cell_index });
        const cell = doc.cellAt(idx);
        const cd = new vscode.NotebookCellData(cell.kind, cell.document.getText(), cell.document.languageId);
        cd.metadata = cell.metadata;
        cd.outputs = [];
        edit.set(doc.uri, [vscode.NotebookEdit.replaceCells(new vscode.NotebookRange(idx, idx + 1), [cd])]);
    }
    const ok = await vscode.workspace.applyEdit(edit);
    return { op: 'clear-outputs', changed: ok, cell_count: doc.cellCount };
}

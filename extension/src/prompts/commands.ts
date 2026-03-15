import * as vscode from 'vscode';
import * as crypto from 'crypto';

/**
 * Insert a markdown prompt cell at the current selection with agent-repl metadata.
 * Focuses the cell for editing.
 */
export async function insertPromptCell(): Promise<void> {
    const editor = vscode.window.activeNotebookEditor;
    if (!editor) {
        vscode.window.showWarningMessage('No active notebook editor');
        return;
    }

    const doc = editor.notebook;
    const index = editor.selections.length > 0
        ? editor.selections[0].end
        : doc.cellCount;

    const cellId = crypto.randomUUID();
    const cellData = new vscode.NotebookCellData(
        vscode.NotebookCellKind.Markup,
        '',
        'markdown'
    );
    cellData.metadata = {
        custom: {
            'agent-repl': {
                cell_id: cellId,
                type: 'prompt',
                status: 'pending'
            }
        }
    };

    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.insertCells(index, [cellData])]);
    await vscode.workspace.applyEdit(edit);

    // Focus the new cell for editing
    const newRange = new vscode.NotebookRange(index, index + 1);
    editor.selections = [newRange];
    editor.revealRange(newRange);

    // Enter edit mode
    await vscode.commands.executeCommand('notebook.cell.edit');
}

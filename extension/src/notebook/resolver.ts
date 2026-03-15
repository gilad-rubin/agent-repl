import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

/**
 * Resolve a workspace-relative notebook path to an open NotebookDocument.
 * Strict matching only — no basename fallback.
 */
export function resolveNotebook(relativePath: string): vscode.NotebookDocument {
    for (const doc of vscode.workspace.notebookDocuments) {
        for (const folder of vscode.workspace.workspaceFolders ?? []) {
            const fullPath = path.resolve(folder.uri.fsPath, relativePath);
            if (realpath(fullPath) === realpath(doc.uri.fsPath)) {
                return doc;
            }
        }
    }
    const err = new Error(
        `Notebook '${relativePath}' is not open in VS Code. Open it first.`
    ) as any;
    err.statusCode = 404;
    throw err;
}

/** Find the visible NotebookEditor for a document. */
export function findEditor(doc: vscode.NotebookDocument): vscode.NotebookEditor {
    const editor = vscode.window.visibleNotebookEditors.find(
        e => e.notebook.uri.toString() === doc.uri.toString()
    );
    if (!editor) {
        const err = new Error('Notebook is not visible in any editor tab') as any;
        err.statusCode = 400;
        throw err;
    }
    return editor;
}

function realpath(p: string): string {
    try { return fs.realpathSync(p); } catch { return p; }
}

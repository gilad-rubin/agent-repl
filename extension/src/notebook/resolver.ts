import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

/**
 * Resolve a notebook path to an open NotebookDocument.
 * Tries: workspace folders, then CWD from environment, then absolute path.
 * Strict matching only — no basename fallback.
 */
export function resolveNotebook(relativePath: string): vscode.NotebookDocument {
    const candidates = new Set<string>();

    // Try resolving against each workspace folder
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
        candidates.add(realpath(path.resolve(folder.uri.fsPath, relativePath)));
    }

    // Try as absolute path (in case the CLI resolved it already)
    if (path.isAbsolute(relativePath)) {
        candidates.add(realpath(relativePath));
    }

    for (const doc of vscode.workspace.notebookDocuments) {
        const docPath = realpath(doc.uri.fsPath);
        if (candidates.has(docPath)) {
            return doc;
        }
        // Also check if the relative path matches the doc's basename path
        // (handles cross-workspace notebooks opened via cwd)
        if (doc.uri.fsPath.endsWith(path.sep + relativePath.replace(/\//g, path.sep))) {
            return doc;
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
    const editor = visibleEditor(doc);
    if (!editor) {
        const err = new Error('Notebook is not visible in any editor tab') as any;
        err.statusCode = 400;
        throw err;
    }
    return editor;
}

export async function ensureNotebookEditor(
    doc: vscode.NotebookDocument,
    options: vscode.NotebookDocumentShowOptions = { preserveFocus: true, preview: false }
): Promise<vscode.NotebookEditor> {
    const editor = visibleEditor(doc);
    if (editor) {
        if (options.selections) {
            editor.selections = [...options.selections];
        }
        return editor;
    }
    return vscode.window.showNotebookDocument(doc, options);
}

type EditorFocus =
    | {
        kind: 'text';
        document: vscode.TextDocument;
        selection: vscode.Selection;
        viewColumn?: vscode.ViewColumn;
    }
    | {
        kind: 'notebook';
        document: vscode.NotebookDocument;
        selections: readonly vscode.NotebookRange[];
        viewColumn?: vscode.ViewColumn;
    }
    | { kind: 'none' };

export function captureEditorFocus(): EditorFocus {
    const textEditor = vscode.window.activeTextEditor;
    if (textEditor) {
        return {
            kind: 'text',
            document: textEditor.document,
            selection: textEditor.selection,
            viewColumn: textEditor.viewColumn,
        };
    }

    const notebookEditor = vscode.window.activeNotebookEditor;
    if (notebookEditor) {
        return {
            kind: 'notebook',
            document: notebookEditor.notebook,
            selections: notebookEditor.selections,
            viewColumn: notebookEditor.viewColumn,
        };
    }

    return { kind: 'none' };
}

export async function restoreEditorFocus(focus: EditorFocus): Promise<void> {
    if (focus.kind === 'text') {
        await vscode.window.showTextDocument(focus.document, {
            viewColumn: focus.viewColumn,
            preserveFocus: false,
            preview: false,
            selection: focus.selection,
        });
        return;
    }

    if (focus.kind === 'notebook') {
        await vscode.window.showNotebookDocument(focus.document, {
            viewColumn: focus.viewColumn,
            preserveFocus: false,
            preview: false,
            selections: focus.selections,
        });
    }
}

function visibleEditor(doc: vscode.NotebookDocument): vscode.NotebookEditor | undefined {
    return vscode.window.visibleNotebookEditors.find(
        e => e.notebook.uri.toString() === doc.uri.toString()
    );
}

function realpath(p: string): string {
    try { return fs.realpathSync(p); } catch { return p; }
}

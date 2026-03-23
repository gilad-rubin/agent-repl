import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';

/**
 * Resolve a notebook path to an open NotebookDocument.
 * Tries: CLI cwd, workspace folders, then absolute path.
 * Strict matching only — no basename fallback.
 */
export function resolveNotebook(relativePath: string, cwd?: string): vscode.NotebookDocument {
    const doc = findOpenNotebook(relativePath, cwd);
    if (doc) { return doc; }

    const err = new Error(
        `Notebook '${relativePath}' is not open in VS Code. Open it first.`
    ) as any;
    err.statusCode = 404;
    throw err;
}

export function findOpenNotebook(relativePath: string, cwd?: string): vscode.NotebookDocument | undefined {
    const candidates = new Set(notebookPathCandidates(relativePath, cwd).map(normalizeFsPath));

    for (const doc of vscode.workspace.notebookDocuments) {
        const docPath = normalizeFsPath(doc.uri.fsPath);
        if (candidates.has(docPath)) {
            return doc;
        }
        if (!path.isAbsolute(relativePath) && doc.uri.fsPath.endsWith(path.sep + relativePath.replace(/\//g, path.sep))) {
            return doc;
        }
    }

    return undefined;
}

export async function resolveOrOpenNotebook(relativePath: string, cwd?: string): Promise<vscode.NotebookDocument> {
    const existing = findOpenNotebook(relativePath, cwd);
    if (existing) { return existing; }
    return vscode.workspace.openNotebookDocument(resolveNotebookUri(relativePath, cwd));
}

export function resolveNotebookUri(relativePath: string, cwd?: string): vscode.Uri {
    const candidates = notebookPathCandidates(relativePath, cwd);
    if (!candidates.length) {
        const err = new Error(`Cannot resolve notebook path '${relativePath}' without a workspace folder or cwd`) as any;
        err.statusCode = 400;
        throw err;
    }

    const existing = candidates.find(candidate => fs.existsSync(candidate));
    return vscode.Uri.file(existing ?? candidates[0]);
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

function notebookPathCandidates(relativePath: string, cwd?: string): string[] {
    const candidates: string[] = [];
    const push = (value: string) => {
        const normalized = normalizeFsPath(value);
        if (!candidates.some(candidate => normalizeFsPath(candidate) === normalized)) {
            candidates.push(value);
        }
    };

    if (path.isAbsolute(relativePath)) {
        push(relativePath);
        return candidates;
    }

    if (cwd) {
        push(path.resolve(cwd, relativePath));
    }

    for (const folder of vscode.workspace.workspaceFolders ?? []) {
        push(path.resolve(folder.uri.fsPath, relativePath));
    }

    return candidates;
}

function normalizeFsPath(p: string): string {
    const resolved = realpath(p);
    return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
}

function realpath(p: string): string {
    try { return fs.realpathSync(p); } catch { return p; }
}

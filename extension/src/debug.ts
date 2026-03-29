import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';

function notebookWorkspaceRoot(fsPath: string | undefined): string | undefined {
    if (!fsPath || path.extname(fsPath) !== '.ipynb') {
        return undefined;
    }
    const normalized = path.normalize(fsPath);
    const folders = vscode.workspace.workspaceFolders ?? [];
    for (const folder of folders) {
        const workspaceRoot = path.normalize(folder.uri.fsPath);
        if (normalized === workspaceRoot || normalized.startsWith(`${workspaceRoot}${path.sep}`)) {
            return workspaceRoot;
        }
    }
    return undefined;
}

export function logNotebookDiagnostic(
    fsPath: string | undefined,
    event: string,
    data: Record<string, unknown> = {},
): void {
    const workspaceRoot = notebookWorkspaceRoot(fsPath);
    if (!workspaceRoot || !fsPath) {
        return;
    }

    const normalized = path.normalize(fsPath);
    const logDir = path.join(workspaceRoot, '.agent-repl');
    const logFile = path.join(logDir, 'notebook-debug.log');
    const record = {
        at: new Date().toISOString(),
        pid: process.pid,
        event,
        path: normalized,
        relativePath: path.relative(workspaceRoot, normalized),
        data,
    };

    try {
        fs.mkdirSync(logDir, { recursive: true });
        fs.appendFileSync(logFile, `${JSON.stringify(record)}\n`, 'utf8');
    } catch (err: any) {
        console.warn('[agent-repl] failed to write notebook diagnostics:', err?.message ?? String(err));
    }
}

export const logPlaygroundDiagnostic = logNotebookDiagnostic;

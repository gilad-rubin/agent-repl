import * as vscode from 'vscode';
import { Routes } from './server';
import { resolveNotebook, findEditor } from './notebook/resolver';
import { applyEdits, EditOp } from './notebook/operations';
import { getCellId, ensureIds, resolveCell, withCellId, newCellId } from './notebook/identity';
import { toJupyter, stripForAgent } from './notebook/outputs';
import { executeCell, getExecution, getStatus, insertAndExecute } from './execution/queue';

export function buildRoutes(maxQueue: number): Routes {
    return {
        // --- Health ---
        'GET /api/health': async () => ({
            status: 'ok',
            version: '0.2.0',
            open_notebooks: vscode.workspace.notebookDocuments
                .filter(d => d.notebookType === 'jupyter-notebook')
                .map(d => d.uri.fsPath)
        }),

        // --- Read ---
        'GET /api/notebook/contents': async (_body, q) => {
            const path = q.get('path');
            if (!path) { throw new Error('Missing ?path='); }
            const doc = resolveNotebook(path);
            await ensureIds(doc);

            const cells = [];
            for (let i = 0; i < doc.cellCount; i++) {
                const cell = doc.cellAt(i);
                const outputs = toJupyter(cell);
                cells.push({
                    index: i,
                    cell_id: getCellId(cell) ?? `index-${i}`,
                    cell_type: cell.kind === vscode.NotebookCellKind.Code ? 'code' : 'markdown',
                    source: cell.document.getText(),
                    outputs: stripForAgent(outputs),
                    execution_count: cell.executionSummary?.executionOrder ?? null,
                    metadata: cell.metadata
                });
            }
            return { path, cells };
        },

        // --- Status ---
        'GET /api/notebook/status': async (_body, q) => {
            const path = q.get('path');
            if (!path) { throw new Error('Missing ?path='); }
            return getStatus(path);
        },

        // --- Edit ---
        'POST /api/notebook/edit': async (body) => {
            const { path, operations } = body as { path: string; operations: EditOp[] };
            if (!path || !operations?.length) { throw new Error('Missing path or operations'); }
            const doc = resolveNotebook(path);
            await ensureIds(doc);
            const results = await applyEdits(doc, operations);
            return { path, results };
        },

        // --- Execute ---
        'POST /api/notebook/execute-cell': async (body) => {
            const { path, cell_id, cell_index } = body as {
                path: string; cell_id?: string; cell_index?: number;
            };
            return executeCell(path, { cell_id, cell_index }, maxQueue);
        },

        'GET /api/notebook/execution': async (_body, q) => {
            const id = q.get('id');
            if (!id) { throw new Error('Missing ?id='); }
            return getExecution(id);
        },

        'POST /api/notebook/insert-and-execute': async (body) => {
            const { path, source, cell_type, at_index } = body as {
                path: string; source: string; cell_type?: string; at_index?: number;
            };
            return insertAndExecute(path, source, cell_type ?? 'code', at_index ?? -1, maxQueue);
        },

        // --- Lifecycle ---
        'POST /api/notebook/execute-all': async (body) => {
            const { path } = body as { path: string };
            const doc = resolveNotebook(path);
            await vscode.window.showNotebookDocument(doc);
            await vscode.commands.executeCommand('notebook.execute');
            // Collect results
            const cells = [];
            for (let i = 0; i < doc.cellCount; i++) {
                const cell = doc.cellAt(i);
                if (cell.kind === vscode.NotebookCellKind.Code) {
                    cells.push({
                        index: i,
                        cell_id: getCellId(cell),
                        outputs: stripForAgent(toJupyter(cell)),
                        execution_count: cell.executionSummary?.executionOrder ?? null
                    });
                }
            }
            return { status: 'ok', path, cells };
        },

        'POST /api/notebook/restart-kernel': async (body) => {
            const { path } = body as { path: string };
            const doc = resolveNotebook(path);
            await vscode.window.showNotebookDocument(doc);
            await restartKernel();
            return { status: 'ok', path };
        },

        'POST /api/notebook/restart-and-run-all': async (body) => {
            const { path } = body as { path: string };
            const doc = resolveNotebook(path);
            await vscode.window.showNotebookDocument(doc);
            await restartKernel();
            await vscode.commands.executeCommand('notebook.execute');
            const cells = [];
            for (let i = 0; i < doc.cellCount; i++) {
                const cell = doc.cellAt(i);
                if (cell.kind === vscode.NotebookCellKind.Code) {
                    cells.push({
                        index: i, cell_id: getCellId(cell),
                        outputs: stripForAgent(toJupyter(cell)),
                        execution_count: cell.executionSummary?.executionOrder ?? null
                    });
                }
            }
            return { status: 'ok', path, cells };
        },

        'POST /api/notebook/select-kernel': async (body) => {
            const { path } = body as { path: string };
            const doc = resolveNotebook(path);
            await vscode.window.showNotebookDocument(doc);
            await vscode.commands.executeCommand('notebook.selectKernel');
            return { status: 'ok', path };
        },

        'POST /api/notebook/create': async (body) => {
            const { path: relPath, kernel_name, cells } = body as {
                path: string; kernel_name?: string; cells?: Array<{ type: string; source: string }>;
            };
            const folder = vscode.workspace.workspaceFolders?.[0];
            if (!folder) { throw new Error('No workspace folder open'); }
            const uri = vscode.Uri.joinPath(folder.uri, relPath);
            const nbCells = (cells ?? []).map(c => ({
                cell_type: c.type === 'code' ? 'code' : 'markdown',
                source: c.source, metadata: {},
                ...(c.type === 'code' ? { outputs: [], execution_count: null } : {})
            }));
            const nb = {
                nbformat: 4, nbformat_minor: 5,
                metadata: { kernelspec: { display_name: kernel_name ?? 'Python 3', language: 'python', name: kernel_name ?? 'python3' } },
                cells: nbCells
            };
            await vscode.workspace.fs.writeFile(uri, Buffer.from(JSON.stringify(nb, null, 2)));
            await vscode.commands.executeCommand('vscode.openWith', uri, 'jupyter-notebook');
            return { status: 'ok', path: relPath };
        },

        'POST /api/notebook/open': async (body) => {
            const { path: relPath } = body as { path: string };
            const folder = vscode.workspace.workspaceFolders?.[0];
            if (!folder) { throw new Error('No workspace folder open'); }
            const uri = vscode.Uri.joinPath(folder.uri, relPath);
            await vscode.commands.executeCommand('vscode.openWith', uri, 'jupyter-notebook');
            return { status: 'ok', path: relPath };
        },

        // --- Prompts ---
        'POST /api/notebook/prompt': async (body) => {
            const { path, instruction, at_index } = body as {
                path: string; instruction: string; at_index?: number;
            };
            const doc = resolveNotebook(path);
            const index = (at_index === undefined || at_index === -1) ? doc.cellCount : at_index;
            const cellId = newCellId();
            const cellData = new vscode.NotebookCellData(vscode.NotebookCellKind.Markup, instruction, 'markdown');
            cellData.metadata = { custom: { 'agent-repl': { cell_id: cellId, type: 'prompt', status: 'pending' } } };
            const edit = new vscode.WorkspaceEdit();
            edit.set(doc.uri, [vscode.NotebookEdit.insertCells(index, [cellData])]);
            await vscode.workspace.applyEdit(edit);
            await doc.save();
            return { status: 'ok', cell_id: cellId, cell_index: index };
        },

        'POST /api/notebook/prompt-status': async (body) => {
            const { path, cell_id, status: promptStatus } = body as {
                path: string; cell_id: string; status: string;
            };
            const doc = resolveNotebook(path);
            const idx = resolveCell(doc, { cell_id });
            const cell = doc.cellAt(idx);
            const meta = { ...(cell.metadata ?? {}) } as Record<string, any>;
            const custom = { ...(meta.custom ?? {}) };
            const ar = { ...(custom['agent-repl'] ?? {}) };
            ar.status = promptStatus;
            custom['agent-repl'] = ar;
            meta.custom = custom;
            const edit = new vscode.WorkspaceEdit();
            edit.set(doc.uri, [vscode.NotebookEdit.updateCellMetadata(idx, meta)]);
            await vscode.workspace.applyEdit(edit);
            await doc.save();
            return { status: 'ok', cell_id, prompt_status: promptStatus };
        },

        // --- Activity ---
        'POST /api/notebook/activity': async (body) => {
            activityEvents.push(body);
            if (activityEvents.length > 500) { activityEvents.splice(0, activityEvents.length - 500); }
            for (const l of activityListeners) { l(body); }
            return { status: 'ok' };
        }
    };
}

// --- Kernel restart helper ---
// Try multiple command IDs — availability depends on VS Code version and Cursor.
async function restartKernel(): Promise<void> {
    const commands = [
        'jupyter.notebookeditor.restartkernel',
        'notebook.restartKernel',
        'jupyter.restartkernel',
    ];
    for (const cmd of commands) {
        try {
            await vscode.commands.executeCommand(cmd);
            return;
        } catch {
            // Command not available or failed — try next
        }
    }
    throw new Error('No kernel restart command succeeded');
}

// Activity event bus
const activityEvents: any[] = [];
const activityListeners: Array<(e: any) => void> = [];

export function onActivity(listener: (e: any) => void): vscode.Disposable {
    activityListeners.push(listener);
    return new vscode.Disposable(() => {
        const i = activityListeners.indexOf(listener);
        if (i >= 0) { activityListeners.splice(i, 1); }
    });
}

export function getActivityEvents(): any[] { return [...activityEvents]; }

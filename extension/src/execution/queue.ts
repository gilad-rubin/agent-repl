import * as vscode from 'vscode';
import { resolveCell, getCellId } from '../notebook/identity';
import { toJupyter, stripForAgent } from '../notebook/outputs';
import { resolveNotebook, findEditor } from '../notebook/resolver';

let executionCounter = 0;

interface QueueEntry {
    id: string;
    path: string;
    cellId: string;
    sourcePreview: string;
    queuedAt: Date;
    startedAt?: Date;
    status: 'queued' | 'running' | 'completed' | 'error';
    result?: any;
    resolve: (value: any) => void;
    reject: (err: any) => void;
}

// Per-notebook queues
const queues = new Map<string, QueueEntry[]>();
const completionCallbacks = new Map<string, () => void>();

// Kernel state tracking via notebook cell execution lifecycle
interface ExecutingCell {
    fsPath: string;
    cellIndex: number;
    cellId: string;
    sourcePreview: string;
}

// Tracks all currently executing cells across all notebooks
const executingCells = new Map<string, ExecutingCell>();
let kernelState: 'idle' | 'busy' = 'idle';

function cellKey(fsPath: string, index: number): string {
    return `${fsPath}:${index}`;
}

function updateKernelState(): void {
    const prev = kernelState;
    kernelState = executingCells.size > 0 ? 'busy' : 'idle';

    // When kernel transitions to idle, drain queued agent cells
    if (prev === 'busy' && kernelState === 'idle') {
        for (const path of queues.keys()) {
            processNext(path);
        }
    }
}

/** Initialize execution monitoring: completion detection + kernel state tracking. */
export function initExecutionMonitor(context: vscode.ExtensionContext): void {
    context.subscriptions.push(
        vscode.workspace.onDidChangeNotebookDocument(e => {
            for (const change of e.cellChanges) {
                // executionSummary is undefined if it didn't change, or the new value
                if (change.executionSummary === undefined) { continue; }

                const fsPath = e.notebook.uri.fsPath;
                const key = cellKey(fsPath, change.cell.index);
                const summary = change.executionSummary;

                if (summary?.timing?.endTime) {
                    // Execution completed
                    executingCells.delete(key);
                    updateKernelState();

                    // Fire completion callback (used by runCell)
                    const cb = completionCallbacks.get(key);
                    if (cb) {
                        completionCallbacks.delete(key);
                        cb();
                    }
                } else {
                    // Execution started (summary set/cleared without endTime)
                    executingCells.set(key, {
                        fsPath,
                        cellIndex: change.cell.index,
                        cellId: getCellId(change.cell) ?? `index-${change.cell.index}`,
                        sourcePreview: change.cell.document.getText().split('\n')[0].slice(0, 80),
                    });
                    updateKernelState();
                }
            }
        })
    );
}

/** Get current kernel state. */
export function getKernelState(): string {
    return kernelState;
}

/**
 * Execute a cell. If nothing is running, executes immediately and holds until done.
 * If something is running, queues and returns immediately with queue info.
 */
export async function executeCell(
    path: string,
    selector: { cell_id?: string; cell_index?: number },
    maxQueue: number
): Promise<any> {
    const doc = resolveNotebook(path);
    const idx = resolveCell(doc, selector);
    const cell = doc.cellAt(idx);
    const cellId = getCellId(cell) ?? `index-${idx}`;
    const preview = cell.document.getText().split('\n')[0].slice(0, 80);
    const execId = `exec-${++executionCounter}`;

    const queue = queues.get(path) ?? [];
    queues.set(path, queue);

    const running = queue.filter(e => e.status === 'running');

    if (running.length === 0 && kernelState !== 'busy') {
        return runCell(path, doc, idx, cellId, execId, preview);
    }

    // Something running — queue it
    if (queue.filter(e => e.status === 'queued').length >= maxQueue) {
        throw Object.assign(new Error(`Queue full (max ${maxQueue})`), { statusCode: 429 });
    }

    return new Promise((resolve) => {
        const entry: QueueEntry = {
            id: execId, path, cellId, sourcePreview: preview,
            queuedAt: new Date(), status: 'queued',
            resolve, reject: () => {}
        };
        queue.push(entry);

        resolve({
            status: 'queued',
            execution_id: execId,
            cell_id: cellId,
            position: queue.filter(e => e.status === 'queued').length,
            kernel_state: kernelState,
            currently_running: running.map(e => ({
                cell_id: e.cellId,
                source_preview: e.sourcePreview,
                owner: 'agent'
            })),
            message: kernelState === 'busy'
                ? `Kernel is busy (human or agent execution in progress). Queued.`
                : `Queued after ${running.length} running cell(s)`
        });
    });
}

/** Get an execution result by ID (for polling). */
export function getExecution(executionId: string): any {
    for (const queue of queues.values()) {
        const entry = queue.find(e => e.id === executionId);
        if (entry) {
            if (entry.status === 'completed' || entry.status === 'error') {
                return entry.result;
            }
            return { execution_id: entry.id, status: entry.status, cell_id: entry.cellId };
        }
    }
    throw Object.assign(new Error(`Unknown execution: ${executionId}`), { statusCode: 404 });
}

/** Get execution status for a notebook. */
export function getStatus(path: string): any {
    const doc = resolveNotebook(path);
    const fsPath = doc.uri.fsPath;
    const queue = queues.get(path) ?? [];

    // Collect agent-running cell IDs for ownership tagging
    const agentRunningIds = new Set(
        queue.filter(e => e.status === 'running').map(e => e.cellId)
    );

    // Build running list from executingCells (covers both human and agent)
    const running: Array<{
        cell_id: string;
        cell_index: number;
        source_preview: string;
        owner: 'human' | 'agent';
    }> = [];

    for (const info of executingCells.values()) {
        if (info.fsPath !== fsPath) { continue; }
        running.push({
            cell_id: info.cellId,
            cell_index: info.cellIndex,
            source_preview: info.sourcePreview,
            owner: agentRunningIds.has(info.cellId) ? 'agent' : 'human',
        });
    }

    const queued = queue
        .filter(e => e.status === 'queued')
        .map((e, i) => ({
            execution_id: e.id,
            cell_id: e.cellId,
            source_preview: e.sourcePreview,
            position: i + 1,
        }));

    return {
        path,
        kernel_state: kernelState,
        busy: kernelState === 'busy',
        running,
        queued
    };
}

/** Insert a cell and execute it. */
export async function insertAndExecute(
    path: string,
    source: string,
    cellType: string,
    atIndex: number,
    maxQueue: number
): Promise<any> {
    const doc = resolveNotebook(path);
    const index = atIndex === -1 ? doc.cellCount : atIndex;

    const kind = cellType === 'code' ? vscode.NotebookCellKind.Code : vscode.NotebookCellKind.Markup;
    const lang = cellType === 'code' ? 'python' : 'markdown';
    const cellId = require('crypto').randomUUID() as string;
    const cellData = new vscode.NotebookCellData(kind, source, lang);
    const { withCellId } = require('../notebook/identity');
    cellData.metadata = withCellId(cellId);

    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.insertCells(index, [cellData])]);
    await vscode.workspace.applyEdit(edit);

    const result = await executeCell(path, { cell_index: index }, maxQueue);
    return { ...result, operation: 'insert-execute', cell_id: cellId, cell_index: index };
}

// --- Internal ---

/** Run a cell using a new queue entry (called from executeCell for immediate execution). */
async function runCell(
    path: string,
    doc: vscode.NotebookDocument,
    cellIndex: number,
    cellId: string,
    execId: string,
    preview: string
): Promise<any> {
    const queue = queues.get(path) ?? [];
    queues.set(path, queue);

    const entry: QueueEntry = {
        id: execId, path, cellId, sourcePreview: preview,
        queuedAt: new Date(), startedAt: new Date(), status: 'running',
        resolve: () => {}, reject: () => {}
    };
    queue.push(entry);

    return runCellEntry(path, doc, cellIndex, entry);
}

/** Run a cell using an existing queue entry (called from processNext for queued cells). */
async function runCellEntry(
    path: string,
    doc: vscode.NotebookDocument,
    cellIndex: number,
    entry: QueueEntry
): Promise<any> {
    try {
        // Ensure notebook is visible before executing (findEditor throws 400 otherwise)
        await vscode.window.showNotebookDocument(doc);
        const editor = findEditor(doc);
        editor.selections = [new vscode.NotebookRange(cellIndex, cellIndex + 1)];

        const completionPromise = waitForCompletion(doc.uri.fsPath, cellIndex);

        await vscode.commands.executeCommand('notebook.cell.execute', {
            ranges: [{ start: cellIndex, end: cellIndex + 1 }],
            document: doc.uri
        });

        await completionPromise;

        const cell = doc.cellAt(cellIndex);
        const outputs = toJupyter(cell);
        const stripped = stripForAgent(outputs);
        const execCount = cell.executionSummary?.executionOrder ?? null;

        const result = {
            status: 'ok',
            execution_id: entry.id,
            cell_id: entry.cellId,
            cell_index: cellIndex,
            outputs: stripped,
            execution_count: execCount
        };

        entry.status = 'completed';
        entry.result = result;
        processNext(path);
        return result;
    } catch (err: any) {
        entry.status = 'error';
        entry.result = { status: 'error', execution_id: entry.id, error: err.message };
        processNext(path);
        throw err;
    }
}

function waitForCompletion(fsPath: string, cellIndex: number): Promise<void> {
    return new Promise((resolve) => {
        completionCallbacks.set(`${fsPath}:${cellIndex}`, resolve);
    });
}

function processNext(path: string): void {
    const queue = queues.get(path);
    if (!queue) { return; }

    const now = Date.now();
    const active = queue.filter(e =>
        e.status === 'queued' || e.status === 'running' ||
        (e.result && now - e.queuedAt.getTime() < 60_000)
    );
    queues.set(path, active);

    const next = active.find(e => e.status === 'queued');
    if (!next) { return; }

    // Mark as running BEFORE calling runCell to prevent re-entry loops
    next.status = 'running';
    next.startedAt = new Date();

    const doc = resolveNotebook(path);
    try {
        const idx = resolveCell(doc, { cell_id: next.cellId });
        runCellEntry(path, doc, idx, next)
            .then(() => {})
            .catch(() => {});
    } catch {
        next.status = 'error';
        next.result = { status: 'error', execution_id: next.id, error: 'Cell no longer exists' };
    }
}

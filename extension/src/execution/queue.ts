import * as vscode from 'vscode';
import { resolveCell, getCellId } from '../notebook/identity';
import { AGENT_REPL_OUTPUT_METADATA_KEY, JupyterOutput, toJupyter, stripForAgent, toVSCode } from '../notebook/outputs';
import { resolveNotebook } from '../notebook/resolver';
import { logNotebookDiagnostic } from '../debug';
import { discoverDaemon, daemonPost, workspaceRootForPath, sessionIdForWorkspaceState } from '../session';

function queueDebug(path: string, event: string, data: Record<string, unknown> = {}): void {
    const queue = queues.get(path) ?? [];
    const snapshot = {
        queueEntries: queue.map(e => ({ id: e.id, cellId: e.cellId, status: e.status, preview: e.sourcePreview })),
        ...data,
    };
    logNotebookDiagnostic(path, `queue:${event}`, snapshot);
}

let executionCounter = 0;

interface QueueEntry {
    id: string;
    path: string;
    cellId: string;
    sourcePreview: string;
    batchId?: string;
    stopBatchOnError?: boolean;
    queuedAt: Date;
    startedAt?: Date;
    status: 'queued' | 'running' | 'completed' | 'error';
    result?: any;
    resolve: (value: any) => void;
    reject: (err: any) => void;
}

interface PreparedExecutionContext {
    doc: vscode.NotebookDocument;
    cellIndex: number;
    cellId: string;
    preview: string;
    execId: string;
    queue: QueueEntry[];
    busy: boolean;
}

// Per-notebook queues
const queues = new Map<string, QueueEntry[]>();

/** Reset execution tracking state. */
export function resetExecutionState(
    fsPath?: string,
    reason: string = 'Execution canceled due to kernel restart'
): void {
    queueDebug(fsPath ?? 'global', 'resetExecutionState', { reason });

    for (const [path, queue] of queues.entries()) {
        if (fsPath && path !== fsPath) { continue; }
        for (const entry of queue) {
            if (entry.status !== 'queued' && entry.status !== 'running') { continue; }
            entry.status = 'error';
            entry.result = {
                status: 'error',
                execution_id: entry.id,
                cell_id: entry.cellId,
                error: reason,
            };
        }
    }

    const targetPaths = fsPath ? [fsPath] : [...queues.keys()];
    for (const path of targetPaths) {
        processNext(path);
    }
}

function isNotebookBusy(path: string): boolean {
    const queue = queues.get(path) ?? [];
    return queue.some(e => e.status === 'running');
}

/**
 * Execute a cell via daemon HTTP. If the daemon reports a queue,
 * the cell is queued locally for status tracking.
 */
export async function executeCell(
    path: string,
    selector: { cell_id?: string; cell_index?: number },
    maxQueue: number
): Promise<any> {
    const execution = prepareExecutionContext(path, selector);

    queueDebug(path, 'executeCell:entry', {
        execId: execution.execId,
        cellId: execution.cellId,
        cellIndex: execution.cellIndex,
    });

    if (queuedEntryCount(execution.queue) >= maxQueue) {
        throw Object.assign(new Error(`Queue full (max ${maxQueue})`), { statusCode: 429 });
    }

    return runCellViaDaemon(path, execution);
}

/** Start execution without waiting for completion. */
export async function startExecution(
    path: string,
    selector: { cell_id?: string; cell_index?: number },
    maxQueue: number,
    options?: {
        batchId?: string;
        stopBatchOnError?: boolean;
        forceQueue?: boolean;
    },
): Promise<any> {
    return enqueueExecution(path, selector, maxQueue, options);
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
export async function getStatus(path: string): Promise<any> {
    const queue = queues.get(path) ?? [];
    return {
        path,
        kernel_state: isNotebookBusy(path) ? 'busy' : 'idle',
        busy: isNotebookBusy(path),
        running: queue.filter(e => e.status === 'running').map(e => ({
            cell_id: e.cellId,
            cell_index: -1,
            source_preview: e.sourcePreview,
            owner: 'agent' as const,
        })),
        queued: buildQueuedStatus(queue),
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

    const result = await enqueueExecution(path, { cell_index: index }, maxQueue);
    return { ...result, operation: 'insert-execute', cell_id: cellId, cell_index: index };
}

export async function startNotebookExecutionAll(path: string, maxQueue: number): Promise<any[]> {
    const doc = resolveNotebook(path);
    const executions: any[] = [];
    const batchId = `run-all-${++executionCounter}`;

    for (let index = 0; index < doc.cellCount; index++) {
        const cell = doc.cellAt(index);
        if (cell.kind !== vscode.NotebookCellKind.Code) {
            continue;
        }
        const forceQueue = (queues.get(path) ?? []).some((entry) =>
            entry.batchId === batchId && (entry.status === 'queued' || entry.status === 'running'),
        );
        executions.push(
            await enqueueExecution(path, { cell_index: index }, maxQueue, {
                batchId,
                stopBatchOnError: true,
                forceQueue,
            }),
        );
    }

    return executions;
}

// --- Internal ---

async function runCellViaDaemon(
    path: string,
    execution: PreparedExecutionContext,
): Promise<any> {
    const entry = createQueueEntry(execution, { status: 'running', startedAt: new Date() });
    execution.queue.push(entry);

    try {
        const daemonResult = await postExecuteCell(execution.doc, execution.cellId, execution.cellIndex);
        const status = daemonResult?.status ?? 'ok';
        entry.status = status === 'error' ? 'error' : 'completed';
        entry.result = {
            ...daemonResult,
            execution_id: entry.id,
            cell_id: entry.cellId,
            cell_index: execution.cellIndex,
            execution_mode: 'daemon',
        };
        processNext(path);
        return entry.result;
    } catch (err: any) {
        entry.status = 'error';
        entry.result = { status: 'error', execution_id: entry.id, cell_id: entry.cellId, error: err.message };
        processNext(path);
        throw err;
    }
}

async function enqueueExecution(
    path: string,
    selector: { cell_id?: string; cell_index?: number },
    maxQueue: number,
    options?: {
        batchId?: string;
        stopBatchOnError?: boolean;
        forceQueue?: boolean;
    },
): Promise<any> {
    const execution = prepareExecutionContext(path, selector);

    queueDebug(path, 'enqueue:entry', {
        execId: execution.execId,
        cellId: execution.cellId,
        cellIndex: execution.cellIndex,
        busy: execution.busy,
        forceQueue: options?.forceQueue ?? false,
        batchId: options?.batchId,
    });

    if (!options?.forceQueue && !execution.busy) {
        queueDebug(path, 'enqueue:immediate-start', { execId: execution.execId, cellId: execution.cellId });
        const entry = createQueueEntry(execution, {
            batchId: options?.batchId,
            stopBatchOnError: options?.stopBatchOnError,
            status: 'running',
            startedAt: new Date(),
        });
        execution.queue.push(entry);

        // Fire and forget — daemon handles execution.
        runDaemonEntry(path, execution.doc, entry, execution.cellIndex)
            .then(() => {})
            .catch(() => {});

        return {
            status: 'started',
            execution_id: execution.execId,
            cell_id: execution.cellId,
            cell_index: execution.cellIndex,
            kernel_state: isNotebookBusy(path) ? 'busy' : 'idle',
        };
    }

    if (queuedEntryCount(execution.queue) >= maxQueue) {
        throw Object.assign(new Error(`Queue full (max ${maxQueue})`), { statusCode: 429 });
    }

    queueDebug(path, 'enqueue:queued', { execId: execution.execId, cellId: execution.cellId });

    execution.queue.push(createQueueEntry(execution, {
        batchId: options?.batchId,
        stopBatchOnError: options?.stopBatchOnError,
    }));

    return buildQueuedExecutionResult(path, execution);
}

function prepareExecutionContext(
    path: string,
    selector: { cell_id?: string; cell_index?: number },
): PreparedExecutionContext {
    const doc = resolveNotebook(path);
    const cellIndex = resolveCell(doc, selector);
    const cell = doc.cellAt(cellIndex);
    const cellId = getCellId(cell);
    if (!cellId) {
        throw new Error(`Cell at index ${cellIndex} is missing an agent-repl cell ID`);
    }
    const preview = cell.document.getText().split('\n')[0].slice(0, 80);
    const execId = `exec-${++executionCounter}`;
    const queue = queues.get(path) ?? [];
    queues.set(path, queue);
    const busy = isNotebookBusy(path);
    return { doc, cellIndex, cellId, preview, execId, queue, busy };
}

function createQueueEntry(
    execution: PreparedExecutionContext,
    overrides?: Partial<QueueEntry>,
): QueueEntry {
    return {
        id: execution.execId,
        path: execution.doc.uri.fsPath,
        cellId: execution.cellId,
        sourcePreview: execution.preview,
        queuedAt: new Date(),
        status: 'queued',
        resolve: () => {},
        reject: () => {},
        ...overrides,
    };
}

function queuedEntryCount(queue: QueueEntry[]): number {
    return queue.filter((entry) => entry.status === 'queued').length;
}

function buildQueuedStatus(queue: QueueEntry[]): Array<{
    execution_id: string;
    cell_id: string;
    source_preview: string;
    position: number;
}> {
    return queue
        .filter((entry) => entry.status === 'queued')
        .map((entry, index) => ({
            execution_id: entry.id,
            cell_id: entry.cellId,
            source_preview: entry.sourcePreview,
            position: index + 1,
        }));
}

function buildQueuedExecutionResult(path: string, execution: PreparedExecutionContext): any {
    return {
        status: 'queued',
        execution_id: execution.execId,
        cell_id: execution.cellId,
        cell_index: execution.cellIndex,
        position: queuedEntryCount(execution.queue),
        kernel_state: isNotebookBusy(path) ? 'busy' : 'idle',
        message: `Queued after running cell(s)`,
    };
}

// -- Daemon HTTP execution --------------------------------------------------

async function postExecuteCell(
    doc: vscode.NotebookDocument,
    cellId: string,
    cellIndex: number,
): Promise<any> {
    const fsPath = doc.uri.fsPath;
    const workspaceRoot = workspaceRootForPath(fsPath);
    if (!workspaceRoot) {
        throw new Error(`No workspace root found for ${fsPath}`);
    }
    const daemon = discoverDaemon(workspaceRoot);
    if (!daemon) {
        throw new Error('Daemon not found');
    }
    const notebookPath = require('path').relative(workspaceRoot, fsPath);
    return daemonPost(daemon, '/api/notebooks/execute-cell', {
        path: notebookPath,
        cell_id: cellId,
        cell_index: cellIndex,
        wait: true,
    });
}

async function runDaemonEntry(
    path: string,
    doc: vscode.NotebookDocument,
    entry: QueueEntry,
    cellIndex: number,
): Promise<void> {
    try {
        const daemonResult = await postExecuteCell(doc, entry.cellId, cellIndex);
        const status = daemonResult?.status ?? 'ok';
        entry.status = status === 'error' ? 'error' : 'completed';
        entry.result = {
            ...daemonResult,
            execution_id: entry.id,
            cell_id: entry.cellId,
            execution_mode: 'daemon',
        };
    } catch (err: any) {
        entry.status = 'error';
        entry.result = { status: 'error', execution_id: entry.id, cell_id: entry.cellId, error: err.message };
    }
    processNext(path);
}

function processNext(path: string): void {
    const queue = queues.get(path);
    if (!queue) {
        queueDebug(path, 'processNext:no-queue');
        return;
    }

    const now = Date.now();
    const active = queue.filter(e =>
        e.status === 'queued' || e.status === 'running' ||
        (e.result && now - e.queuedAt.getTime() < 60_000)
    );
    queues.set(path, active);

    pauseQueuedBatchEntries(active);

    const next = active.find(e => e.status === 'queued');

    queueDebug(path, 'processNext', {
        activeCount: active.length,
        nextExecId: next?.id ?? null,
        nextCellId: next?.cellId ?? null,
    });

    if (!next) { return; }

    next.status = 'running';
    next.startedAt = new Date();

    const doc = resolveNotebook(path);
    try {
        const idx = resolveCell(doc, { cell_id: next.cellId });
        runDaemonEntry(path, doc, next, idx)
            .then(() => {})
            .catch(() => {});
    } catch {
        next.status = 'error';
        next.result = { status: 'error', execution_id: next.id, error: 'Cell no longer exists' };
        queueDebug(path, 'processNext:cell-gone', { execId: next.id, cellId: next.cellId });
    }
}

function pauseQueuedBatchEntries(entries: QueueEntry[]): void {
    const haltedBatches = new Map<string, QueueEntry>();
    for (const entry of entries) {
        if (entry.status !== 'error' || !entry.stopBatchOnError || !entry.batchId) {
            continue;
        }
        haltedBatches.set(entry.batchId, entry);
    }

    for (const entry of entries) {
        if (entry.status !== 'queued' || !entry.batchId) {
            continue;
        }
        const haltedBy = haltedBatches.get(entry.batchId);
        if (!haltedBy) {
            continue;
        }
        entry.status = 'completed';
        entry.result = {
            status: 'paused',
            execution_id: entry.id,
            cell_id: entry.cellId,
            error: `Paused after ${haltedBy.cellId} failed`,
            stopped_by_execution_id: haltedBy.id,
            stopped_by_cell_id: haltedBy.cellId,
        };
    }
}

// -- Output helpers (kept for shared usage) ---------------------------------

export function iopubMessageToJupyterOutput(msg: any): JupyterOutput | undefined {
    const type = msg?.header?.msg_type;
    const content = msg?.content ?? {};
    if (!type) { return undefined; }

    if (type === 'stream') {
        return { output_type: 'stream', name: content.name, text: content.text ?? '' };
    }
    if (type === 'execute_result') {
        return {
            output_type: 'execute_result',
            data: content.data ?? {},
            metadata: content.metadata ?? {},
            transient: content.transient ?? {},
            execution_count: content.execution_count,
        };
    }
    if (type === 'display_data') {
        return {
            output_type: 'display_data',
            data: content.data ?? {},
            metadata: content.metadata ?? {},
            transient: content.transient ?? {},
        };
    }
    if (type === 'update_display_data') {
        return {
            output_type: 'update_display_data',
            data: content.data ?? {},
            metadata: content.metadata ?? {},
            transient: content.transient ?? {},
        };
    }
    if (type === 'clear_output') {
        return { output_type: 'clear_output', wait: content.wait === true };
    }
    if (type === 'error') {
        return {
            output_type: 'error',
            ename: content.ename,
            evalue: content.evalue,
            traceback: Array.isArray(content.traceback) ? content.traceback : [],
        };
    }
    return undefined;
}

export function applyNotebookOutput(
    outputs: vscode.NotebookCellOutput[],
    next: vscode.NotebookCellOutput
): vscode.NotebookCellOutput[] {
    const displayId = getNotebookDisplayId(next);
    if (!displayId) {
        return [...outputs, next];
    }
    const index = outputs.findIndex(output => getNotebookDisplayId(output) === displayId);
    if (index === -1) {
        return [...outputs, next];
    }
    const updated = outputs.slice();
    updated[index] = next;
    return updated;
}

export function applyJupyterOutput(outputs: JupyterOutput[], next: JupyterOutput): JupyterOutput[] {
    if (next.output_type !== 'update_display_data') {
        return [...outputs, next];
    }

    const displayId = getJupyterDisplayId(next);
    const normalized: JupyterOutput = { ...next, output_type: 'display_data' };
    if (!displayId) {
        return [...outputs, normalized];
    }

    const index = outputs.findIndex(output => getJupyterDisplayId(output) === displayId);
    if (index === -1) {
        return [...outputs, normalized];
    }

    const existing = outputs[index];
    const updated = outputs.slice();
    updated[index] = {
        ...existing,
        ...normalized,
        output_type: existing.output_type === 'execute_result' ? 'execute_result' : normalized.output_type,
        data: normalized.data ?? existing.data,
        metadata: normalized.metadata ?? existing.metadata,
        transient: { ...(existing.transient ?? {}), ...(normalized.transient ?? {}) },
    };
    return updated;
}

function getNotebookDisplayId(output: vscode.NotebookCellOutput | undefined): string | undefined {
    const transient = output?.metadata?.transient;
    if (transient && typeof transient === 'object' && typeof transient.display_id === 'string') {
        return transient.display_id;
    }
    const internal = output?.metadata?.[AGENT_REPL_OUTPUT_METADATA_KEY];
    if (internal && typeof internal === 'object' && typeof internal.display_id === 'string') {
        return internal.display_id;
    }
    return undefined;
}

function getJupyterDisplayId(output: JupyterOutput | undefined): string | undefined {
    return typeof output?.transient?.display_id === 'string'
        ? output.transient.display_id
        : undefined;
}

// -- Jupyter API (kept for kernel selection in routes.ts) -------------------

let cachedJupyterApi: any | undefined;

export async function getJupyterApi(): Promise<any | undefined> {
    if (cachedJupyterApi) { return cachedJupyterApi; }
    const jupyterExt = vscode.extensions.getExtension('ms-toolsai.jupyter');
    if (!jupyterExt) { return undefined; }
    cachedJupyterApi = jupyterExt.isActive ? jupyterExt.exports : await jupyterExt.activate();
    return cachedJupyterApi;
}

export function resetJupyterApiCache(): void {
    // Only the extension object cache; execution is now daemon-routed.
    cachedJupyterApi = undefined;
}

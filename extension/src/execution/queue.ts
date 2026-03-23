import * as vscode from 'vscode';
import { resolveCell, getCellId } from '../notebook/identity';
import { AGENT_REPL_OUTPUT_METADATA_KEY, JupyterOutput, toJupyter, stripForAgent, toVSCode } from '../notebook/outputs';
import { resolveNotebook, ensureNotebookEditor, captureEditorFocus, restoreEditorFocus } from '../notebook/resolver';

let executionCounter = 0;
type ExecutionMode = 'no-yank' | 'native';

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
type CompletionReason = 'completed' | 'canceled' | 'timeout';
const completionCallbacks = new Map<string, (reason: CompletionReason) => void>();

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

function matchesCellKeyPath(key: string, fsPath?: string): boolean {
    return !fsPath || key.startsWith(`${fsPath}:`);
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

export function executionSummaryIndicatesCompletion(
    summary: vscode.NotebookCellExecutionSummary | null | undefined,
    initialExecutionOrder?: number | null
): boolean {
    if (!summary) { return false; }
    if (typeof summary.success === 'boolean') { return true; }
    if (typeof summary.timing?.endTime === 'number') { return true; }
    return initialExecutionOrder !== undefined &&
        typeof summary.executionOrder === 'number' &&
        summary.executionOrder !== initialExecutionOrder;
}

/** Initialize execution monitoring: completion detection + kernel state tracking. */
export function initExecutionMonitor(): vscode.Disposable {
    return vscode.workspace.onDidChangeNotebookDocument(e => {
        for (const change of e.cellChanges) {
            // executionSummary is undefined if it didn't change, or the new value
            if (change.executionSummary === undefined) { continue; }

            const fsPath = e.notebook.uri.fsPath;
            const key = cellKey(fsPath, change.cell.index);
            const summary = change.executionSummary;

            if (executionSummaryIndicatesCompletion(summary)) {
                // Execution completed
                executingCells.delete(key);
                updateKernelState();

                // Fire completion callback (used by runCell)
                const cb = completionCallbacks.get(key);
                if (cb) {
                    completionCallbacks.delete(key);
                    cb('completed');
                }
            } else if (summary) {
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
    });
}

/** Get current kernel state. */
export function getKernelState(): string {
    return kernelState;
}

/** Reset execution tracking state. Call on kernel restart to avoid orphaned "busy" state. */
export function resetExecutionState(
    fsPath?: string,
    reason: string = 'Execution canceled due to kernel restart'
): void {
    for (const key of [...executingCells.keys()]) {
        if (matchesCellKeyPath(key, fsPath)) {
            executingCells.delete(key);
        }
    }

    for (const [key, callback] of [...completionCallbacks.entries()]) {
        if (!matchesCellKeyPath(key, fsPath)) { continue; }
        completionCallbacks.delete(key);
        callback('canceled');
    }

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

    updateKernelState();

    const targetPaths = fsPath ? [fsPath] : [...queues.keys()];
    for (const path of targetPaths) {
        processNext(path);
    }
}

function isNotebookBusy(path: string): boolean {
    const queue = queues.get(path) ?? [];
    return kernelState === 'busy' || queue.some(e => e.status === 'running');
}

/**
 * Check the real kernel status via Jupyter APIs.
 * If our tracking says busy but the kernel is actually idle/dead,
 * clear the stale state so execution can proceed.
 * Also expires queue entries that have been running for too long
 * (e.g. completion tracking failed on code-server).
 */
async function reconcileKernelState(path: string): Promise<void> {
    const queue = queues.get(path);
    const hasRunningQueueEntry = queue?.some(entry => entry.status === 'running') ?? false;

    if (queue?.some(entry =>
        entry.status === 'running' &&
        entry.startedAt &&
        Date.now() - entry.startedAt.getTime() > 300_000
    )) {
        resetExecutionState(path, 'Execution tracking expired');
        return;
    }

    if (kernelState !== 'busy' && !hasRunningQueueEntry) { return; }

    try {
        const doc = resolveNotebook(path);
        const realStatus = await queryKernelStatus(doc);

        // If kernel is idle, dead, restarting, or unreachable — our "busy" is stale
        if (realStatus !== 'busy' && realStatus !== 'starting') {
            resetExecutionState(path, 'Execution tracking reset after kernel became idle');
        }
    } catch {
        // Can't resolve notebook or check kernel — leave state as-is
    }
}

async function queryKernelStatus(doc: vscode.NotebookDocument): Promise<string> {
    const privateKernel = await getPrivateJupyterKernel(doc);
    if (privateKernel?.status) { return privateKernel.status; }

    const kernel = await getJupyterKernel(doc);
    if (kernel?.status) { return kernel.status; }

    // Can't reach kernel at all — likely dead or restarted
    return 'unknown';
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
    await reconcileKernelState(path);

    const doc = resolveNotebook(path);
    const idx = resolveCell(doc, selector);
    const cell = doc.cellAt(idx);
    const cellId = getCellId(cell) ?? `index-${idx}`;
    const preview = cell.document.getText().split('\n')[0].slice(0, 80);
    const execId = `exec-${++executionCounter}`;

    const queue = queues.get(path) ?? [];
    queues.set(path, queue);

    const running = queue.filter(e => e.status === 'running');

    if (running.length === 0 && !isNotebookBusy(path)) {
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
            kernel_state: isNotebookBusy(path) ? 'busy' : 'idle',
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
export async function getStatus(path: string): Promise<any> {
    await reconcileKernelState(path);
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

    for (const entry of queue.filter(e => e.status === 'running')) {
        if (running.some(r => r.cell_id === entry.cellId)) { continue; }
        try {
            const cellIndex = resolveCell(doc, { cell_id: entry.cellId });
            running.push({
                cell_id: entry.cellId,
                cell_index: cellIndex,
                source_preview: entry.sourcePreview,
                owner: 'agent',
            });
        } catch {
            // Ignore cells that disappeared while queued/running.
        }
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
        kernel_state: isNotebookBusy(path) ? 'busy' : kernelState,
        busy: isNotebookBusy(path),
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

    const result = await enqueueExecution(path, { cell_index: index }, maxQueue);
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

async function enqueueExecution(
    path: string,
    selector: { cell_id?: string; cell_index?: number },
    maxQueue: number
): Promise<any> {
    await reconcileKernelState(path);

    const doc = resolveNotebook(path);
    const idx = resolveCell(doc, selector);
    const cell = doc.cellAt(idx);
    const cellId = getCellId(cell) ?? `index-${idx}`;
    const preview = cell.document.getText().split('\n')[0].slice(0, 80);
    const execId = `exec-${++executionCounter}`;

    const queue = queues.get(path) ?? [];
    queues.set(path, queue);

    const running = queue.filter(e => e.status === 'running');

    if (running.length === 0 && !isNotebookBusy(path)) {
        const entry: QueueEntry = {
            id: execId,
            path,
            cellId,
            sourcePreview: preview,
            queuedAt: new Date(),
            startedAt: new Date(),
            status: 'running',
            resolve: () => {},
            reject: () => {},
        };
        queue.push(entry);
        runCellEntry(path, doc, idx, entry)
            .then(() => {})
            .catch(() => {});

        return {
            status: 'started',
            execution_id: execId,
            cell_id: cellId,
            cell_index: idx,
            kernel_state: isNotebookBusy(path) ? 'busy' : 'idle',
        };
    }

    if (queue.filter(e => e.status === 'queued').length >= maxQueue) {
        throw Object.assign(new Error(`Queue full (max ${maxQueue})`), { statusCode: 429 });
    }

    const entry: QueueEntry = {
        id: execId,
        path,
        cellId,
        sourcePreview: preview,
        queuedAt: new Date(),
        status: 'queued',
        resolve: () => {},
        reject: () => {},
    };
    queue.push(entry);

    return {
        status: 'queued',
        execution_id: execId,
        cell_id: cellId,
        cell_index: idx,
        position: queue.filter(e => e.status === 'queued').length,
        kernel_state: isNotebookBusy(path) ? 'busy' : 'idle',
        currently_running: running.map(e => ({
            cell_id: e.cellId,
            source_preview: e.sourcePreview,
            owner: 'agent',
        })),
        message: kernelState === 'busy'
            ? 'Kernel is busy (human or agent execution in progress). Queued.'
            : `Queued after ${running.length} running cell(s)`,
    };
}

/** Run a cell using an existing queue entry (called from processNext for queued cells). */
async function runCellEntry(
    path: string,
    doc: vscode.NotebookDocument,
    cellIndex: number,
    entry: QueueEntry
): Promise<any> {
    try {
        const executionPreference = getExecutionMode();
        if (executionPreference === 'native') {
            return await runCellViaNotebookCommand(path, doc, cellIndex, entry, {
                executionPreference,
            });
        }

        const directAttempt = await runCellViaJupyterKernelApi(doc, cellIndex, entry);
        if (directAttempt.result) {
            directAttempt.result.execution_preference = executionPreference;
            entry.status = 'completed';
            entry.result = directAttempt.result;
            processNext(path);
            return directAttempt.result;
        }

        return await runCellViaNotebookCommand(path, doc, cellIndex, entry, {
            executionPreference,
            fallbackReason: directAttempt.fallbackReason,
        });
    } catch (err: any) {
        entry.status = 'error';
        entry.result = { status: 'error', execution_id: entry.id, error: err.message };
        processNext(path);
        throw err;
    }
}

async function runCellViaNotebookCommand(
    path: string,
    doc: vscode.NotebookDocument,
    cellIndex: number,
    entry: QueueEntry,
    options: {
        executionPreference: ExecutionMode;
        fallbackReason?: string;
    }
): Promise<any> {
    const selection = [new vscode.NotebookRange(cellIndex, cellIndex + 1)];
    const completionPromise = waitForCompletion(doc, cellIndex);
    let restoreFocusWarning: string | undefined;

    if (options.executionPreference === 'native') {
        await vscode.window.showNotebookDocument(doc, {
            preserveFocus: false,
            preview: false,
            selections: selection,
        });
        await vscode.commands.executeCommand('notebook.cell.execute', {
            ranges: [{ start: cellIndex, end: cellIndex + 1 }],
            document: doc.uri
        });
    } else {
        const focus = captureEditorFocus();
        try {
            await ensureNotebookEditor(doc, {
                preserveFocus: true,
                preview: false,
                selections: selection,
            });
            await vscode.commands.executeCommand('notebook.cell.execute', {
                ranges: [{ start: cellIndex, end: cellIndex + 1 }],
                document: doc.uri
            });
        } finally {
            try {
                await restoreEditorFocus(focus);
            } catch (err: any) {
                // Focus restoration is best-effort and should never mask a successful execution.
                restoreFocusWarning = err?.message ?? String(err);
                console.warn('agent-repl: failed to restore editor focus', err);
            }
        }
    }

    const completion = await completionPromise;
    if (completion !== 'completed') {
        throw new Error(
            completion === 'timeout'
                ? 'Execution tracking timed out'
                : 'Execution canceled due to kernel restart'
        );
    }

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
        execution_count: execCount,
        execution_mode: 'notebook-command',
        execution_preference: options.executionPreference,
        execution_fallback_reason: options.fallbackReason ?? null,
        ...(restoreFocusWarning ? { focus_restore_warning: restoreFocusWarning } : {}),
    };

    entry.status = 'completed';
    entry.result = result;
    processNext(path);
    return result;
}

async function runCellViaJupyterKernelApi(
    doc: vscode.NotebookDocument,
    cellIndex: number,
    entry: QueueEntry
): Promise<{ result?: any; fallbackReason?: string }> {
    if (kernelApiDisabled) {
        return { fallbackReason: 'kernel-api-disabled' };
    }

    const privateResult = await runCellViaPrivateJupyterSession(doc, cellIndex, entry);
    if (privateResult.result || privateResult.fallbackReason !== 'no-private-jupyter-session') {
        return privateResult;
    }

    const kernel = await getJupyterKernel(doc);
    if (!kernel || typeof kernel.executeCode !== 'function') {
        return { fallbackReason: 'no-private-jupyter-session; no-jupyter-kernel-api' };
    }

    let outputs: vscode.NotebookCellOutput[] = [];
    let started = false;
    const startTime = Date.now();

    try {
        await replaceCellState(doc, cellIndex, outputs, 'running', {
            success: undefined,
            timing: { startTime, endTime: startTime },
        });
        const source = doc.cellAt(cellIndex).document.getText();

        for await (const output of kernel.executeCode(source)) {
            started = true;
            if (!isNotebookOutput(output)) { continue; }
            outputs = applyNotebookOutput(outputs, output);
            await replaceCellState(doc, cellIndex, outputs, 'running', {
                success: undefined,
                timing: { startTime, endTime: Date.now() },
            });
        }

        await replaceCellState(doc, cellIndex, outputs, undefined, {
            success: true,
            timing: { startTime, endTime: Date.now() },
        });
        const cell = doc.cellAt(cellIndex);
        return {
            result: {
                status: 'ok',
                execution_id: entry.id,
                cell_id: entry.cellId,
                cell_index: cellIndex,
                outputs: stripForAgent(toJupyter(cell)),
                execution_count: cell.executionSummary?.executionOrder ?? null,
                execution_mode: 'jupyter-kernel-api',
            }
        };
    } catch (err: any) {
        if (!started) {
            // Kernel API returned an object but execution failed before producing
            // any output — the API is broken (e.g. access denied on code-server).
            // Disable it for the rest of the session so we fall back cleanly.
            kernelApiDisabled = true;
            await clearAgentRunState(doc, cellIndex);
            return { fallbackReason: err?.message ?? String(err) };
        }

        const errorOutput = new vscode.NotebookCellOutput([
            vscode.NotebookCellOutputItem.error(
                err instanceof Error ? err : new Error(err?.message ?? String(err))
            )
        ]);
        outputs = [...outputs, errorOutput];
        await replaceCellState(doc, cellIndex, outputs, 'error');
        throw err;
    } finally {
        await clearAgentRunState(doc, cellIndex);
    }
}

async function runCellViaPrivateJupyterSession(
    doc: vscode.NotebookDocument,
    cellIndex: number,
    entry: QueueEntry
): Promise<{ result?: any; fallbackReason?: string }> {
    const kernel = await getPrivateJupyterKernel(doc);
    if (!kernel || typeof kernel.requestExecute !== 'function') {
        return { fallbackReason: 'no-private-jupyter-session' };
    }

    const startTime = Date.now();
    const source = doc.cellAt(cellIndex).document.getText();
    let rawOutputs: JupyterOutput[] = [];
    let clearOutputPending = false;
    let executionCount: number | null = null;
    let success = true;

    try {
        await replaceCellState(doc, cellIndex, [], 'running', {
            success: undefined,
            timing: { startTime, endTime: startTime },
        });

        const future = kernel.requestExecute({
            code: source.replace(/\r\n/g, '\n'),
            silent: false,
            stop_on_error: false,
            allow_stdin: false,
            store_history: true,
        });

        future.onIOPub = async (msg: any) => {
            const output = iopubMessageToJupyterOutput(msg);
            if (!output) { return; }
            if (output.output_type === 'clear_output') {
                clearOutputPending = output.wait === true;
                if (!clearOutputPending) {
                    rawOutputs = [];
                }
            } else {
                if (clearOutputPending) {
                    rawOutputs = [];
                    clearOutputPending = false;
                }
                rawOutputs = applyJupyterOutput(rawOutputs, output);
            }
            if (output.execution_count != null) {
                executionCount = output.execution_count;
            }
            if (output.output_type === 'error') {
                success = false;
            }
            await replaceCellState(
                doc,
                cellIndex,
                rawOutputs.map(toVSCode),
                success ? 'running' : 'error',
                {
                    executionOrder: executionCount ?? undefined,
                    success: undefined,
                    timing: { startTime, endTime: Date.now() },
                }
            );
        };

        future.onReply = (msg: any) => {
            const replyCount = msg?.content?.execution_count;
            if (typeof replyCount === 'number') {
                executionCount = replyCount;
            }
            if (msg?.content?.status === 'error') {
                success = false;
            }
        };

        await future.done;
        if (typeof future.dispose === 'function') {
            future.dispose();
        }
        if (clearOutputPending) {
            rawOutputs = [];
            clearOutputPending = false;
        }

        await replaceCellState(
            doc,
            cellIndex,
            rawOutputs.map(toVSCode),
            success ? undefined : 'error',
            {
                executionOrder: executionCount ?? undefined,
                success,
                timing: { startTime, endTime: Date.now() },
            }
        );

        return {
            result: {
                status: success ? 'ok' : 'error',
                execution_id: entry.id,
                cell_id: entry.cellId,
                cell_index: cellIndex,
                outputs: stripForAgent(rawOutputs),
                execution_count: executionCount,
                execution_mode: 'jupyter-private-session',
            }
        };
    } catch (err: any) {
        const message = err?.message ?? String(err);
        if (rawOutputs.length === 0) {
            kernelApiDisabled = true;
            await clearAgentRunState(doc, cellIndex);
            return { fallbackReason: message };
        }
        if (clearOutputPending) {
            rawOutputs = [];
            clearOutputPending = false;
        }

        rawOutputs.push({
            output_type: 'error',
            ename: err?.name ?? 'Error',
            evalue: message,
            traceback: [],
        });
        await replaceCellState(
            doc,
            cellIndex,
            rawOutputs.map(toVSCode),
            'error',
            {
                executionOrder: executionCount ?? undefined,
                success: false,
                timing: { startTime, endTime: Date.now() },
            }
        );
        throw err;
    } finally {
        await clearAgentRunState(doc, cellIndex);
    }
}

// Cache Jupyter API objects so we only trigger the access check once per session.
// The Jupyter extension identifies callers via stack inspection and shows a warning
// popup for unrecognized publishers on every getKernelService() / kernels.getKernel()
// call when the publisher isn't in their allowlist.
// When kernel API access fails at execution time (e.g. on code-server where our
// publisher isn't allowlisted), we disable the API for the rest of the session to
// avoid broken kernel objects that leave execution state stuck.
let cachedJupyterApi: any | undefined;
let cachedKernelService: any | undefined;
let kernelApiDisabled = false;

export async function getJupyterApi(): Promise<any | undefined> {
    if (cachedJupyterApi) { return cachedJupyterApi; }
    const jupyterExt = vscode.extensions.getExtension('ms-toolsai.jupyter');
    if (!jupyterExt) { return undefined; }
    cachedJupyterApi = jupyterExt.isActive ? jupyterExt.exports : await jupyterExt.activate();
    return cachedJupyterApi;
}

async function getJupyterKernel(doc: vscode.NotebookDocument): Promise<any | undefined> {
    const api = await getJupyterApi();
    const getKernel = api?.kernels?.getKernel;
    if (typeof getKernel !== 'function') { return undefined; }

    try {
        return await getKernel(doc.uri);
    } catch {
        return undefined;
    }
}

async function getPrivateJupyterKernel(doc: vscode.NotebookDocument): Promise<any | undefined> {
    if (!cachedKernelService) {
        const api = await getJupyterApi();
        const getKernelService = api?.getKernelService;
        if (typeof getKernelService !== 'function') { return undefined; }

        try {
            cachedKernelService = await getKernelService();
        } catch {
            return undefined;
        }
    }
    if (!cachedKernelService) { return undefined; }

    try {
        const kernel = await cachedKernelService.getKernel?.(doc.uri);
        return kernel?.connection?.kernel;
    } catch {
        return undefined;
    }
}

/** Reset cached Jupyter API references (e.g. after kernel restart). */
export function resetJupyterApiCache(): void {
    cachedKernelService = undefined;
    kernelApiDisabled = false;
    // Keep cachedJupyterApi — the extension object itself doesn't change.
}

function getExecutionMode(): ExecutionMode {
    const config = vscode.workspace.getConfiguration('agent-repl');
    const value = config.get<string>('executionMode', 'no-yank');
    return value === 'native' ? 'native' : 'no-yank';
}

async function replaceCellState(
    doc: vscode.NotebookDocument,
    cellIndex: number,
    outputs: vscode.NotebookCellOutput[],
    runStatus?: 'running' | 'error',
    executionSummary?: {
        executionOrder?: number;
        success?: boolean;
        timing?: { startTime: number; endTime: number };
    }
): Promise<void> {
    const cell = doc.cellAt(cellIndex);
    const data = new vscode.NotebookCellData(cell.kind, cell.document.getText(), cell.document.languageId);
    data.outputs = outputs;
    data.metadata = withAgentRunStatus(cell.metadata as Record<string, any> | undefined, runStatus);
    if (executionSummary) {
        data.executionSummary = executionSummary as vscode.NotebookCellExecutionSummary;
    } else if (cell.executionSummary) {
        data.executionSummary = cell.executionSummary;
    }

    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.replaceCells(new vscode.NotebookRange(cellIndex, cellIndex + 1), [data])]);
    await vscode.workspace.applyEdit(edit);
}

async function clearAgentRunState(doc: vscode.NotebookDocument, cellIndex: number): Promise<void> {
    const cell = doc.cellAt(cellIndex);
    const metadata = withAgentRunStatus(cell.metadata as Record<string, any> | undefined, undefined);
    const edit = new vscode.WorkspaceEdit();
    edit.set(doc.uri, [vscode.NotebookEdit.updateCellMetadata(cellIndex, metadata)]);
    await vscode.workspace.applyEdit(edit);
}

function withAgentRunStatus(
    existing: Record<string, any> | undefined,
    runStatus?: 'running' | 'error'
): Record<string, any> {
    const metadata = { ...(existing ?? {}) };
    const custom = { ...(metadata.custom ?? {}) };
    const agent = { ...(custom['agent-repl'] ?? {}) };

    if (runStatus) {
        agent.type = agent.type ?? 'agent-run';
        agent.run_status = runStatus;
    } else {
        delete agent.run_status;
        if (agent.type === 'agent-run') {
            delete agent.type;
        }
    }

    if (Object.keys(agent).length > 0) {
        custom['agent-repl'] = agent;
    } else {
        delete custom['agent-repl'];
    }

    if (Object.keys(custom).length > 0) {
        metadata.custom = custom;
    } else {
        delete metadata.custom;
    }

    return metadata;
}

function isNotebookOutput(value: any): value is vscode.NotebookCellOutput {
    return !!value && Array.isArray(value.items);
}

export function iopubMessageToJupyterOutput(msg: any): JupyterOutput | undefined {
    const type = msg?.header?.msg_type;
    const content = msg?.content ?? {};
    if (!type) { return undefined; }

    if (type === 'stream') {
        return {
            output_type: 'stream',
            name: content.name,
            text: content.text ?? '',
        };
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
        return {
            output_type: 'clear_output',
            wait: content.wait === true,
        };
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
    const normalized: JupyterOutput = {
        ...next,
        output_type: 'display_data',
    };
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

function waitForCompletion(
    doc: vscode.NotebookDocument,
    cellIndex: number,
    timeoutMs: number = 300_000
): Promise<CompletionReason> {
    return new Promise((resolve) => {
        const key = cellKey(doc.uri.fsPath, cellIndex);
        const initialExecutionOrder = doc.cellAt(cellIndex).executionSummary?.executionOrder ?? null;
        let done = false;

        const finish = (reason: CompletionReason) => {
            if (done) { return; }
            done = true;
            clearTimeout(timer);
            clearInterval(poll);
            completionCallbacks.delete(key);
            if (reason !== 'completed') {
                executingCells.delete(key);
                updateKernelState();
            }
            resolve(reason);
        };

        const poll = setInterval(() => {
            try {
                const cell = doc.cellAt(cellIndex);
                if (executionSummaryIndicatesCompletion(cell.executionSummary, initialExecutionOrder)) {
                    finish('completed');
                }
            } catch {
                // The cell may disappear during restart or edits; rely on reset/timeout.
            }
        }, 200);

        const timer = setTimeout(() => {
            // Completion event never fired — clean up and unblock the queue
            finish('timeout');
        }, timeoutMs);
        completionCallbacks.set(key, (reason) => {
            finish(reason);
        });
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

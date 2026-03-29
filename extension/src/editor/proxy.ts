import * as vscode from 'vscode';
import * as path from 'path';
import * as http from 'http';
import * as fs from 'fs';
import * as os from 'os';
import * as childProcess from 'child_process';
import * as util from 'util';
import { coreCliPlans, sessionIdForWorkspaceState } from '../session';
import { buildActivityPollResult } from '../shared/notebookActivity';
import { runNotebookCommandFlow } from '../shared/notebookCommandFlow';
import { buildReplaceSourceOperation } from '../shared/notebookEditPayload';
import { buildRuntimeSnapshot } from '../shared/runtimeSnapshot';
import { NotebookCellSnapshot, PyrightNotebookLspClient } from './lsp';
import type { CellData } from './protocol';

const execFile = util.promisify(childProcess.execFile);

const RUNTIME_FILE_PREFIX = 'agent-repl-core-';

/**
 * DaemonProxy — extension-host-side bridge between the Canvas WebView and the
 * agent-repl core daemon. Uses direct HTTP for speed, CLI as fallback.
 */
export class DaemonProxy {
    readonly disposables: vscode.Disposable[] = [];
    private pollTimer: ReturnType<typeof setInterval> | undefined;
    private activityCursor = 0;
    private notebookPath: string;
    private workspaceRoot: string;
    private disposed = false;
    private daemonUrl: string | undefined;
    private daemonToken: string | undefined;
    private cells: CellData[] = [];
    private draftSources = new Map<string, string>();
    private lspClient: PyrightNotebookLspClient | null = null;

    constructor(
        private readonly documentUri: vscode.Uri,
        private readonly panel: vscode.WebviewPanel,
        private readonly context: vscode.ExtensionContext,
    ) {
        const wsFolder = vscode.workspace.getWorkspaceFolder(documentUri);
        this.workspaceRoot = wsFolder?.uri.fsPath ?? path.dirname(documentUri.fsPath);
        this.notebookPath = path.relative(this.workspaceRoot, documentUri.fsPath);
    }

    async start(): Promise<void> {}

    async handleMessage(msg: { type: string; requestId?: string; [key: string]: any }): Promise<void> {
        if (this.disposed) return;
        try {
            switch (msg.type) {
                case 'webview-ready': await this.onWebviewReady(msg.requestId ?? ''); break;
                case 'load-contents': await this.loadContents(msg.requestId ?? ''); break;
                case 'edit': await this.handleEdit(msg); break;
                case 'execute-cell': await this.handleExecuteCell(msg); break;
                case 'interrupt-execution': await this.handleInterruptExecution(msg); break;
                case 'execute-all': await this.handleExecuteAll(msg); break;
                case 'select-kernel': await this.handleSelectKernel(msg); break;
                case 'restart-kernel': await this.handleRestart(msg); break;
                case 'restart-and-run-all': await this.handleRestartAndRunAll(msg); break;
                case 'get-kernels': await this.handleGetKernels(msg); break;
                case 'get-runtime': await this.handleGetRuntime(msg); break;
                case 'flush-draft': await this.handleFlushDraft(msg); break;
                case 'lsp-sync-cell': await this.handleLspSyncCell(msg); break;
                case 'lsp-complete': await this.handleLspComplete(msg); break;
                case 'open-external-link': await this.handleOpenLink(msg); break;
            }
        } catch (err: any) {
            this.postMessage({
                type: 'error',
                requestId: msg.requestId ?? '',
                message: err?.message ?? String(err),
                conflict: Boolean(err?.conflict),
            });
        }
    }

    onVisibilityChanged(visible: boolean): void {
        if (visible && !this.pollTimer) {
            this.startPolling();
            void this.syncPresence('observing');
        } else if (!visible && this.pollTimer) {
            this.stopPolling();
            void this.clearPresence();
        }
    }

    dispose(): void {
        this.disposed = true;
        this.stopPolling();
        void this.clearPresence();
        this.lspClient?.dispose();
        this.lspClient = null;
        for (const d of this.disposables) d.dispose();
        this.disposables.length = 0;
    }

    // -----------------------------------------------------------------------
    // Daemon HTTP discovery
    // -----------------------------------------------------------------------

    private discoverDaemon(): boolean {
        if (this.daemonUrl) return true;
        const runtimeDir = path.join(os.homedir(), 'Library', 'Jupyter', 'runtime');
        try {
            const files = fs.readdirSync(runtimeDir)
                .filter(f => f.startsWith(RUNTIME_FILE_PREFIX) && f.endsWith('.json'))
                .map(f => ({ name: f, mtime: fs.statSync(path.join(runtimeDir, f)).mtimeMs }))
                .sort((a, b) => b.mtime - a.mtime);

            for (const file of files) {
                try {
                    const info = JSON.parse(fs.readFileSync(path.join(runtimeDir, file.name), 'utf-8'));
                    const wsRoot = fs.realpathSync(info.workspace_root);
                    const myRoot = fs.realpathSync(this.workspaceRoot);
                    if (myRoot.startsWith(wsRoot)) {
                        this.daemonUrl = `http://127.0.0.1:${info.port}`;
                        this.daemonToken = info.token;
                        return true;
                    }
                } catch { continue; }
            }
        } catch { /* runtime dir may not exist */ }
        return false;
    }

    private async httpPost(endpoint: string, body: any): Promise<any> {
        if (!this.discoverDaemon()) throw new Error('Daemon not found');
        return new Promise((resolve, reject) => {
            const url = new URL(endpoint, this.daemonUrl!);
            const data = JSON.stringify(body);
            const req = http.request(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(data),
                    'Authorization': `token ${this.daemonToken}`,
                },
                timeout: 30_000,
            }, (res) => {
                let chunks: Buffer[] = [];
                res.on('data', (chunk: Buffer) => chunks.push(chunk));
                res.on('end', () => {
                    const raw = Buffer.concat(chunks).toString();
                    const statusCode = res.statusCode ?? 500;
                    try {
                        const payload = raw ? JSON.parse(raw) : {};
                        if (statusCode >= 400) {
                            const error: any = new Error(payload?.error ?? payload?.message ?? `Daemon HTTP ${statusCode}`);
                            error.statusCode = statusCode;
                            error.payload = payload;
                            error.conflict = Boolean(payload?.conflict || payload?.reason === 'lease-conflict');
                            reject(error);
                            return;
                        }
                        resolve(payload);
                    } catch {
                        const prefix = raw.slice(0, 200);
                        reject(new Error(`Invalid JSON from daemon: ${prefix}`));
                    }
                });
            });
            req.on('error', reject);
            req.on('timeout', () => { req.destroy(); reject(new Error('Daemon request timeout')); });
            req.write(data);
            req.end();
        });
    }

    private async httpGet(endpoint: string, params?: Record<string, string>): Promise<any> {
        if (!this.discoverDaemon()) throw new Error('Daemon not found');
        return new Promise((resolve, reject) => {
            const url = new URL(endpoint, this.daemonUrl!);
            if (params) { for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v); }
            const req = http.request(url, {
                method: 'GET',
                headers: { 'Authorization': `token ${this.daemonToken}` },
                timeout: 15_000,
            }, (res) => {
                let chunks: Buffer[] = [];
                res.on('data', (chunk: Buffer) => chunks.push(chunk));
                res.on('end', () => {
                    const raw = Buffer.concat(chunks).toString();
                    const statusCode = res.statusCode ?? 500;
                    try {
                        const payload = raw ? JSON.parse(raw) : {};
                        if (statusCode >= 400) {
                            const error: any = new Error(payload?.error ?? payload?.message ?? `Daemon HTTP ${statusCode}`);
                            error.statusCode = statusCode;
                            error.payload = payload;
                            error.conflict = Boolean(payload?.conflict || payload?.reason === 'lease-conflict');
                            reject(error);
                            return;
                        }
                        resolve(payload);
                    } catch {
                        reject(new Error(`Invalid JSON from daemon`));
                    }
                });
            });
            req.on('error', reject);
            req.on('timeout', () => { req.destroy(); reject(new Error('Daemon request timeout')); });
            req.end();
        });
    }

    // -----------------------------------------------------------------------
    // Initial load
    // -----------------------------------------------------------------------

    private async onWebviewReady(requestId: string): Promise<void> {
        await this.handleGetRuntime({ requestId: 'init-runtime' });
        await this.loadContents(requestId);
        this.ensureLspStarted();
        await this.handleGetKernels({ requestId: 'init-kernels' });
        await this.syncPresence('observing');
        this.startPolling();
    }

    private async loadContents(requestId: string): Promise<void> {
        const result = await this.httpPost('/api/notebooks/contents', { path: this.notebookPath });
        this.replaceCells(result.cells ?? []);
        this.postMessage({ type: 'contents', requestId, cells: this.cells });
    }

    // -----------------------------------------------------------------------
    // Cell operations
    // -----------------------------------------------------------------------

    private async handleEdit(msg: any): Promise<void> {
        const operations = msg.operations ?? [];
        const ownerSessionId = this.ownerSessionId();
        for (const op of operations) {
            const body: any = { path: this.notebookPath };
            if (ownerSessionId) {
                body.owner_session_id = ownerSessionId;
            }
            switch (op.op) {
                case 'insert':
                    body.operations = [{ op: 'insert', source: op.source, cell_type: op.cell_type || 'code', at_index: op.at_index ?? -1 }];
                    break;
                case 'delete':
                    body.operations = [{ op: 'delete', cell_id: op.cell_id }];
                    break;
                case 'replace-source':
                    body.operations = [buildReplaceSourceOperation({ cell_id: op.cell_id, source: op.source })];
                    break;
                case 'change-cell-type':
                    body.operations = [{
                        op: 'change-cell-type',
                        cell_id: op.cell_id,
                        cell_type: op.cell_type || 'code',
                        ...(typeof op.source === 'string' ? { source: op.source } : {}),
                    }];
                    break;
                case 'move':
                    body.operations = [{ op: 'move', cell_id: op.cell_id, to_index: op.to_index }];
                    break;
                default: continue;
            }
            await this.httpPost('/api/notebooks/edit', body);
        }
        await this.loadContents(msg.requestId);
    }

    private async handleFlushDraft(msg: any): Promise<void> {
        const body: any = {
            path: this.notebookPath,
            operations: [buildReplaceSourceOperation({ cell_id: msg.cell_id, source: msg.source })],
        };
        const ownerSessionId = this.ownerSessionId();
        if (ownerSessionId) {
            body.owner_session_id = ownerSessionId;
        }
        await this.httpPost('/api/notebooks/edit', body);
        this.draftSources.delete(msg.cell_id);
        this.updateCellSource(msg.cell_id, msg.source);
        this.syncLsp();
        this.postMessage({ type: 'ok', requestId: msg.requestId });
    }

    private async handleLspSyncCell(msg: any): Promise<void> {
        if (typeof msg.cell_id !== 'string' || typeof msg.source !== 'string') {
            return;
        }
        this.draftSources.set(msg.cell_id, msg.source);
        this.updateCellSource(msg.cell_id, msg.source);
        this.syncLsp();
    }

    private async handleLspComplete(msg: any): Promise<void> {
        if (
            typeof msg.requestId !== 'string' ||
            typeof msg.cell_id !== 'string' ||
            typeof msg.source !== 'string' ||
            typeof msg.offset !== 'number'
        ) {
            return;
        }

        this.draftSources.set(msg.cell_id, msg.source);
        this.updateCellSource(msg.cell_id, msg.source);
        this.syncLsp();
        const items = await this.lspClient?.completeAt(
            msg.cell_id,
            msg.offset,
            typeof msg.trigger_character === 'string' ? msg.trigger_character : undefined,
            Boolean(msg.explicit),
        ) ?? [];
        this.postMessage({
            type: 'lsp-completions',
            requestId: msg.requestId,
            cell_id: msg.cell_id,
            items,
        });
    }

    // -----------------------------------------------------------------------
    // Execution
    // -----------------------------------------------------------------------

    private async handleExecuteCell(msg: any): Promise<void> {
        const body: any = {
            path: this.notebookPath,
            cell_id: msg.cell_id,
        };
        const ownerSessionId = this.ownerSessionId();
        if (ownerSessionId) {
            body.owner_session_id = ownerSessionId;
        }
        const sourceOverride = typeof msg.source === 'string' ? msg.source : undefined;

        try {
            if (sourceOverride !== undefined) {
                const editBody: any = {
                    path: this.notebookPath,
                    operations: [buildReplaceSourceOperation({ cell_id: msg.cell_id, source: sourceOverride })],
                };
                if (ownerSessionId) {
                    editBody.owner_session_id = ownerSessionId;
                }
                await this.httpPost('/api/notebooks/edit', editBody);
                this.draftSources.delete(msg.cell_id);
                this.updateCellSource(msg.cell_id, sourceOverride);
                this.syncLsp();
            }

            const result = await this.httpPost('/api/notebooks/execute-cell', body);
            const resultStatus = typeof result?.status === 'string' ? result.status : 'started';
            const resultCellId = typeof result?.cell_id === 'string' ? result.cell_id : msg.cell_id;

            if (resultStatus === 'error') {
                this.postMessage({
                    type: 'execute-failed',
                    requestId: msg.requestId,
                    cell_id: resultCellId,
                    message: typeof result?.error === 'string' && result.error.trim()
                        ? result.error
                        : 'Execution failed.',
                });
                await this.handleGetRuntime({ requestId: 'execute-failed-runtime' });
                return;
            }

            if (resultStatus === 'started') {
                this.postMessage({
                    type: 'execute-started',
                    requestId: msg.requestId,
                    execution_id: typeof result?.execution_id === 'string' ? result.execution_id : undefined,
                    cell_id: resultCellId,
                });
                await this.handleGetRuntime({
                    requestId: 'execute-started-runtime',
                });
                return;
            }

            if (resultStatus === 'ok') {
                await this.refreshNotebookSurfaceAfterSuccess(
                    'execute-cell',
                    async () => {
                        await this.loadContents('execute-finished');
                    },
                );
                this.postMessage({
                    type: 'execute-finished',
                    requestId: msg.requestId,
                    cell_id: resultCellId,
                    ok: true,
                });
                await this.handleGetRuntime({
                    requestId: 'execute-finished-runtime',
                });
                return;
            }

            if (resultStatus === 'queued') {
                this.postMessage({
                    type: 'ok',
                    requestId: msg.requestId,
                });
                await this.handleGetRuntime({
                    requestId: 'execute-queued-runtime',
                });
                return;
            }

            this.postMessage({
                type: 'ok',
                requestId: msg.requestId,
            });
            await this.handleGetRuntime({
                requestId: 'execute-runtime',
            });
        } catch (err: any) {
            this.postMessage({
                type: 'execute-failed',
                requestId: msg.requestId,
                cell_id: msg.cell_id,
                message: err?.message ?? String(err),
            });
            await this.handleGetRuntime({ requestId: 'execute-failed-runtime' });
        }
    }

    private async handleExecuteAll(msg: any): Promise<void> {
        const body: any = { path: this.notebookPath };
        const ownerSessionId = this.ownerSessionId();
        if (ownerSessionId) {
            body.owner_session_id = ownerSessionId;
        }
        runNotebookCommandFlow({
            run: () => this.httpPost('/api/notebooks/execute-all', body),
            onSuccess: async () => {
                this.postMessage({ type: 'ok', requestId: msg.requestId });
                await this.refreshNotebookSurfaceAfterSuccess(
                    'execute-all',
                    async () => {
                        await this.loadContents('execute-all-finished');
                        await this.handleGetRuntime({ requestId: 'execute-all-runtime' });
                    },
                );
            },
            onConflict: async (err: any) => {
                if (this.isSelfLeaseConflict(err, ownerSessionId)) {
                    void this.executeAllViaOwnedCells(msg.requestId, ownerSessionId);
                    return true;
                }
                return false;
            },
            onError: async (err: any) => {
                this.postMessage({
                    type: 'error',
                    requestId: msg.requestId,
                    message: err?.message ?? String(err),
                    conflict: Boolean(err?.conflict),
                });
            },
        });
    }

    private async handleInterruptExecution(msg: any): Promise<void> {
        await this.httpPost('/api/notebooks/interrupt', { path: this.notebookPath });
        this.postMessage({ type: 'ok', requestId: msg.requestId });
        await this.loadContents('interrupt-execution');
        await this.handleGetRuntime({ requestId: 'interrupt-execution-runtime' });
    }

    // -----------------------------------------------------------------------
    // Kernel management
    // -----------------------------------------------------------------------

    private async handleSelectKernel(msg: any): Promise<void> {
        await this.httpPost('/api/notebooks/select-kernel', {
            path: this.notebookPath,
            kernel_id: msg.kernel_id,
        });
        this.postMessage({ type: 'ok', requestId: msg.requestId });
        await this.handleGetKernels({ requestId: 'select-kernel-kernels' });
        await this.handleGetRuntime({ requestId: 'select-kernel-runtime' });
    }

    private async handleRestart(msg: any): Promise<void> {
        await this.httpPost('/api/notebooks/restart', { path: this.notebookPath });
        this.postMessage({ type: 'ok', requestId: msg.requestId });
        await this.loadContents('restart-kernel');
        await this.handleGetRuntime({ requestId: 'restart-kernel-runtime' });
    }

    private async handleRestartAndRunAll(msg: any): Promise<void> {
        const body: any = { path: this.notebookPath };
        const ownerSessionId = this.ownerSessionId();
        if (ownerSessionId) {
            body.owner_session_id = ownerSessionId;
        }
        runNotebookCommandFlow({
            run: () => this.httpPost('/api/notebooks/restart-and-run-all', body),
            onSuccess: async () => {
                this.postMessage({ type: 'ok', requestId: msg.requestId });
                await this.refreshNotebookSurfaceAfterSuccess(
                    'restart-and-run-all',
                    async () => {
                        await this.loadContents('restart-and-run-all-finished');
                        await this.handleGetRuntime({ requestId: 'restart-and-run-all-runtime' });
                    },
                );
            },
            onConflict: async (err: any) => {
                if (this.isSelfLeaseConflict(err, ownerSessionId)) {
                    void this.restartAndExecuteAllViaOwnedCells(msg.requestId, ownerSessionId);
                    return true;
                }
                return false;
            },
            onError: async (err: any) => {
                this.postMessage({
                    type: 'error',
                    requestId: msg.requestId,
                    message: err?.message ?? String(err),
                    conflict: Boolean(err?.conflict),
                });
            },
        });
    }

    private async handleGetKernels(msg: any): Promise<void> {
        try {
            // Kernels discovery is CLI-only (not on the daemon API)
            const result = await this.runCli(['kernels']);
            this.postMessage({
                type: 'kernels',
                requestId: msg.requestId,
                kernels: (result.kernels ?? []).map((k: any) => ({
                    id: k.id, label: k.label, recommended: k.recommended ?? false,
                })),
                preferred_kernel: result.preferred_kernel
                    ? { id: result.preferred_kernel.id, label: result.preferred_kernel.label }
                    : undefined,
            });
        } catch {
            this.postMessage({ type: 'kernels', requestId: msg.requestId, kernels: [] });
        }
    }

    private async handleGetRuntime(msg: any): Promise<void> {
        const [runtimeResult, statusResult] = await Promise.all([
            this.httpPost('/api/notebooks/runtime', { path: this.notebookPath }),
            this.httpPost('/api/notebooks/status', { path: this.notebookPath }),
        ]);
        const snapshot = buildRuntimeSnapshot({
            ...runtimeResult,
            running: Array.isArray(statusResult?.running) ? statusResult.running : undefined,
            queued: Array.isArray(statusResult?.queued) ? statusResult.queued : undefined,
        });
        this.postMessage({
            type: 'runtime',
            requestId: msg.requestId,
            active: snapshot.active,
            busy: snapshot.busy,
            kernel_label: snapshot.kernel_label,
            runtime_id: snapshot.runtime_id,
            kernel_generation: snapshot.kernel_generation,
            current_execution: snapshot.current_execution,
            running_cell_ids: snapshot.running_cell_ids,
            queued_cell_ids: snapshot.queued_cell_ids,
        });
    }

    // -----------------------------------------------------------------------
    // Activity polling — direct HTTP, fast
    // -----------------------------------------------------------------------

    private startPolling(): void {
        if (this.pollTimer || this.disposed) return;
        this.pollTimer = setInterval(() => this.pollActivity(), 500);
        void this.pollActivity();
    }

    private stopPolling(): void {
        if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = undefined; }
    }

    private async pollActivity(): Promise<void> {
        if (this.disposed) { this.stopPolling(); return; }
        try {
            const body: any = { path: this.notebookPath };
            if (this.activityCursor > 0) body.since = this.activityCursor;
            const result = await this.httpPost('/api/notebooks/activity', body);

            const activityResult = buildActivityPollResult(result, {
                cursorFallback: this.activityCursor,
                includeDetachedRuntime: true,
                inlineSourceUpdates: true,
                reloadOnSourceUpdates: false,
            });
            for (const cell of activityResult.sourceUpdates) {
                this.upsertCell(cell as CellData);
            }
            if (activityResult.shouldReloadContents) {
                await this.loadContents('activity-reload');
            } else if (activityResult.shouldSyncLsp) {
                this.syncLsp();
            }
            const activitySnapshot = activityResult.activityUpdate;
            if (!activitySnapshot) return;

            this.postMessage({
                type: 'activity-update',
                events: activitySnapshot.events,
                presence: activitySnapshot.presence,
                leases: activitySnapshot.leases,
                runtime: activitySnapshot.runtime,
                cursor: activitySnapshot.cursor,
            });

            if (typeof result.cursor === 'number') this.activityCursor = result.cursor;
        } catch { /* non-fatal */ }
    }

    // -----------------------------------------------------------------------
    // External links
    // -----------------------------------------------------------------------

    private async handleOpenLink(msg: any): Promise<void> {
        await vscode.env.openExternal(vscode.Uri.parse(msg.url));
        this.postMessage({ type: 'ok', requestId: msg.requestId });
    }

    private ownerSessionId(): string | undefined {
        return sessionIdForWorkspaceState(this.context, this.workspaceRoot);
    }

    private async syncPresence(activity: string): Promise<void> {
        const sessionId = this.ownerSessionId();
        if (!sessionId) {
            return;
        }
        try {
            await this.httpPost('/api/sessions/presence/upsert', {
                session_id: sessionId,
                path: this.notebookPath,
                activity,
            });
        } catch {
            // Presence is best-effort; activity polling is still authoritative.
        }
    }

    private async clearPresence(): Promise<void> {
        const sessionId = this.ownerSessionId();
        if (!sessionId) {
            return;
        }
        try {
            await this.httpPost('/api/sessions/presence/clear', {
                session_id: sessionId,
                path: this.notebookPath,
            });
        } catch {
            // Presence clear is best-effort during panel teardown.
        }
    }

    private isSelfLeaseConflict(err: any, ownerSessionId: string | undefined): boolean {
        if (!ownerSessionId || !err?.conflict) {
            return false;
        }
        const holderSessionId = err?.payload?.conflict?.holder?.session_id;
        const leaseSessionId = err?.payload?.conflict?.lease?.session_id;
        return holderSessionId === ownerSessionId || leaseSessionId === ownerSessionId;
    }

    private async executeAllViaOwnedCells(
        requestId: string,
        ownerSessionId: string | undefined,
        options?: {
            action?: string;
            contentsRequestId?: string;
            runtimeRequestId?: string;
        },
    ): Promise<void> {
        try {
            const codeCellIds = this.cells
                .filter((cell) => cell.cell_type === 'code')
                .map((cell) => cell.cell_id);
            for (const cellId of codeCellIds) {
                const body: any = {
                    path: this.notebookPath,
                    cell_id: cellId,
                };
                if (ownerSessionId) {
                    body.owner_session_id = ownerSessionId;
                }
                const result = await this.httpPost('/api/notebooks/execute-cell', body);
                if (result?.status === 'error') {
                    break;
                }
            }
            this.postMessage({ type: 'ok', requestId });
            await this.refreshNotebookSurfaceAfterSuccess(
                options?.action ?? 'execute-all-fallback',
                async () => {
                    await this.loadContents(options?.contentsRequestId ?? 'execute-all-finished');
                    await this.handleGetRuntime({ requestId: options?.runtimeRequestId ?? 'execute-all-runtime' });
                },
            );
        } catch (err: any) {
            this.postMessage({
                type: 'error',
                requestId,
                message: err?.message ?? String(err),
                conflict: Boolean(err?.conflict),
            });
        }
    }

    private async restartAndExecuteAllViaOwnedCells(
        requestId: string,
        ownerSessionId: string | undefined,
    ): Promise<void> {
        try {
            await this.httpPost('/api/notebooks/restart', { path: this.notebookPath });
            await this.executeAllViaOwnedCells(requestId, ownerSessionId, {
                action: 'restart-and-run-all-fallback',
                contentsRequestId: 'restart-and-run-all-finished',
                runtimeRequestId: 'restart-and-run-all-runtime',
            });
        } catch (err: any) {
            this.postMessage({
                type: 'error',
                requestId,
                message: err?.message ?? String(err),
                conflict: Boolean(err?.conflict),
            });
        }
    }

    private async refreshNotebookSurfaceAfterSuccess(
        action: string,
        refresh: () => Promise<void>,
    ): Promise<void> {
        try {
            await refresh();
        } catch (err: any) {
            console.warn(
                `[agent-repl] ${action} refresh failed:`,
                err?.message ?? String(err),
            );
        }
    }

    private replaceCells(nextCells: any[]): void {
        this.cells = nextCells.map((cell: any) => this.normalizeCell(cell));
        this.syncLsp();
    }

    private upsertCell(cell: any): void {
        const index = this.cells.findIndex((candidate) => candidate.cell_id === cell.cell_id);
        const nextCell = this.normalizeCell(cell, index);
        if (index >= 0) {
            this.cells[index] = nextCell;
        } else {
            this.cells.push(nextCell);
            this.cells.sort((left, right) => left.index - right.index);
        }
    }

    private updateCellSource(cellId: string, source: string): void {
        const cell = this.cells.find((candidate) => candidate.cell_id === cellId);
        if (!cell) {
            return;
        }
        cell.source = source;
    }

    private normalizeCell(cell: any, fallbackIndex = 0): CellData {
        return {
            index: typeof cell?.index === 'number' ? cell.index : fallbackIndex,
            cell_id: String(cell?.cell_id ?? ''),
            cell_type: cell?.cell_type ?? 'code',
            source: this.draftSources.get(cell?.cell_id) ?? cell?.source ?? '',
            outputs: Array.isArray(cell?.outputs) ? cell.outputs : [],
            execution_count: typeof cell?.execution_count === 'number' ? cell.execution_count : null,
            display_number: typeof cell?.display_number === 'number' ? cell.display_number : null,
            metadata: cell?.metadata && typeof cell.metadata === 'object' ? cell.metadata : undefined,
        };
    }

    private toLspSnapshots(): NotebookCellSnapshot[] {
        return this.cells.map((cell) => ({
            index: cell.index,
            cell_id: cell.cell_id,
            cell_type: cell.cell_type,
            source: cell.source,
        }));
    }

    private ensureLspStarted(): void {
        if (this.lspClient) {
            return;
        }

        const configuredCommand = vscode.workspace.getConfiguration('agent-repl').get<string>('pyrightCommand')?.trim() || '';
        const bundledServerScript = path.join(this.context.extensionPath, 'node_modules', 'pyright', 'langserver.index.js');
        const serverCommand = configuredCommand || process.execPath;
        const serverArgs = configuredCommand ? ['--stdio'] : [bundledServerScript, '--stdio'];

        this.lspClient = new PyrightNotebookLspClient(
            this.workspaceRoot,
            this.documentUri.fsPath,
            diagnosticsByCell => this.postMessage({
                type: 'lsp-diagnostics',
                diagnostics_by_cell: diagnosticsByCell,
            }),
            status => this.postMessage({
                type: 'lsp-status',
                state: status.state,
                message: status.message,
            }),
            serverCommand,
            serverArgs,
        );
        void this.lspClient.start(this.toLspSnapshots()).catch(() => {});
    }

    private syncLsp(): void {
        this.ensureLspStarted();
        this.lspClient?.syncCells(this.toLspSnapshots());
    }

    // -----------------------------------------------------------------------
    // CLI fallback (only for commands not on the daemon API)
    // -----------------------------------------------------------------------

    private async runCli(args: string[]): Promise<any> {
        const config = vscode.workspace.getConfiguration('agent-repl');
        for (const plan of coreCliPlans(this.workspaceRoot, config)) {
            try {
                const result = await execFile(plan.command, [...plan.args, ...args], {
                    cwd: plan.cwd, timeout: 15_000,
                });
                return JSON.parse(result.stdout);
            } catch { continue; }
        }
        throw new Error('CLI failed');
    }

    private postMessage(msg: any): void {
        if (!this.disposed) this.panel.webview.postMessage(msg);
    }
}

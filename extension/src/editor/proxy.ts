import * as vscode from 'vscode';
import * as path from 'path';
import * as http from 'http';
import * as fs from 'fs';
import * as os from 'os';
import * as childProcess from 'child_process';
import * as util from 'util';
import { coreCliPlans } from '../session';

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
                case 'execute-all': await this.handleExecuteAll(msg); break;
                case 'select-kernel': await this.handleSelectKernel(msg); break;
                case 'restart-kernel': await this.handleRestart(msg); break;
                case 'restart-and-run-all': await this.handleRestartAndRunAll(msg); break;
                case 'get-kernels': await this.handleGetKernels(msg); break;
                case 'get-runtime': await this.handleGetRuntime(msg); break;
                case 'flush-draft': await this.handleFlushDraft(msg); break;
                case 'open-external-link': await this.handleOpenLink(msg); break;
            }
        } catch (err: any) {
            this.postMessage({ type: 'error', requestId: msg.requestId ?? '', message: err?.message ?? String(err) });
        }
    }

    onVisibilityChanged(visible: boolean): void {
        if (visible && !this.pollTimer) this.startPolling();
        else if (!visible && this.pollTimer) this.stopPolling();
    }

    dispose(): void {
        this.disposed = true;
        this.stopPolling();
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
                    try {
                        resolve(JSON.parse(Buffer.concat(chunks).toString()));
                    } catch {
                        reject(new Error(`Invalid JSON from daemon: ${Buffer.concat(chunks).toString().slice(0, 200)}`));
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
                    try {
                        resolve(JSON.parse(Buffer.concat(chunks).toString()));
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
        await this.loadContents(requestId);
        await this.handleGetKernels({ requestId: 'init-kernels' });
        this.startPolling();
    }

    private async loadContents(requestId: string): Promise<void> {
        const result = await this.httpPost('/api/notebooks/contents', { path: this.notebookPath });
        this.postMessage({ type: 'contents', requestId, cells: result.cells ?? [] });
    }

    // -----------------------------------------------------------------------
    // Cell operations
    // -----------------------------------------------------------------------

    private async handleEdit(msg: any): Promise<void> {
        const operations = msg.operations ?? [];
        for (const op of operations) {
            const body: any = { path: this.notebookPath };
            switch (op.op) {
                case 'insert':
                    body.operations = [{ op: 'insert', source: op.source, cell_type: op.cell_type || 'code', at_index: op.at_index ?? -1 }];
                    break;
                case 'delete':
                    body.operations = [{ op: 'delete', cell_id: op.cell_id }];
                    break;
                case 'replace-source':
                    body.operations = [{ op: 'replace-source', cell_id: op.cell_id, source: op.source }];
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
        await this.httpPost('/api/notebooks/edit', {
            path: this.notebookPath,
            operations: [{ op: 'replace-source', cell_id: msg.cell_id, source: msg.source }],
        });
        this.postMessage({ type: 'ok', requestId: msg.requestId });
    }

    // -----------------------------------------------------------------------
    // Execution
    // -----------------------------------------------------------------------

    private async handleExecuteCell(msg: any): Promise<void> {
        // Fire-and-forget — outputs arrive via activity polling
        this.httpPost('/api/notebooks/execute-cell', {
            path: this.notebookPath,
            cell_id: msg.cell_id,
            wait: false,
        }).catch(() => {});
        this.postMessage({
            type: 'execute-started',
            requestId: msg.requestId,
            cell_id: msg.cell_id,
        });
    }

    private async handleExecuteAll(msg: any): Promise<void> {
        // Fire-and-forget — don't block the WebView while all cells run
        this.httpPost('/api/notebooks/execute-all', { path: this.notebookPath }).catch(() => {});
        this.postMessage({ type: 'ok', requestId: msg.requestId });
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
    }

    private async handleRestart(msg: any): Promise<void> {
        await this.httpPost('/api/notebooks/restart', { path: this.notebookPath });
        this.postMessage({ type: 'ok', requestId: msg.requestId });
    }

    private async handleRestartAndRunAll(msg: any): Promise<void> {
        this.httpPost('/api/notebooks/restart-and-run-all', { path: this.notebookPath }).catch(() => {});
        this.postMessage({ type: 'ok', requestId: msg.requestId });
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
        const result = await this.httpPost('/api/notebooks/runtime', { path: this.notebookPath });
        this.postMessage({
            type: 'runtime',
            requestId: msg.requestId,
            busy: result.runtime?.busy ?? false,
            kernel_label: result.runtime?.python_path ?? undefined,
            current_execution: result.runtime?.current_execution ?? null,
        });
    }

    // -----------------------------------------------------------------------
    // Activity polling — direct HTTP, fast
    // -----------------------------------------------------------------------

    private startPolling(): void {
        if (this.pollTimer || this.disposed) return;
        this.pollTimer = setInterval(() => this.pollActivity(), 500);
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

            const events = result.recent_events ?? [];
            if (events.length === 0 && !result.runtime) return;

            this.postMessage({
                type: 'activity-update',
                events: events.map((e: any) => ({
                    event_id: e.event_id, path: e.path, event_type: e.type,
                    detail: e.detail, actor: e.actor, session_id: e.session_id,
                    cell_id: e.cell_id, cell_index: e.cell_index,
                    data: e.data, timestamp: e.timestamp,
                })),
                presence: result.presence ?? [],
                leases: result.leases ?? [],
                runtime: result.runtime
                    ? { busy: result.runtime.busy ?? false, current_execution: result.runtime.current_execution }
                    : null,
                cursor: result.cursor ?? this.activityCursor,
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

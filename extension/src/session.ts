import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import * as util from 'util';
import * as vscode from 'vscode';
import { toJupyter, toVSCode } from './notebook/outputs';
import { pushActivityEvent } from './routes';
import { logNotebookDiagnostic } from './debug';
import { DaemonWebSocket, type SocketLike } from './shared/wsClient';

const execFile = util.promisify(childProcess.execFile);
const HEARTBEAT_INTERVAL_MS = 30_000;
export const PROJECTION_CONTROLLER_ID = 'agent-repl.headless-runtime';

type CliPlan = {
    command: string;
    args: string[];
    cwd: string;
};

type SessionRef = {
    workspaceRoot: string;
    sessionId: string;
};

export function primaryWorkspaceRoot(): string | undefined {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

export function workspaceRootForPath(fsPath: string): string | undefined {
    const folders = vscode.workspace.workspaceFolders ?? [];
    for (const folder of folders) {
        if (fsPath === folder.uri.fsPath || fsPath.startsWith(`${folder.uri.fsPath}${path.sep}`)) {
            return folder.uri.fsPath;
        }
    }
    return primaryWorkspaceRoot();
}

export function sessionIdForWorkspaceState(
    context: vscode.ExtensionContext,
    workspaceRoot: string,
): string | undefined {
    return context.workspaceState.get<string>(sessionStorageKey(workspaceRoot));
}

function workspaceExecutable(workspaceRoot: string, executable: string): string {
    const binDir = process.platform === 'win32' ? 'Scripts' : 'bin';
    return path.join(workspaceRoot, '.venv', binDir, executable);
}

function existingWorkspaceCliPath(workspaceRoot: string): string | undefined {
    const executable = process.platform === 'win32' ? 'agent-repl.exe' : 'agent-repl';
    const candidate = workspaceExecutable(workspaceRoot, executable);
    return fs.existsSync(candidate) ? candidate : undefined;
}

function configuredCliPath(config: vscode.WorkspaceConfiguration): string | undefined {
    const value = config.get<string>('cliCommand') ?? config.get<string>('cliPath');
    const trimmed = value?.trim();
    return trimmed ? trimmed : undefined;
}

function autoAttachEnabled(config: vscode.WorkspaceConfiguration): boolean {
    return config.get<boolean>('sessionAutoAttach', config.get<boolean>('sessionAutoAttach', true));
}

export function coreCliPlans(workspaceRoot: string, config: vscode.WorkspaceConfiguration): CliPlan[] {
    const plans: CliPlan[] = [];
    const seen = new Set<string>();
    const push = (command: string, args: string[]) => {
        const key = `${command}\0${args.join('\0')}`;
        if (!seen.has(key)) {
            plans.push({ command, args, cwd: workspaceRoot });
            seen.add(key);
        }
    };

    const cliPath = configuredCliPath(config);
    if (cliPath) {
        push(cliPath, []);
    }

    const workspaceCli = existingWorkspaceCliPath(workspaceRoot);
    if (workspaceCli) {
        push(workspaceCli, []);
    }

    if (fs.existsSync(path.join(workspaceRoot, 'pyproject.toml'))) {
        push('uv', ['run', 'agent-repl']);
    }

    push('agent-repl', []);
    return plans;
}

type DaemonInfo = {
    url: string;
    token: string;
};

const RUNTIME_FILE_PREFIX = 'agent-repl-core-';

export function discoverDaemon(workspaceRoot: string): DaemonInfo | undefined {
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
                const myRoot = fs.realpathSync(workspaceRoot);
                if (myRoot.startsWith(wsRoot)) {
                    return { url: `http://127.0.0.1:${info.port}`, token: info.token };
                }
            } catch { continue; }
        }
    } catch { /* runtime dir may not exist */ }
    return undefined;
}

export function daemonPost<T = any>(daemon: DaemonInfo, endpoint: string, body: Record<string, any>): Promise<T> {
    return new Promise((resolve, reject) => {
        const url = new URL(endpoint, daemon.url);
        const data = JSON.stringify(body);
        const req = http.request(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Content-Length': Buffer.byteLength(data),
                'Authorization': `token ${daemon.token}`,
            },
            timeout: 15_000,
        }, (res) => {
            const chunks: Buffer[] = [];
            res.on('data', (chunk: Buffer) => chunks.push(chunk));
            res.on('end', () => {
                const raw = Buffer.concat(chunks).toString();
                try {
                    const payload = raw ? JSON.parse(raw) : {};
                    if ((res.statusCode ?? 500) >= 400) {
                        reject(new Error(payload?.error ?? `Daemon HTTP ${res.statusCode}`));
                    } else {
                        resolve(payload as T);
                    }
                } catch {
                    reject(new Error(`Daemon returned invalid JSON (${res.statusCode})`));
                }
            });
        });
        req.on('error', reject);
        req.on('timeout', () => { req.destroy(); reject(new Error('Daemon HTTP timeout')); });
        req.write(data);
        req.end();
    });
}

export class SessionAutoAttach implements vscode.Disposable {
    private heartbeat: NodeJS.Timeout | undefined;
    private session: SessionRef | undefined;
    private _discoverDaemon: (workspaceRoot: string) => DaemonInfo | undefined;

    constructor(private readonly context: vscode.ExtensionContext, daemonDiscovery?: (workspaceRoot: string) => DaemonInfo | undefined) {
        this._discoverDaemon = daemonDiscovery ?? discoverDaemon;
    }

    async attachIfEnabled(config: vscode.WorkspaceConfiguration): Promise<void> {
        if (!autoAttachEnabled(config)) {
            return;
        }
        const workspaceRoot = primaryWorkspaceRoot();
        if (!workspaceRoot) {
            return;
        }
        const storedSessionId = this.context.workspaceState.get<string>(sessionStorageKey(workspaceRoot));
        const preferredSessionId = storedSessionId ?? await this.findReusableSessionId(workspaceRoot);
        const daemon = this._discoverDaemon(workspaceRoot);
        if (!daemon) {
            // Fall back to CLI attach when daemon is not yet running
            const result = await this.runCli(
                workspaceRoot,
                [
                    'core', 'attach',
                    '--workspace-root', workspaceRoot,
                    '--actor', 'human',
                    '--client-type', 'vscode',
                    '--label', `${vscode.env.appName} window`,
                    '--capability', 'projection',
                    '--capability', 'editor',
                    '--capability', 'presence',
                    ...(preferredSessionId ? ['--session-id', preferredSessionId] : []),
                ],
            );
            const sessionId = result?.session?.session_id;
            if (typeof sessionId !== 'string' || !sessionId) {
                throw new Error('session attach returned no session_id');
            }
            this.session = { workspaceRoot, sessionId };
            await this.context.workspaceState.update(sessionStorageKey(workspaceRoot), sessionId);
            this.startHeartbeat();
            return;
        }
        const result = await daemonPost(daemon, '/api/sessions/start', {
            actor: 'human',
            client: 'vscode',
            label: `${vscode.env.appName} window`,
            capabilities: ['projection', 'editor', 'presence'],
            ...(preferredSessionId ? { session_id: preferredSessionId } : { session_id: crypto.randomUUID() }),
        });
        const sessionId = result?.session?.session_id;
        if (typeof sessionId !== 'string' || !sessionId) {
            throw new Error('session attach returned no session_id');
        }
        this.session = { workspaceRoot, sessionId };
        await this.context.workspaceState.update(sessionStorageKey(workspaceRoot), sessionId);
        this.startHeartbeat();
    }

    async detachIfAttached(): Promise<void> {
        this.stopHeartbeat();
        if (!this.session) {
            return;
        }
        const current = this.session;
        this.session = undefined;
        try {
            const daemon = this._discoverDaemon(current.workspaceRoot);
            if (daemon) {
                await daemonPost(daemon, '/api/sessions/detach', { session_id: current.sessionId });
            } else {
                await this.runCli(current.workspaceRoot, [
                    'core', 'session-detach',
                    '--workspace-root', current.workspaceRoot,
                    '--session-id', current.sessionId,
                ]);
            }
        } catch (err: any) {
            console.warn('[agent-repl] session auto-attach detach failed:', err?.message ?? String(err));
        }
    }

    dispose(): void {
        this.stopHeartbeat();
    }

    private startHeartbeat(): void {
        this.stopHeartbeat();
        this.heartbeat = setInterval(() => {
            void this.touch();
        }, HEARTBEAT_INTERVAL_MS);
    }

    private stopHeartbeat(): void {
        if (this.heartbeat) {
            clearInterval(this.heartbeat);
            this.heartbeat = undefined;
        }
    }

    private async touch(): Promise<void> {
        if (!this.session) {
            return;
        }
        try {
            const daemon = this._discoverDaemon(this.session.workspaceRoot);
            if (daemon) {
                await daemonPost(daemon, '/api/sessions/touch', { session_id: this.session.sessionId });
            } else {
                await this.runCli(this.session.workspaceRoot, [
                    'core', 'session-touch',
                    '--workspace-root', this.session.workspaceRoot,
                    '--session-id', this.session.sessionId,
                ]);
            }
        } catch (err: any) {
            console.warn('[agent-repl] session auto-attach heartbeat failed:', err?.message ?? String(err));
        }
    }

    private async findReusableSessionId(workspaceRoot: string): Promise<string | undefined> {
        try {
            const daemon = this._discoverDaemon(workspaceRoot);
            if (daemon) {
                const payload = await daemonPost(daemon, '/api/sessions/resolve', { actor: 'human' });
                return typeof payload?.session?.session_id === 'string' ? payload.session.session_id : undefined;
            }
            const payload = await this.runCli(workspaceRoot, [
                'core', 'session-resolve',
                '--workspace-root', workspaceRoot,
            ]);
            return typeof payload?.session?.session_id === 'string' ? payload.session.session_id : undefined;
        } catch {
            return undefined;
        }
    }

    private async runCli(workspaceRoot: string, args: string[]): Promise<any> {
        let lastError: Error | undefined;
        const diagnostics: string[] = [];
        for (const plan of coreCliPlans(workspaceRoot, vscode.workspace.getConfiguration('agent-repl'))) {
            try {
                const result = await execFile(plan.command, [...plan.args, ...args], {
                    cwd: plan.cwd,
                    timeout: 15_000,
                });
                return JSON.parse(result.stdout);
            } catch (err: any) {
                const detail = err?.stderr?.trim?.() || err?.message || String(err);
                diagnostics.push(`${plan.command} ${[...plan.args, ...args].join(' ')} => ${detail}`);
                lastError = err instanceof Error ? err : new Error(String(err));
            }
        }
        if (diagnostics.length > 0) {
            throw new Error(`No working agent-repl launcher found for session auto-attach. Attempts: ${diagnostics.join(' | ')}`);
        }
        throw lastError ?? new Error('No working agent-repl launcher found for session auto-attach');
    }
}

type NotebookRuntimeState = {
    status: string;
    path: string;
    active: boolean;
    mode?: string | null;
    reattach_policy?: {
        action?: string;
        reason?: string;
        selected_runtime_id?: string | null;
    } | null;
    runtime?: {
        runtime_id?: string;
        python_path?: string;
        busy?: boolean;
        kernel_generation?: number;
    } | null;
    runtime_record?: {
        runtime_id?: string;
        status?: string;
        health?: string;
        kernel_generation?: number;
    } | null;
};

type VisibleCellExecutionResult = {
    status: string;
    outputs?: Array<Record<string, any>>;
    execution_count?: number | null;
};

type ProjectVisibleNotebookResult = {
    status: string;
    path: string;
    cell_count: number;
    mode?: string | null;
};

type ProjectionCell = {
    index: number;
    cell_id?: string;
    cell_type: string;
    source: string;
    outputs?: Array<Record<string, any>>;
    execution_count?: number | null;
    metadata?: Record<string, any>;
};

type NotebookProjectionState = {
    status: string;
    path: string;
    active: boolean;
    mode?: string | null;
    runtime?: {
        busy?: boolean;
        current_execution?: {
            cell_id?: string;
            cell_index?: number;
        } | null;
        python_path?: string;
    } | null;
    contents?: {
        path: string;
        cells: ProjectionCell[];
    } | null;
};

type NotebookActivityState = {
    status: string;
    path: string;
    cursor?: number;
    recent_events?: NotebookActivityEvent[];
};

type NotebookActivityEvent = {
    event_id?: string;
    type?: string;
    path?: string;
    detail?: string;
    actor?: string | null;
    session_id?: string | null;
    runtime_id?: string | null;
    cell_id?: string | null;
    cell_index?: number | null;
    timestamp?: number;
    data?: {
        cell?: ProjectionCell;
        output?: Record<string, any>;
        execution_count?: number | null;
        cell_id?: string;
    } | null;
};

type ProjectionExecution = {
    cellId?: string;
    cellIndex: number;
    execution: vscode.NotebookCellExecution;
    outputs: vscode.NotebookCellOutput[];
};

type TrackedProjection = {
    notebook: vscode.NotebookDocument;
    lastAppliedSignature?: string;
    activeExecution?: ProjectionExecution;
    lastActivityCursor?: number;
};

export class HeadlessNotebookProjection implements vscode.Disposable {
    private readonly controller: vscode.NotebookController;
    private readonly disposables: vscode.Disposable[] = [];
    private readonly attaching = new Set<string>();
    private readonly tracked = new Map<string, TrackedProjection>();
    private readonly userClosed = new Set<string>();
    private daemonWs: DaemonWebSocket | null = null;
    private wsSubscriptions = new Set<string>();
    private readonly _discoverDaemon: (workspaceRoot: string) => DaemonInfo | undefined;

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly extensionId: string,
        daemonDiscovery?: (workspaceRoot: string) => DaemonInfo | undefined,
    ) {
        this._discoverDaemon = daemonDiscovery ?? discoverDaemon;
        this.controller = vscode.notebooks.createNotebookController(
            PROJECTION_CONTROLLER_ID,
            'jupyter-notebook',
            'Agent REPL Runtime',
        );
        this.controller.supportedLanguages = ['python'];
        this.controller.description = 'Shared runtime projection';
        this.controller.executeHandler = async (cells, notebook) => {
            await this.executeCells(cells, notebook);
        };
        this.disposables.push(this.controller);
        this.disposables.push(
            this.controller.onDidChangeSelectedNotebooks(({ notebook, selected }) => {
                if (selected) {
                    this.trackNotebook(notebook);
                    return;
                }
                this.untrackNotebook(notebook.uri.fsPath);
            }),
        );
        this.disposables.push(
            vscode.workspace.onDidOpenNotebookDocument((notebook) => {
                if (this.userClosed.has(notebook.uri.fsPath)) {
                    this.userClosed.delete(notebook.uri.fsPath);
                }
                logNotebookDiagnostic(notebook.uri.fsPath, 'workspace.onDidOpenNotebookDocument', {
                    notebookType: notebook.notebookType,
                    dirty: notebook.isDirty,
                    cellCount: notebook.cellCount,
                });
                void this.attachNotebookIfRunning(notebook);
            }),
        );
        this.disposables.push(
            vscode.workspace.onDidCloseNotebookDocument((notebook) => {
                this.userClosed.add(notebook.uri.fsPath);
                logNotebookDiagnostic(notebook.uri.fsPath, 'workspace.onDidCloseNotebookDocument', {
                    notebookType: notebook.notebookType,
                    dirty: notebook.isDirty,
                    cellCount: notebook.cellCount,
                });
                this.untrackNotebook(notebook.uri.fsPath);
            }),
        );
        this.disposables.push(
            vscode.window.onDidChangeVisibleNotebookEditors((editors) => {
                const visiblePaths = editors.map((editor) => editor.notebook.uri.fsPath);
                for (const editor of editors) {
                    logNotebookDiagnostic(editor.notebook.uri.fsPath, 'window.onDidChangeVisibleNotebookEditors', {
                        visible: true,
                        dirty: editor.notebook.isDirty,
                        cellCount: editor.notebook.cellCount,
                        visibleNotebookCount: editors.length,
                        visibleNotebookPaths: visiblePaths,
                    });
                }
                for (const editor of editors) {
                    if (!this.userClosed.has(editor.notebook.uri.fsPath)) {
                        void this.attachNotebookIfRunning(editor.notebook, editor);
                    }
                }
            }),
        );
        this.startWs();
    }

    dispose(): void {
        this.stopWs();
        for (const tracked of this.tracked.values()) {
            void this.clearNotebookPresence(tracked.notebook);
            tracked.activeExecution?.execution.end(false, Date.now());
        }
        this.tracked.clear();
        for (const disposable of this.disposables) {
            disposable.dispose();
        }
        this.disposables.length = 0;
    }

    async attachNotebookIfRunning(
        notebook: vscode.NotebookDocument,
        editor?: vscode.NotebookEditor,
    ): Promise<boolean> {
        if (notebook.notebookType !== 'jupyter-notebook') {
            return false;
        }
        const workspaceRoot = workspaceRootForPath(notebook.uri.fsPath);
        if (!workspaceRoot) {
            return false;
        }
        const key = notebook.uri.fsPath;
        if (this.attaching.has(key)) {
            return false;
        }
        const config = vscode.workspace.getConfiguration('agent-repl');
        if (!autoAttachEnabled(config)) {
            return false;
        }
        this.attaching.add(key);
        try {
            const state = await runCliJson<NotebookRuntimeState>(workspaceRoot, config, [
                'core', 'notebook-runtime',
                '--workspace-root', workspaceRoot,
                notebook.uri.fsPath,
            ]);
            const action = state.reattach_policy?.action ?? 'none';
            const shouldAttach = state.mode === 'headless' && (
                state.active ||
                action === 'resume-runtime' ||
                action === 'create-runtime' ||
                action === 'attach-with-warning' ||
                action === 'observe-or-queue'
            );
            logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.attachNotebookIfRunning', {
                active: state.active,
                mode: state.mode ?? null,
                reattachAction: action,
                shouldAttach,
                runtimeBusy: state.runtime?.busy ?? null,
                selectedRuntimeId: state.reattach_policy?.selected_runtime_id ?? null,
            });
            if (!shouldAttach) {
                return false;
            }
            this.controller.updateNotebookAffinity(notebook, vscode.NotebookControllerAffinity.Preferred);
            const targetEditor = editor ?? vscode.window.visibleNotebookEditors.find((candidate) => candidate.notebook === notebook);
            if (targetEditor) {
                await vscode.commands.executeCommand('notebook.selectKernel', {
                    notebookEditor: targetEditor,
                    id: this.controller.id,
                    extension: this.extensionId,
                });
            }
            this.trackNotebook(notebook);
            if (state.active) {
                await this.syncNotebookProjection(notebook);
            }
            return true;
        } finally {
            this.attaching.delete(key);
        }
    }

    async syncNotebookProjection(notebook: vscode.NotebookDocument): Promise<boolean> {
        const tracked = this.trackNotebook(notebook);
        const workspaceRoot = workspaceRootForPath(notebook.uri.fsPath);
        if (!workspaceRoot) {
            return false;
        }
        const config = vscode.workspace.getConfiguration('agent-repl');
        const state = await runCliJson<NotebookProjectionState>(workspaceRoot, config, [
            'core', 'notebook-projection',
            '--workspace-root', workspaceRoot,
            notebook.uri.fsPath,
        ]);
        logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.syncNotebookProjection.state', {
            active: state.active,
            mode: state.mode ?? null,
            notebookDirty: notebook.isDirty,
            notebookCellCount: notebook.cellCount,
            projectionCellCount: state.contents?.cells?.length ?? null,
            runtimeBusy: state.runtime?.busy ?? null,
            currentExecutionCellId: state.runtime?.current_execution?.cell_id ?? null,
        });
        if (!state.active || state.mode !== 'headless' || !state.contents) {
            this.finishTrackedExecution(tracked, undefined);
            return false;
        }

        this.syncTrackedExecution(tracked, state);
        const signature = projectionSignature(state.contents.cells);

        // Never replay runtime-owned snapshots over a dirty notebook. The local
        // editor may be mid-edit or closing, and forcing replaceCells() here
        // causes the visible cell churn and save/close races we're trying to avoid.
        if (notebook.isDirty) {
            logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.syncNotebookProjection.skipDirty', {
                signatureLength: signature.length,
            });
            return false;
        }

        if (tracked.lastAppliedSignature === signature) {
            logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.syncNotebookProjection.skipSignatureMatch', {});
            return false;
        }

        logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.syncNotebookProjection.applySnapshot', {
            previousSignatureLength: tracked.lastAppliedSignature?.length ?? 0,
            nextSignatureLength: signature.length,
            projectionCellCount: state.contents.cells.length,
        });
        await applyProjectionSnapshot(notebook, state.contents.cells);
        tracked.lastAppliedSignature = signature;
        return true;
    }

    private async executeCells(cells: readonly vscode.NotebookCell[], notebook: vscode.NotebookDocument): Promise<void> {
        const targetCells = this.executionTargets(cells, notebook);
        const workspaceRoot = workspaceRootForPath(notebook.uri.fsPath);
        if (!workspaceRoot) {
            throw new Error(`No workspace root matched '${notebook.uri.fsPath}'`);
        }
        const config = vscode.workspace.getConfiguration('agent-repl');
        await this.projectVisibleNotebook(workspaceRoot, config, notebook);
        const daemon = this._discoverDaemon(workspaceRoot);
        if (!daemon) {
            throw new Error('Daemon not available for execution');
        }
        const notebookPath = path.relative(workspaceRoot, notebook.uri.fsPath);
        const sessionId = this.sessionIdForWorkspace(workspaceRoot);
        for (const cell of targetCells) {
            if (cell.kind !== vscode.NotebookCellKind.Code) {
                continue;
            }
            const execution = this.controller.createNotebookCellExecution(cell);
            execution.start(Date.now());
            try {
                const result = await daemonPost<VisibleCellExecutionResult>(daemon, '/api/notebooks/execute-cell', {
                    path: notebookPath,
                    cell_index: cell.index,
                    wait: true,
                    ...(sessionId ? { owner_session_id: sessionId } : {}),
                });
                if (typeof result.execution_count === 'number') {
                    execution.executionOrder = result.execution_count;
                }
                await execution.replaceOutput(toNotebookOutputs(result.outputs ?? []));
                execution.end(result.status !== 'error', Date.now());
            } catch (err: any) {
                await execution.replaceOutput([
                    new vscode.NotebookCellOutput([
                        vscode.NotebookCellOutputItem.error(err instanceof Error ? err : new Error(String(err))),
                    ]),
                ]);
                execution.end(false, Date.now());
            }
        }
        await notebook.save();
    }

    private executionTargets(
        cells: readonly vscode.NotebookCell[],
        notebook: vscode.NotebookDocument,
    ): readonly vscode.NotebookCell[] {
        if (cells.length > 0) {
            return cells;
        }
        const editor = this.editorForNotebook(notebook);
        if (!editor) {
            return cells;
        }
        const selectedIndexes = new Set<number>();
        for (const range of editor.selections ?? []) {
            const start = Math.max(0, range.start);
            const end = Math.min(notebook.cellCount, range.end);
            for (let index = start; index < end; index += 1) {
                selectedIndexes.add(index);
            }
        }
        if (selectedIndexes.size === 0) {
            return cells;
        }
        return [...selectedIndexes]
            .sort((left, right) => left - right)
            .map((index) => notebook.cellAt(index));
    }

    private editorForNotebook(notebook: vscode.NotebookDocument): vscode.NotebookEditor | undefined {
        const activeEditor = vscode.window.activeNotebookEditor;
        if (activeEditor?.notebook === notebook) {
            return activeEditor;
        }
        return vscode.window.visibleNotebookEditors.find((candidate) => candidate.notebook === notebook);
    }

    private async projectVisibleNotebook(
        workspaceRoot: string,
        config: vscode.WorkspaceConfiguration,
        notebook: vscode.NotebookDocument,
    ): Promise<void> {
        const sessionId = this.sessionIdForWorkspace(workspaceRoot);
        const notebookCells = typeof notebook.getCells === 'function'
            ? notebook.getCells()
            : Array.from({ length: notebook.cellCount }, (_unused, index) => notebook.cellAt(index));
        const projection = notebookCells.map((cell) => ({
            cell_type: cell.kind === vscode.NotebookCellKind.Code ? 'code' : 'markdown',
            source: cell.document.getText(),
            cell_id: cell.metadata?.custom?.['agent-repl']?.cell_id,
            metadata: cell.metadata ?? {},
            outputs: toJupyter(cell),
            execution_count: cell.executionSummary?.executionOrder ?? null,
        }));
        const tempFile = path.join(
            os.tmpdir(),
            `agent-repl-visible-${Date.now()}-${Math.random().toString(36).slice(2)}.json`,
        );
        await fs.promises.writeFile(tempFile, JSON.stringify(projection), 'utf8');
        try {
            await runCliJson<ProjectVisibleNotebookResult>(workspaceRoot, config, [
                'core', 'project-visible-notebook',
                '--workspace-root', workspaceRoot,
                ...(sessionId ? ['--session-id', sessionId] : []),
                notebook.uri.fsPath,
                '--cells-file', tempFile,
            ]);
        } finally {
            void fs.promises.unlink(tempFile).catch(() => undefined);
        }
    }

    private trackNotebook(notebook: vscode.NotebookDocument): TrackedProjection {
        const existing = this.tracked.get(notebook.uri.fsPath);
        if (existing) {
            existing.notebook = notebook;
            return existing;
        }
        const tracked: TrackedProjection = { notebook };
        this.tracked.set(notebook.uri.fsPath, tracked);
        this.wsSubscribeNotebook(notebook);
        return tracked;
    }

    private untrackNotebook(fsPath: string): void {
        const tracked = this.tracked.get(fsPath);
        if (!tracked) {
            return;
        }
        void this.clearNotebookPresence(tracked.notebook);
        this.finishTrackedExecution(tracked, undefined);
        this.tracked.delete(fsPath);
        // Unsubscribe from WS push for this notebook.
        const workspaceRoot = workspaceRootForPath(fsPath);
        if (workspaceRoot) {
            const relative = path.relative(workspaceRoot, fsPath);
            if (this.wsSubscriptions.has(relative)) {
                this.wsSubscriptions.delete(relative);
                this.daemonWs?.unsubscribe(relative);
            }
        }
    }

    // -- WebSocket push (replaces timer-based HTTP polling) --------------------

    private startWs(): void {
        if (this.daemonWs) return;
        const workspaceRoot = primaryWorkspaceRoot();
        if (!workspaceRoot) return;
        const daemon = this._discoverDaemon(workspaceRoot);
        if (!daemon) return;

        this.daemonWs = new DaemonWebSocket({
            daemonUrl: daemon.url,
            daemonToken: daemon.token,
            createSocket: (url: string): SocketLike => {
                if (typeof (globalThis as any).WebSocket === 'function') {
                    return new (globalThis as any).WebSocket(url);
                }
                try {
                    const WSLib = require('ws');
                    return new WSLib(url);
                } catch { /* not available */ }
                throw new Error('No WebSocket implementation available');
            },
            fetchFn: globalThis.fetch ?? (async (input: any, init?: any) => {
                // Minimal Node fetch shim via http module for nonce request.
                const url = new URL(typeof input === 'string' ? input : input.url);
                return new Promise<Response>((resolve, reject) => {
                    const req = http.request(url, {
                        method: init?.method ?? 'GET',
                        headers: init?.headers as Record<string, string>,
                    }, (res) => {
                        const chunks: Buffer[] = [];
                        res.on('data', (chunk: Buffer) => chunks.push(chunk));
                        res.on('end', () => {
                            const body = Buffer.concat(chunks).toString();
                            resolve({
                                ok: (res.statusCode ?? 500) < 400,
                                status: res.statusCode ?? 500,
                                json: async () => JSON.parse(body),
                            } as Response);
                        });
                    });
                    req.on('error', reject);
                    req.end();
                });
            }),
            onMessage: (msg) => this.handleWsMessage(msg),
            onConnect: () => {
                // Re-subscribe all tracked notebooks.
                for (const relativePath of this.wsSubscriptions) {
                    this.daemonWs?.subscribe(relativePath);
                }
            },
            onDisconnect: () => { /* reconnect is automatic */ },
            onInstanceChange: () => {
                // Daemon restarted — full resync for all tracked notebooks.
                for (const tracked of this.tracked.values()) {
                    void this.syncNotebookProjection(tracked.notebook).catch((err: any) => {
                        console.warn('[agent-repl] notebook projection resync failed:', err?.message ?? String(err));
                    });
                }
            },
        });
        this.daemonWs.connect();
    }

    private stopWs(): void {
        if (this.daemonWs) {
            this.daemonWs.close();
            this.daemonWs = null;
        }
        this.wsSubscriptions.clear();
    }

    private wsSubscribeNotebook(notebook: vscode.NotebookDocument): void {
        const workspaceRoot = workspaceRootForPath(notebook.uri.fsPath);
        if (!workspaceRoot) return;
        const relative = path.relative(workspaceRoot, notebook.uri.fsPath);
        if (this.wsSubscriptions.has(relative)) return;
        this.wsSubscriptions.add(relative);
        this.daemonWs?.subscribe(relative);
    }

    private handleWsMessage(msg: any): void {
        // Route to the tracked notebook by path.
        const eventPath: string | undefined = msg.path ?? msg.data?.path;
        if (!eventPath) return;
        let tracked: TrackedProjection | undefined;
        for (const [fsPath, t] of this.tracked) {
            const workspaceRoot = workspaceRootForPath(fsPath);
            if (workspaceRoot && path.relative(workspaceRoot, fsPath) === eventPath) {
                tracked = t;
                break;
            }
        }
        if (!tracked) return;

        // Wrap into activity event array and process.
        const events: NotebookActivityEvent[] = [msg];
        void (async () => {
            try {
                const applyResult = await applyIncrementalActivityEvents(tracked!, events);
                for (const event of events) {
                    pushActivityEvent(event);
                }
                if (typeof msg.cursor === 'number') {
                    tracked!.lastActivityCursor = msg.cursor;
                }
                // If incremental apply says we need a snapshot, do a full sync.
                if (applyResult.needsSnapshot) {
                    await this.syncNotebookProjection(tracked!.notebook);
                }
            } catch (err: any) {
                console.warn('[agent-repl] WS event handling failed:', err?.message ?? String(err));
            }
        })();
    }

    private async clearNotebookPresence(notebook: vscode.NotebookDocument): Promise<void> {
        const workspaceRoot = workspaceRootForPath(notebook.uri.fsPath);
        if (!workspaceRoot) {
            return;
        }
        const sessionId = this.sessionIdForWorkspace(workspaceRoot);
        if (!sessionId) {
            return;
        }
        try {
            await runCliJson(workspaceRoot, vscode.workspace.getConfiguration('agent-repl'), [
                'core', 'session-presence-clear',
                '--workspace-root', workspaceRoot,
                '--session-id', sessionId,
                '--path', notebook.uri.fsPath,
            ]);
        } catch (err: any) {
            console.warn('[agent-repl] notebook presence clear failed:', err?.message ?? String(err));
        }
    }

    private sessionIdForWorkspace(workspaceRoot: string): string | undefined {
        return sessionIdForWorkspaceState(this.context, workspaceRoot);
    }

    private syncTrackedExecution(tracked: TrackedProjection, state: NotebookProjectionState): void {
        const current = state.runtime?.current_execution;
        const busy = Boolean(state.runtime?.busy && current && typeof current.cell_index === 'number');
        if (!busy || !current || typeof current.cell_index !== 'number') {
            this.finishTrackedExecution(tracked, state);
            return;
        }
        const matchesExisting = tracked.activeExecution &&
            tracked.activeExecution.cellIndex === current.cell_index &&
            tracked.activeExecution.cellId === current.cell_id;
        if (matchesExisting) {
            return;
        }
        this.finishTrackedExecution(tracked, undefined);
        if (current.cell_index < 0 || current.cell_index >= tracked.notebook.cellCount) {
            return;
        }
        const execution = this.controller.createNotebookCellExecution(tracked.notebook.cellAt(current.cell_index));
        execution.start(Date.now());
        tracked.activeExecution = {
            cellId: current.cell_id,
            cellIndex: current.cell_index,
            execution,
            outputs: [],
        };
    }

    private finishTrackedExecution(tracked: TrackedProjection, state: NotebookProjectionState | undefined): void {
        const active = tracked.activeExecution;
        if (!active) {
            return;
        }
        const snapshotCell = state?.contents?.cells?.[active.cellIndex];
        if (snapshotCell) {
            if (typeof snapshotCell.execution_count === 'number') {
                active.execution.executionOrder = snapshotCell.execution_count;
            }
            void active.execution.replaceOutput(toNotebookOutputs(snapshotCell.outputs ?? []));
            active.execution.end(!hasErrorOutput(snapshotCell.outputs ?? []), Date.now());
        } else {
            active.execution.end(false, Date.now());
        }
        tracked.activeExecution = undefined;
    }
}

async function applyProjectionSnapshot(
    notebook: vscode.NotebookDocument,
    cells: ProjectionCell[],
): Promise<void> {
    await replaceProjectionCells(notebook, 0, notebook.cellCount, cells);
}

export async function applyIncrementalActivityEvents(
    tracked: TrackedProjection,
    events: NotebookActivityEvent[],
): Promise<{ changed: boolean; needsSnapshot: boolean }> {
    const notebook = tracked.notebook;
    if (notebook.isDirty) {
        logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.applyIncrementalActivityEvents.skipDirty', {
            eventTypes: events.map((event) => event.type ?? 'unknown'),
        });
        return { changed: false, needsSnapshot: true };
    }
    let documentChanged = false;
    let needsSnapshot = false;
    for (const event of events) {
        const type = event.type ?? '';
        if (type === 'notebook-reset-needed' || type === 'notebook-projected') {
            needsSnapshot = true;
            continue;
        }
        if (type === 'cell-execution-updated') {
            if (tracked.activeExecution && event.cell_id && tracked.activeExecution.cellId === event.cell_id) {
                const executionCount = event.data?.execution_count;
                if (typeof executionCount === 'number') {
                    tracked.activeExecution.execution.executionOrder = executionCount;
                }
            }
            continue;
        }
        if (type === 'cell-output-appended') {
            if (tracked.activeExecution && event.cell_id && tracked.activeExecution.cellId === event.cell_id && event.data?.output) {
                tracked.activeExecution.outputs.push(toVSCode(event.data.output as any));
                await tracked.activeExecution.execution.replaceOutput(tracked.activeExecution.outputs);
                if (event.data.cell) {
                    const replaced = await upsertProjectionCell(notebook, event.data.cell, true);
                    documentChanged = documentChanged || replaced;
                }
                continue;
            }
            if (event.data?.cell) {
                const replaced = await upsertProjectionCell(notebook, event.data.cell, false);
                documentChanged = documentChanged || replaced;
                continue;
            }
            needsSnapshot = true;
            continue;
        }
        if (type === 'cell-inserted') {
            if (!event.data?.cell) {
                needsSnapshot = true;
                continue;
            }
            const insertIndex = normalizeCellIndex(event.data.cell.index, notebook.cellCount);
            await replaceProjectionCells(notebook, insertIndex, 0, [event.data.cell]);
            shiftActiveExecutionForInsert(tracked, insertIndex);
            documentChanged = true;
            continue;
        }
        if (type === 'cell-removed') {
            const targetIndex = findNotebookCellIndex(notebook, event.cell_id ?? undefined, event.cell_index ?? undefined);
            if (targetIndex < 0) {
                needsSnapshot = true;
                continue;
            }
            await replaceProjectionCells(notebook, targetIndex, 1, []);
            shiftActiveExecutionForDelete(tracked, targetIndex);
            documentChanged = true;
            continue;
        }
        if (type === 'cell-source-updated' || type === 'cell-outputs-updated' || type === 'cell-updated') {
            if (!event.data?.cell) {
                needsSnapshot = true;
                continue;
            }
            const replaced = await upsertProjectionCell(notebook, event.data.cell, true);
            if (!replaced) {
                needsSnapshot = true;
                continue;
            }
            documentChanged = true;
        }
    }
    if (documentChanged) {
        // No save — the headless runtime owns the disk file.
    }
    logNotebookDiagnostic(notebook.uri.fsPath, 'HeadlessNotebookProjection.applyIncrementalActivityEvents.result', {
        eventTypes: events.map((event) => event.type ?? 'unknown'),
        documentChanged,
        needsSnapshot,
        cellCount: notebook.cellCount,
    });
    return { changed: documentChanged, needsSnapshot };
}

async function upsertProjectionCell(
    notebook: vscode.NotebookDocument,
    cell: ProjectionCell,
    replaceExisting: boolean,
): Promise<boolean> {
    const existingIndex = findNotebookCellIndex(notebook, cell.cell_id, cell.index);
    if (existingIndex >= 0) {
        await replaceProjectionCells(notebook, existingIndex, 1, [cell]);
        return true;
    }
    if (!replaceExisting) {
        const insertIndex = normalizeCellIndex(cell.index, notebook.cellCount);
        await replaceProjectionCells(notebook, insertIndex, 0, [cell]);
        return true;
    }
    return false;
}

async function replaceProjectionCells(
    notebook: vscode.NotebookDocument,
    start: number,
    deleteCount: number,
    cells: ProjectionCell[],
): Promise<void> {
    const edit = new vscode.WorkspaceEdit();
    edit.set(notebook.uri, [
        vscode.NotebookEdit.replaceCells(
            new vscode.NotebookRange(start, start + deleteCount),
            cells.map(toNotebookCellData),
        ),
    ]);
    await vscode.workspace.applyEdit(edit);
}

function toNotebookCellData(cell: ProjectionCell): vscode.NotebookCellData {
    const kind = cell.cell_type === 'code' ? vscode.NotebookCellKind.Code : vscode.NotebookCellKind.Markup;
    const languageId = cell.cell_type === 'code' ? 'python' : 'markdown';
    const cellData = new vscode.NotebookCellData(kind, cell.source ?? '', languageId);
    cellData.metadata = cell.metadata ?? {};
    cellData.outputs = toNotebookOutputs(cell.outputs ?? []);
    return cellData;
}

function projectionSignature(cells: ProjectionCell[]): string {
    return JSON.stringify(cells);
}

function cellIdForNotebookCell(cell: vscode.NotebookCell | any): string | undefined {
    return cell?.metadata?.custom?.['agent-repl']?.cell_id;
}

function findNotebookCellIndex(
    notebook: vscode.NotebookDocument,
    cellId?: string,
    fallbackIndex?: number | null,
): number {
    const notebookCells = typeof notebook.getCells === 'function'
        ? notebook.getCells()
        : Array.from({ length: notebook.cellCount }, (_unused, index) => notebook.cellAt(index));
    if (cellId) {
        const matchIndex = notebookCells.findIndex((cell) => cellIdForNotebookCell(cell) === cellId);
        if (matchIndex >= 0) {
            return matchIndex;
        }
    }
    if (typeof fallbackIndex === 'number' && fallbackIndex >= 0 && fallbackIndex < notebook.cellCount) {
        return fallbackIndex;
    }
    return -1;
}

function normalizeCellIndex(index: number | undefined, length: number): number {
    if (typeof index !== 'number' || Number.isNaN(index)) {
        return length;
    }
    return Math.max(0, Math.min(index, length));
}

function shiftActiveExecutionForInsert(tracked: TrackedProjection, insertedIndex: number): void {
    if (!tracked.activeExecution) {
        return;
    }
    if (tracked.activeExecution.cellIndex >= insertedIndex) {
        tracked.activeExecution.cellIndex += 1;
    }
}

function shiftActiveExecutionForDelete(tracked: TrackedProjection, removedIndex: number): void {
    if (!tracked.activeExecution) {
        return;
    }
    if (tracked.activeExecution.cellIndex === removedIndex) {
        tracked.activeExecution.execution.end(false, Date.now());
        tracked.activeExecution = undefined;
        return;
    }
    if (tracked.activeExecution.cellIndex > removedIndex) {
        tracked.activeExecution.cellIndex -= 1;
    }
}

function toNotebookOutputs(outputs: Array<Record<string, any>>): vscode.NotebookCellOutput[] {
    return outputs.map((output) => toVSCode(output as any));
}

function hasErrorOutput(outputs: Array<Record<string, any>>): boolean {
    return outputs.some((output) => output.output_type === 'error');
}

async function runCliJson<T>(workspaceRoot: string, config: vscode.WorkspaceConfiguration, args: string[]): Promise<T> {
    let lastError: Error | undefined;
    const diagnostics: string[] = [];
    for (const plan of coreCliPlans(workspaceRoot, config)) {
        try {
            const result = await execFile(plan.command, [...plan.args, ...args], {
                cwd: plan.cwd,
                timeout: 15_000,
            });
            return JSON.parse(result.stdout) as T;
        } catch (err: any) {
            const detail = err?.stderr?.trim?.() || err?.message || String(err);
            diagnostics.push(`${plan.command} ${[...plan.args, ...args].join(' ')} => ${detail}`);
            lastError = err instanceof Error ? err : new Error(String(err));
        }
    }
    if (diagnostics.length > 0) {
        throw new Error(`No working agent-repl launcher found. Attempts: ${diagnostics.join(' | ')}`);
    }
    throw lastError ?? new Error('No working agent-repl launcher found');
}

function sessionStorageKey(workspaceRoot: string): string {
    return `agent-repl.session:${workspaceRoot}`;
}

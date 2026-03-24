import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as util from 'util';
import * as vscode from 'vscode';
import { toVSCode } from './notebook/outputs';

const execFile = util.promisify(childProcess.execFile);
const HEARTBEAT_INTERVAL_MS = 30_000;
const PROJECTION_SYNC_INTERVAL_MS = 1_000;
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
    return config.get<boolean>('sessionAutoAttach', config.get<boolean>('v2AutoAttach', true));
}

export function v2CliPlans(workspaceRoot: string, config: vscode.WorkspaceConfiguration): CliPlan[] {
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

export class V2AutoAttach implements vscode.Disposable {
    private heartbeat: NodeJS.Timeout | undefined;
    private session: SessionRef | undefined;

    constructor(private readonly context: vscode.ExtensionContext) {}

    async attachIfEnabled(config: vscode.WorkspaceConfiguration): Promise<void> {
        if (!autoAttachEnabled(config)) {
            return;
        }
        const workspaceRoot = primaryWorkspaceRoot();
        if (!workspaceRoot) {
            return;
        }
        const storedSessionId =
            this.context.workspaceState.get<string>(sessionStorageKey(workspaceRoot)) ??
            this.context.workspaceState.get<string>(legacySessionStorageKey(workspaceRoot));
        const result = await this.runCli(
            workspaceRoot,
            [
                'v2', 'attach',
                '--workspace-root', workspaceRoot,
                '--actor', 'human',
                '--client-type', 'vscode',
                '--label', `${vscode.env.appName} window`,
                '--capability', 'projection',
                '--capability', 'editor',
                '--capability', 'presence',
                ...(storedSessionId ? ['--session-id', storedSessionId] : []),
            ],
        );
        const sessionId = result?.session?.session_id;
        if (typeof sessionId !== 'string' || !sessionId) {
            throw new Error('session attach returned no session_id');
        }
        this.session = { workspaceRoot, sessionId };
        await this.context.workspaceState.update(sessionStorageKey(workspaceRoot), sessionId);
        await this.context.workspaceState.update(legacySessionStorageKey(workspaceRoot), undefined);
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
            await this.runCli(current.workspaceRoot, [
                'v2', 'session-detach',
                '--workspace-root', current.workspaceRoot,
                '--session-id', current.sessionId,
            ]);
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
            await this.runCli(this.session.workspaceRoot, [
                'v2', 'session-touch',
                '--workspace-root', this.session.workspaceRoot,
                '--session-id', this.session.sessionId,
            ]);
        } catch (err: any) {
            console.warn('[agent-repl] session auto-attach heartbeat failed:', err?.message ?? String(err));
        }
    }

    private async runCli(workspaceRoot: string, args: string[]): Promise<any> {
        let lastError: Error | undefined;
        const diagnostics: string[] = [];
        for (const plan of v2CliPlans(workspaceRoot, vscode.workspace.getConfiguration('agent-repl'))) {
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
    runtime?: {
        python_path?: string;
        busy?: boolean;
    } | null;
};

type VisibleCellExecutionResult = {
    status: string;
    outputs?: Array<Record<string, any>>;
    execution_count?: number | null;
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

type ProjectionExecution = {
    cellId?: string;
    cellIndex: number;
    execution: vscode.NotebookCellExecution;
};

type TrackedProjection = {
    notebook: vscode.NotebookDocument;
    lastAppliedSignature?: string;
    activeExecution?: ProjectionExecution;
};

export class HeadlessNotebookProjection implements vscode.Disposable {
    private readonly controller: vscode.NotebookController;
    private readonly disposables: vscode.Disposable[] = [];
    private readonly attaching = new Set<string>();
    private readonly tracked = new Map<string, TrackedProjection>();
    private syncTimer: NodeJS.Timeout | undefined;

    constructor(
        private readonly context: vscode.ExtensionContext,
        private readonly extensionId: string,
    ) {
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
                void this.attachNotebookIfRunning(notebook);
            }),
        );
        this.disposables.push(
            vscode.workspace.onDidCloseNotebookDocument((notebook) => {
                this.untrackNotebook(notebook.uri.fsPath);
            }),
        );
        this.disposables.push(
            vscode.window.onDidChangeVisibleNotebookEditors((editors) => {
                for (const editor of editors) {
                    void this.attachNotebookIfRunning(editor.notebook, editor);
                }
            }),
        );
        this.syncTimer = setInterval(() => {
            void this.syncTrackedNotebooks();
        }, PROJECTION_SYNC_INTERVAL_MS);
    }

    dispose(): void {
        if (this.syncTimer) {
            clearInterval(this.syncTimer);
            this.syncTimer = undefined;
        }
        for (const tracked of this.tracked.values()) {
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
                'v2', 'notebook-runtime',
                '--workspace-root', workspaceRoot,
                notebook.uri.fsPath,
            ]);
            if (!state.active || state.mode !== 'headless') {
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
            await this.syncNotebookProjection(notebook);
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
            'v2', 'notebook-projection',
            '--workspace-root', workspaceRoot,
            notebook.uri.fsPath,
        ]);
        if (!state.active || state.mode !== 'headless' || !state.contents) {
            this.finishTrackedExecution(tracked, undefined);
            return false;
        }

        let changed = false;
        const signature = JSON.stringify(state.contents.cells);
        if (!notebook.isDirty && tracked.lastAppliedSignature !== signature) {
            await applyProjectionSnapshot(notebook, state.contents.cells);
            tracked.lastAppliedSignature = signature;
            changed = true;
        }

        this.syncTrackedExecution(tracked, state);
        return changed;
    }

    private async executeCells(cells: readonly vscode.NotebookCell[], notebook: vscode.NotebookDocument): Promise<void> {
        const workspaceRoot = workspaceRootForPath(notebook.uri.fsPath);
        if (!workspaceRoot) {
            throw new Error(`No workspace root matched '${notebook.uri.fsPath}'`);
        }
        const config = vscode.workspace.getConfiguration('agent-repl');
        for (const cell of cells) {
            if (cell.kind !== vscode.NotebookCellKind.Code) {
                continue;
            }
            const execution = this.controller.createNotebookCellExecution(cell);
            execution.start(Date.now());
            try {
                const result = await runCliJson<VisibleCellExecutionResult>(workspaceRoot, config, [
                    'v2', 'execute-visible-cell',
                    '--workspace-root', workspaceRoot,
                    notebook.uri.fsPath,
                    '--cell-index', String(cell.index),
                    '--source', cell.document.getText(),
                ]);
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

    private trackNotebook(notebook: vscode.NotebookDocument): TrackedProjection {
        const existing = this.tracked.get(notebook.uri.fsPath);
        if (existing) {
            existing.notebook = notebook;
            return existing;
        }
        const tracked: TrackedProjection = { notebook };
        this.tracked.set(notebook.uri.fsPath, tracked);
        return tracked;
    }

    private untrackNotebook(fsPath: string): void {
        const tracked = this.tracked.get(fsPath);
        if (!tracked) {
            return;
        }
        this.finishTrackedExecution(tracked, undefined);
        this.tracked.delete(fsPath);
    }

    private async syncTrackedNotebooks(): Promise<void> {
        for (const tracked of this.tracked.values()) {
            try {
                await this.syncNotebookProjection(tracked.notebook);
            } catch (err: any) {
                console.warn('[agent-repl] notebook projection sync failed:', err?.message ?? String(err));
            }
        }
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
    const edit = new vscode.WorkspaceEdit();
    edit.set(notebook.uri, [
        vscode.NotebookEdit.replaceCells(
            new vscode.NotebookRange(0, notebook.cellCount),
            cells.map((cell) => {
                const kind = cell.cell_type === 'code' ? vscode.NotebookCellKind.Code : vscode.NotebookCellKind.Markup;
                const languageId = cell.cell_type === 'code' ? 'python' : 'markdown';
                const cellData = new vscode.NotebookCellData(kind, cell.source ?? '', languageId);
                cellData.metadata = cell.metadata ?? {};
                cellData.outputs = toNotebookOutputs(cell.outputs ?? []);
                return cellData;
            }),
        ),
    ]);
    await vscode.workspace.applyEdit(edit);
    await notebook.save();
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
    for (const plan of v2CliPlans(workspaceRoot, config)) {
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

function legacySessionStorageKey(workspaceRoot: string): string {
    return `agent-repl.v2.session:${workspaceRoot}`;
}

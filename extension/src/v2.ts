import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as util from 'util';
import * as vscode from 'vscode';

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

export class HeadlessNotebookProjection implements vscode.Disposable {
    private readonly controller: vscode.NotebookController;
    private readonly disposables: vscode.Disposable[] = [];
    private readonly attaching = new Set<string>();

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
            vscode.workspace.onDidOpenNotebookDocument((notebook) => {
                void this.attachNotebookIfRunning(notebook);
            }),
        );
        this.disposables.push(
            vscode.window.onDidChangeVisibleNotebookEditors((editors) => {
                for (const editor of editors) {
                    void this.attachNotebookIfRunning(editor.notebook, editor);
                }
            }),
        );
    }

    dispose(): void {
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
            return true;
        } finally {
            this.attaching.delete(key);
        }
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
}

function toNotebookOutputs(outputs: Array<Record<string, any>>): vscode.NotebookCellOutput[] {
    return outputs.map((output) => {
        const outputType = output.output_type;
        if (outputType === 'stream') {
            return new vscode.NotebookCellOutput([
                vscode.NotebookCellOutputItem.text(String(output.text ?? ''), output.name === 'stderr' ? 'application/vnd.code.notebook.stderr' : 'application/vnd.code.notebook.stdout'),
            ]);
        }
        if (outputType === 'error') {
            return new vscode.NotebookCellOutput([
                vscode.NotebookCellOutputItem.error(new Error(String(output.evalue ?? output.ename ?? 'Execution failed'))),
            ]);
        }
        const data = output.data ?? {};
        const items: vscode.NotebookCellOutputItem[] = [];
        if (typeof data['text/plain'] === 'string') {
            items.push(vscode.NotebookCellOutputItem.text(data['text/plain']));
        }
        if (items.length === 0) {
            items.push(vscode.NotebookCellOutputItem.text(JSON.stringify(data)));
        }
        return new vscode.NotebookCellOutput(items, output.metadata ?? {});
    });
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

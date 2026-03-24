import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as util from 'util';
import * as vscode from 'vscode';

const execFile = util.promisify(childProcess.execFile);
const HEARTBEAT_INTERVAL_MS = 30_000;

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
    const value = config.get<string>('cliPath');
    const trimmed = value?.trim();
    return trimmed ? trimmed : undefined;
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
        if (!config.get<boolean>('v2AutoAttach', true)) {
            return;
        }
        const workspaceRoot = primaryWorkspaceRoot();
        if (!workspaceRoot) {
            return;
        }
        const storedSessionId = this.context.workspaceState.get<string>(sessionStorageKey(workspaceRoot));
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
            throw new Error('v2 attach returned no session_id');
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
            await this.runCli(current.workspaceRoot, [
                'v2', 'session-detach',
                '--workspace-root', current.workspaceRoot,
                '--session-id', current.sessionId,
            ]);
        } catch (err: any) {
            console.warn('[agent-repl] v2 auto-attach detach failed:', err?.message ?? String(err));
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
            console.warn('[agent-repl] v2 auto-attach heartbeat failed:', err?.message ?? String(err));
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
            throw new Error(`No working agent-repl launcher found for v2 auto-attach. Attempts: ${diagnostics.join(' | ')}`);
        }
        throw lastError ?? new Error('No working agent-repl launcher found for v2 auto-attach');
    }
}

function sessionStorageKey(workspaceRoot: string): string {
    return `agent-repl.v2.session:${workspaceRoot}`;
}

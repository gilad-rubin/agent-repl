import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
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

function sessionStorageKey(workspaceRoot: string): string {
    return `agent-repl.session:${workspaceRoot}`;
}

const childProcess = require('node:child_process');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const util = require('node:util');
const { downloadAndUnzipVSCode, resolveCliPathFromExecutablePath } = require('@vscode/test-electron');

const execFile = util.promisify(childProcess.execFile);
const REPO_ROOT = path.resolve(__dirname, '..', '..');
const EXTENSION_ROOT = path.resolve(__dirname, '..');
const BIN_DIR = process.platform === 'win32' ? 'Scripts' : 'bin';
const CLI_BASENAME = process.platform === 'win32' ? 'agent-repl.exe' : 'agent-repl';
const CLI_PATH = path.join(REPO_ROOT, '.venv', BIN_DIR, CLI_BASENAME);

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function ensureDir(dir) {
    await fsp.mkdir(dir, { recursive: true });
}

async function runJson(command, args, options = {}) {
    const result = await execFile(command, args, {
        cwd: options.cwd ?? REPO_ROOT,
        timeout: options.timeout ?? 30_000,
        maxBuffer: 16 * 1024 * 1024,
        env: options.env ?? process.env,
    });
    return JSON.parse(result.stdout);
}

async function packageVsix(targetPath) {
    await execFile('npx', ['vsce', 'package', '--allow-missing-repository', '--out', targetPath], {
        cwd: EXTENSION_ROOT,
        timeout: 120_000,
        maxBuffer: 16 * 1024 * 1024,
    });
}

async function waitForSession(workspaceRoot, runtimeDir, predicate, timeoutMs) {
    const started = Date.now();
    let lastPayload = null;
    while ((Date.now() - started) < timeoutMs) {
        lastPayload = await runJson('uv', [
            'run', 'agent-repl', '--pretty',
            'v2', 'sessions',
            '--workspace-root', workspaceRoot,
            '--runtime-dir', runtimeDir,
        ], { cwd: REPO_ROOT });
        const match = (lastPayload.sessions || []).find(predicate);
        if (match) {
            return match;
        }
        await sleep(1_000);
    }
    throw new Error(`Timed out waiting for session condition. Last payload: ${JSON.stringify(lastPayload)}`);
}

async function waitForBridge(workspaceRoot, timeoutMs) {
    const started = Date.now();
    let lastError = null;
    while ((Date.now() - started) < timeoutMs) {
        try {
            return await runJson('uv', ['run', 'agent-repl', '--pretty', 'status'], { cwd: workspaceRoot, timeout: 10_000 });
        } catch (err) {
            lastError = err;
            await sleep(1_000);
        }
    }
    throw lastError ?? new Error('Timed out waiting for bridge discovery');
}

async function terminateProcess(proc) {
    if (!proc || proc.exitCode !== null) {
        return;
    }
    proc.kill('SIGTERM');
    const started = Date.now();
    while (proc.exitCode === null && (Date.now() - started) < 15_000) {
        await sleep(250);
    }
    if (proc.exitCode === null) {
        proc.kill('SIGKILL');
    }
}

async function main() {
    if (!fs.existsSync(CLI_PATH)) {
        throw new Error(`Expected workspace CLI at ${CLI_PATH}`);
    }

    const tempRoot = await fsp.mkdtemp(path.join(os.tmpdir(), 'agent-repl-v2-installed-'));
    const runtimeDir = path.join(tempRoot, 'runtime');
    const workspaceRoot = path.join(tempRoot, 'workspace');
    const userDataDir = path.join(tempRoot, 'user-data');
    const extensionsDir = path.join(tempRoot, 'extensions');
    const artifactsDir = path.join(tempRoot, 'artifacts');
    const settingsDir = path.join(userDataDir, 'User');
    const vsixPath = path.join(artifactsDir, 'agent-repl.vsix');

    await Promise.all([
        ensureDir(runtimeDir),
        ensureDir(workspaceRoot),
        ensureDir(extensionsDir),
        ensureDir(artifactsDir),
        ensureDir(settingsDir),
    ]);

    await fsp.writeFile(path.join(workspaceRoot, 'README.md'), '# auto-attach smoke\n', 'utf8');
    await fsp.writeFile(
        path.join(settingsDir, 'settings.json'),
        JSON.stringify({
            'agent-repl.autoStart': true,
            'agent-repl.v2AutoAttach': true,
            'agent-repl.cliPath': CLI_PATH,
        }, null, 2),
        'utf8',
    );

    await runJson('uv', [
        'run', 'agent-repl', '--pretty',
        'v2', 'start',
        '--workspace-root', workspaceRoot,
        '--runtime-dir', runtimeDir,
    ], { cwd: REPO_ROOT });

    let vscodeProcess;
    try {
        await packageVsix(vsixPath);
        const vscodeExecutablePath = await downloadAndUnzipVSCode('stable');
        const vscodeCliPath = resolveCliPathFromExecutablePath(vscodeExecutablePath);

        await execFile(vscodeCliPath, [
            '--user-data-dir', userDataDir,
            '--extensions-dir', extensionsDir,
            '--install-extension', vsixPath,
            '--force',
        ], {
            cwd: REPO_ROOT,
            timeout: 120_000,
            maxBuffer: 16 * 1024 * 1024,
        });

        vscodeProcess = childProcess.spawn(vscodeExecutablePath, [
            '--new-window',
            '--disable-workspace-trust',
            '--user-data-dir', userDataDir,
            '--extensions-dir', extensionsDir,
            workspaceRoot,
        ], {
            cwd: REPO_ROOT,
            stdio: 'ignore',
        });

        const bridge = await waitForBridge(workspaceRoot, 60_000);
        const attached = await waitForSession(
            workspaceRoot,
            runtimeDir,
            session => session.client === 'vscode' && session.status === 'attached',
            60_000,
        );
        const initialSeen = attached.last_seen_at;
        const withHeartbeat = await waitForSession(
            workspaceRoot,
            runtimeDir,
            session => session.session_id === attached.session_id && session.status === 'attached' && session.last_seen_at > initialSeen,
            45_000,
        );

        await runJson('uv', ['run', 'agent-repl', '--pretty', 'stop'], { cwd: workspaceRoot });
        const detached = await waitForSession(
            workspaceRoot,
            runtimeDir,
            session => session.session_id === attached.session_id && session.status === 'detached',
            30_000,
        );

        console.log(JSON.stringify({
            status: 'ok',
            workspace_root: workspaceRoot,
            runtime_dir: runtimeDir,
            bridge_port: bridge.port,
            session_id: attached.session_id,
            initial_last_seen_at: initialSeen,
            heartbeat_last_seen_at: withHeartbeat.last_seen_at,
            detached_status: detached.status,
            vsix_path: vsixPath,
        }, null, 2));
    } finally {
        await terminateProcess(vscodeProcess);
        try {
            await runJson('uv', [
                'run', 'agent-repl', '--pretty',
                'v2', 'stop',
                '--workspace-root', workspaceRoot,
                '--runtime-dir', runtimeDir,
            ], { cwd: REPO_ROOT, timeout: 10_000 });
        } catch {
            // Best-effort cleanup for the smoke environment.
        }
    }
}

main().catch(err => {
    console.error(err?.stack ?? err?.message ?? String(err));
    process.exitCode = 1;
});

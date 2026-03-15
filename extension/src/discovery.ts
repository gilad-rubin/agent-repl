import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';
import * as crypto from 'crypto';

export interface ConnectionInfo {
    port: number;
    token: string;
    pid: number;
    version: string;
    workspace_folders: string[];
}

function runtimeDir(): string {
    return process.platform === 'darwin'
        ? path.join(os.homedir(), 'Library', 'Jupyter', 'runtime')
        : path.join(os.homedir(), '.local', 'share', 'jupyter', 'runtime');
}

function filePath(): string {
    return path.join(runtimeDir(), `agent-repl-bridge-${process.pid}.json`);
}

export function writeConnectionFile(info: ConnectionInfo): void {
    const dir = runtimeDir();
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(filePath(), JSON.stringify(info, null, 2), { mode: 0o600 });
}

export function removeConnectionFile(): void {
    try { fs.unlinkSync(filePath()); } catch { /* ok */ }
}

export function generateToken(): string {
    return crypto.randomBytes(24).toString('hex');
}

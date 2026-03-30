import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import {
    computeLineStarts,
    offsetToPosition,
    positionToOffset,
    buildVirtualDocument,
} from '../shared/notebookVirtualDocument';
import type {
    CellSnapshot,
    CellDiagnostic,
    DiagnosticsByCell,
    VirtualCellSegment,
    VirtualDocument,
} from '../shared/notebookVirtualDocument';

export type NotebookCellSnapshot = CellSnapshot;
export type { CellDiagnostic, DiagnosticsByCell };

export type CellCompletionItem = {
    label: string;
    kind?: string;
    detail?: string;
    documentation?: string;
    apply?: string;
    sortText?: string;
    filterText?: string;
};

export type LspStatus =
    | { state: 'starting'; message: string }
    | { state: 'ready'; message: string }
    | { state: 'unavailable'; message: string };

type LspPosition = { line: number; character: number };
type LspRange = { start: LspPosition; end: LspPosition };
type LspDiagnostic = {
    range: LspRange;
    severity?: number;
    message: string;
    source?: string;
};

type PublishDiagnosticsParams = {
    uri: string;
    diagnostics?: LspDiagnostic[];
};

type WorkspaceConfigurationParams = {
    items?: Array<{
        scopeUri?: string;
        section?: string;
    }>;
};

type LspCompletionItem = {
    label: string;
    kind?: number;
    detail?: string;
    documentation?: string | { kind?: string; value?: string };
    insertText?: string;
    filterText?: string;
    sortText?: string;
    textEdit?: {
        newText?: string;
    };
};

type CompletionList = {
    items?: LspCompletionItem[];
    isIncomplete?: boolean;
};

type JsonRpcRequest = {
    jsonrpc: '2.0';
    id: number;
    method: string;
    params?: unknown;
};

type JsonRpcNotification = {
    jsonrpc: '2.0';
    method: string;
    params?: unknown;
};

type JsonRpcResponse = {
    jsonrpc: '2.0';
    id: number;
    result?: unknown;
    error?: { code: number; message: string };
};

export type VirtualNotebookDocument = VirtualDocument & {
    filePath: string;
    shadowFilePath: string;
    uri: string;
};

const JSON_RPC_HEADER = '\r\n\r\n';
const SHADOW_STATE_DIRNAME = '.agent-repl';
const PYRIGHT_SHADOW_DIRNAME = 'pyright';
const DIAGNOSTIC_SEVERITY: Record<number, CellDiagnostic['severity']> = {
    1: 'error',
    2: 'warning',
    3: 'info',
    4: 'hint',
};

const COMPLETION_KIND: Record<number, string> = {
    2: 'module',
    3: 'class',
    4: 'interface',
    5: 'class',
    6: 'variable',
    7: 'class',
    8: 'interface',
    9: 'module',
    10: 'property',
    11: 'unit',
    12: 'function',
    13: 'variable',
    14: 'keyword',
    15: 'snippet',
    16: 'text',
    17: 'text',
    18: 'keyword',
    19: 'keyword',
    20: 'class',
    21: 'constant',
    22: 'class',
    23: 'interface',
    24: 'event',
    25: 'operator',
};

function pythonAnalysisSettings(): Record<string, unknown> {
    return {
        autoImportCompletions: true,
        autoSearchPaths: true,
        useLibraryCodeForTypes: true,
        diagnosticSeverityOverrides: {
            // Notebook cells commonly end with a value expression for display.
            reportUnusedExpression: 'none',
        },
    };
}

function clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
}

function virtualDocumentPath(notebookPath: string): string {
    const parsed = path.parse(notebookPath);
    return path.join(parsed.dir, `${parsed.base}.agent-repl.py`);
}

function virtualDocumentShadowPath(workspaceRoot: string, notebookPath: string): string {
    const relativeNotebookPath = path.relative(workspaceRoot, notebookPath);
    const safeNotebookPath = relativeNotebookPath.startsWith('..')
        ? path.join('_external', path.basename(notebookPath))
        : relativeNotebookPath;
    const parsed = path.parse(safeNotebookPath);
    return path.join(
        workspaceRoot,
        SHADOW_STATE_DIRNAME,
        PYRIGHT_SHADOW_DIRNAME,
        parsed.dir,
        `${parsed.base}.agent-repl.py`,
    );
}

function documentationToString(documentation: LspCompletionItem['documentation']): string | undefined {
    if (typeof documentation === 'string') {
        return documentation;
    }
    if (documentation && typeof documentation.value === 'string') {
        return documentation.value;
    }
    return undefined;
}

function mapCompletionItems(items: readonly LspCompletionItem[]): CellCompletionItem[] {
    return items
        .filter((item) => typeof item.label === 'string' && item.label.length > 0)
        .map((item) => ({
            label: item.label,
            kind: item.kind != null ? COMPLETION_KIND[item.kind] ?? 'text' : undefined,
            detail: item.detail,
            documentation: documentationToString(item.documentation),
            apply: item.textEdit?.newText ?? item.insertText,
            sortText: item.sortText,
            filterText: item.filterText,
        }));
}

export function buildWorkspaceConfiguration(params: WorkspaceConfigurationParams): unknown[] {
    return (params.items ?? []).map((item) => {
        switch (item.section) {
            case 'python':
                return {};
            case 'python.analysis':
                return pythonAnalysisSettings();
            case 'pyright':
                return {};
            default:
                return {};
        }
    });
}

export function defaultWorkspaceSettings(): Record<string, unknown> {
    return {
        python: {
            analysis: pythonAnalysisSettings(),
        },
        pyright: {},
    };
}

export function buildVirtualNotebookDocument(
    workspaceRoot: string,
    notebookPath: string,
    cells: readonly NotebookCellSnapshot[],
    version: number,
): VirtualNotebookDocument {
    const filePath = virtualDocumentPath(notebookPath);
    const shadowFilePath = virtualDocumentShadowPath(workspaceRoot, notebookPath);
    const base = buildVirtualDocument(cells, version);
    return {
        ...base,
        filePath,
        shadowFilePath,
        uri: vscode.Uri.file(filePath).toString(),
    };
}

export function mapDiagnosticsToCells(
    virtualDocument: VirtualNotebookDocument,
    params: PublishDiagnosticsParams,
): DiagnosticsByCell {
    const diagnosticsByCell: DiagnosticsByCell = {};
    for (const cell of virtualDocument.codeCells) {
        diagnosticsByCell[cell.cell_id] = [];
    }

    if (params.uri !== virtualDocument.uri) {
        return diagnosticsByCell;
    }

    for (const diagnostic of params.diagnostics ?? []) {
        const absoluteFrom = positionToOffset(
            virtualDocument.lineStarts,
            virtualDocument.text.length,
            diagnostic.range.start,
        );
        const absoluteTo = positionToOffset(
            virtualDocument.lineStarts,
            virtualDocument.text.length,
            diagnostic.range.end,
        );
        const segment = virtualDocument.codeCells.find((candidate) => (
            absoluteFrom <= candidate.contentTo &&
            absoluteTo >= candidate.contentFrom
        ));
        if (!segment) {
            continue;
        }

        const from = clamp(absoluteFrom - segment.contentFrom, 0, segment.source.length);
        const to = clamp(Math.max(absoluteTo - segment.contentFrom, from), from, segment.source.length);
        diagnosticsByCell[segment.cell_id].push({
            from,
            to,
            severity: DIAGNOSTIC_SEVERITY[diagnostic.severity ?? 1] ?? 'error',
            message: diagnostic.message,
            source: diagnostic.source,
        });
    }

    for (const cellId of Object.keys(diagnosticsByCell)) {
        diagnosticsByCell[cellId].sort((left, right) => left.from - right.from || left.to - right.to);
    }

    return diagnosticsByCell;
}

export class PyrightNotebookLspClient {
    private process: childProcess.ChildProcessWithoutNullStreams | null = null;
    private readBuffer = Buffer.alloc(0);
    private requestId = 0;
    private pending = new Map<number, {
        resolve: (value: unknown) => void;
        reject: (reason?: unknown) => void;
    }>();
    private stderrTail = '';
    private virtualDocument: VirtualNotebookDocument | null = null;
    private ready = false;
    private disposed = false;

    constructor(
        private readonly workspaceRoot: string,
        private readonly notebookPath: string,
        private readonly onDiagnostics: (diagnosticsByCell: DiagnosticsByCell) => void,
        private readonly onStatus: (status: LspStatus) => void,
        private readonly serverCommand: string,
        private readonly serverArgs: string[] = ['--stdio'],
    ) {}

    async start(cells: readonly NotebookCellSnapshot[]): Promise<void> {
        if (this.disposed || this.process) {
            return;
        }

        this.onStatus({ state: 'starting', message: 'Starting Pyright language server…' });
        const proc = childProcess.spawn(this.serverCommand, this.serverArgs, {
            cwd: this.workspaceRoot,
            stdio: 'pipe',
        });

        await new Promise<void>((resolve, reject) => {
            proc.once('spawn', () => resolve());
            proc.once('error', reject);
        }).catch((error: unknown) => {
            const detail = error instanceof Error ? error.message : String(error);
            this.onStatus({ state: 'unavailable', message: `Python IDE features unavailable: ${detail}` });
            throw error;
        });

        this.process = proc;
        proc.stdout.on('data', (chunk: Buffer) => this.handleStdout(chunk));
        proc.stderr.on('data', (chunk: Buffer) => {
            this.stderrTail = `${this.stderrTail}${chunk.toString('utf8')}`.slice(-4000);
        });
        proc.on('exit', (code, signal) => {
            this.process = null;
            this.rejectPending(new Error('Pyright language server stopped'));
            if (!this.disposed) {
                const suffix = code !== null
                    ? `exit code ${code}`
                    : signal
                        ? `signal ${signal}`
                        : 'an unknown reason';
                const detail = this.stderrTail.trim();
                const message = detail
                    ? `Python IDE features unavailable: Pyright stopped (${suffix}). ${detail}`
                    : `Python IDE features unavailable: Pyright stopped (${suffix}).`;
                this.onStatus({ state: 'unavailable', message });
            }
        });

        try {
            await this.request('initialize', {
                processId: process.pid,
                rootUri: vscode.Uri.file(this.workspaceRoot).toString(),
                workspaceFolders: [{
                    uri: vscode.Uri.file(this.workspaceRoot).toString(),
                    name: path.basename(this.workspaceRoot),
                }],
                clientInfo: {
                    name: 'agent-repl.canvas',
                },
                capabilities: {
                    textDocument: {
                        synchronization: {
                            didSave: false,
                            willSave: false,
                            willSaveWaitUntil: false,
                        },
                        publishDiagnostics: {},
                        completion: {
                            completionItem: {
                                documentationFormat: ['markdown', 'plaintext'],
                                insertReplaceSupport: false,
                                snippetSupport: false,
                            },
                        },
                    },
                    workspace: {
                        workspaceFolders: true,
                        configuration: true,
                    },
                },
            });
            this.notify('initialized', {});
            this.notify('workspace/didChangeConfiguration', {
                settings: defaultWorkspaceSettings(),
            });
            this.ready = true;
            this.onStatus({ state: 'ready', message: 'Python IDE features powered by Pyright.' });
            this.syncCells(cells);
        } catch (error) {
            const detail = error instanceof Error ? error.message : String(error);
            this.onStatus({ state: 'unavailable', message: `Python IDE features unavailable: ${detail}` });
            this.dispose();
        }
    }

    syncCells(cells: readonly NotebookCellSnapshot[]): void {
        if (!this.ready || this.disposed) {
            return;
        }

        const nextVersion = (this.virtualDocument?.version ?? 0) + 1;
        const nextVirtualDocument = buildVirtualNotebookDocument(this.workspaceRoot, this.notebookPath, cells, nextVersion);
        this.writeVirtualDocumentToDisk(nextVirtualDocument);

        if (!this.virtualDocument) {
            this.notify('textDocument/didOpen', {
                textDocument: {
                    uri: nextVirtualDocument.uri,
                    languageId: 'python',
                    version: nextVirtualDocument.version,
                    text: nextVirtualDocument.text,
                },
            });
        } else {
            this.notify('textDocument/didChange', {
                textDocument: {
                    uri: nextVirtualDocument.uri,
                    version: nextVirtualDocument.version,
                },
                contentChanges: [{ text: nextVirtualDocument.text }],
            });
        }

        this.virtualDocument = nextVirtualDocument;
    }

    async completeAt(
        cellId: string,
        offset: number,
        triggerCharacter?: string,
        explicit = false,
    ): Promise<CellCompletionItem[]> {
        if (!this.ready || this.disposed || !this.virtualDocument) {
            return [];
        }

        const segment = this.virtualDocument.codeCells.find((candidate) => candidate.cell_id === cellId);
        if (!segment) {
            return [];
        }

        const absoluteOffset = clamp(segment.contentFrom + offset, segment.contentFrom, segment.contentTo);
        const position = offsetToPosition(
            this.virtualDocument.lineStarts,
            this.virtualDocument.text.length,
            absoluteOffset,
        );
        const result = await this.request('textDocument/completion', {
            textDocument: { uri: this.virtualDocument.uri },
            position,
            context: triggerCharacter
                ? { triggerKind: 2, triggerCharacter }
                : { triggerKind: 1 },
        });

        const items = Array.isArray(result)
            ? result as LspCompletionItem[]
            : ((result as CompletionList | null | undefined)?.items ?? []);
        return mapCompletionItems(items);
    }

    dispose(): void {
        this.disposed = true;

        if (this.ready && this.virtualDocument) {
            this.notify('textDocument/didClose', {
                textDocument: { uri: this.virtualDocument.uri },
            });
        }
        this.ready = false;

        if (this.process) {
            this.process.kill();
            this.process = null;
        }

        if (this.virtualDocument) {
            try {
                fs.unlinkSync(this.virtualDocument.shadowFilePath);
            } catch {
                // Best-effort cleanup for the generated shadow document.
            }
        }

        this.rejectPending(new Error('Pyright language server disposed'));
    }

    private rejectPending(error: Error): void {
        for (const pending of this.pending.values()) {
            pending.reject(error);
        }
        this.pending.clear();
    }

    private handleStdout(chunk: Buffer): void {
        this.readBuffer = Buffer.concat([this.readBuffer, chunk]);

        while (true) {
            const headerEnd = this.readBuffer.indexOf(JSON_RPC_HEADER);
            if (headerEnd === -1) {
                return;
            }

            const headerText = this.readBuffer.subarray(0, headerEnd).toString('utf8');
            const contentLengthMatch = headerText.match(/Content-Length:\s*(\d+)/i);
            if (!contentLengthMatch) {
                this.readBuffer = Buffer.alloc(0);
                return;
            }

            const contentLength = Number.parseInt(contentLengthMatch[1], 10);
            const messageStart = headerEnd + JSON_RPC_HEADER.length;
            const totalLength = messageStart + contentLength;
            if (this.readBuffer.length < totalLength) {
                return;
            }

            const body = this.readBuffer.subarray(messageStart, totalLength).toString('utf8');
            this.readBuffer = this.readBuffer.subarray(totalLength);

            try {
                this.handleMessage(JSON.parse(body) as JsonRpcRequest | JsonRpcNotification | JsonRpcResponse);
            } catch {
                // Ignore malformed messages from the server and keep the session alive.
            }
        }
    }

    private handleMessage(message: JsonRpcRequest | JsonRpcNotification | JsonRpcResponse): void {
        if ('id' in message && !('method' in message)) {
            const pending = this.pending.get(message.id);
            if (!pending) {
                return;
            }
            this.pending.delete(message.id);
            if (message.error) {
                pending.reject(new Error(message.error.message));
            } else {
                pending.resolve(message.result);
            }
            return;
        }

        if ('method' in message && message.method === 'textDocument/publishDiagnostics') {
            if (!this.virtualDocument) {
                return;
            }
            this.onDiagnostics(mapDiagnosticsToCells(
                this.virtualDocument,
                message.params as PublishDiagnosticsParams,
            ));
            return;
        }

        if ('method' in message && 'id' in message) {
            if (message.method === 'workspace/configuration') {
                this.respond(
                    message.id,
                    buildWorkspaceConfiguration(message.params as WorkspaceConfigurationParams),
                );
                return;
            }
            this.respond(message.id, null);
        }
    }

    private request(method: string, params: unknown): Promise<unknown> {
        const id = this.requestId + 1;
        this.requestId = id;
        this.send({ jsonrpc: '2.0', id, method, params });
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
        });
    }

    private notify(method: string, params: unknown): void {
        this.send({ jsonrpc: '2.0', method, params });
    }

    private respond(id: number, result: unknown, error?: { code: number; message: string }): void {
        const payload: JsonRpcResponse = error
            ? { jsonrpc: '2.0', id, error }
            : { jsonrpc: '2.0', id, result };
        this.send(payload);
    }

    private send(message: JsonRpcRequest | JsonRpcNotification | JsonRpcResponse): void {
        if (!this.process || this.process.killed) {
            return;
        }
        const body = JSON.stringify(message);
        this.process.stdin.write(`Content-Length: ${Buffer.byteLength(body, 'utf8')}\r\n\r\n${body}`);
    }

    private writeVirtualDocumentToDisk(document: VirtualNotebookDocument): void {
        try {
            fs.mkdirSync(path.dirname(document.shadowFilePath), { recursive: true });
            fs.writeFileSync(document.shadowFilePath, document.text, 'utf8');
        } catch {
            // Keep the in-memory LSP session alive even if the shadow file can't be written.
        }
    }
}

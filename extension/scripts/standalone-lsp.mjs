import childProcess from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const {
  computeLineStarts,
  positionToOffset,
  buildVirtualDocument,
} = require('../out/shared/notebookVirtualDocument.js');

const JSON_RPC_HEADER = '\r\n\r\n';
const DIAGNOSTIC_SEVERITY = {
  1: 'error',
  2: 'warning',
  3: 'info',
  4: 'hint',
};

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function virtualDocumentPath(notebookPath) {
  const parsed = path.parse(notebookPath);
  return path.join(parsed.dir, `${parsed.base}.agent-repl.py`);
}

function buildVirtualNotebookDocument(notebookPath, cells, version) {
  const base = buildVirtualDocument(cells, version);
  return {
    ...base,
    uri: pathToFileURL(virtualDocumentPath(notebookPath)).toString(),
  };
}

function mapDiagnosticsToCells(virtualDocument, params) {
  const diagnosticsByCell = {};
  for (const cell of virtualDocument.codeCells) {
    diagnosticsByCell[cell.cell_id] = [];
  }

  if ((params?.uri ?? '') !== virtualDocument.uri) {
    return diagnosticsByCell;
  }

  for (const diagnostic of params?.diagnostics ?? []) {
    const absoluteFrom = positionToOffset(
      virtualDocument.lineStarts,
      virtualDocument.text.length,
      diagnostic.range?.start ?? { line: 0, character: 0 },
    );
    const absoluteTo = positionToOffset(
      virtualDocument.lineStarts,
      virtualDocument.text.length,
      diagnostic.range?.end ?? { line: 0, character: 0 },
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

export class StandaloneNotebookLspSession {
  constructor({
    workspaceRoot,
    notebookPath,
    serverCommand,
    serverArgs,
  }) {
    this.workspaceRoot = workspaceRoot;
    this.notebookPath = notebookPath;
    this.serverCommand = serverCommand;
    this.serverArgs = serverArgs;
    this.process = null;
    this.readBuffer = Buffer.alloc(0);
    this.requestId = 0;
    this.pending = new Map();
    this.stderrTail = '';
    this.virtualDocument = null;
    this.ready = false;
    this.disposed = false;
    this.diagnosticsByCell = {};
    this.status = { state: 'starting', message: 'Starting Pyright language server…' };
    this.updateWaiters = [];
    this.startPromise = null;
  }

  snapshot() {
    return {
      diagnosticsByCell: this.diagnosticsByCell,
      status: this.status,
    };
  }

  async syncCells(cells) {
    await this.ensureStarted(cells);
    if (!this.ready || this.disposed) {
      return this.snapshot();
    }

    const nextVersion = (this.virtualDocument?.version ?? 0) + 1;
    const nextVirtualDocument = buildVirtualNotebookDocument(this.notebookPath, cells, nextVersion);
    this.diagnosticsByCell = mapDiagnosticsToCells(nextVirtualDocument, {
      uri: nextVirtualDocument.uri,
      diagnostics: [],
    });

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
    await this.waitForUpdate(250);
    return this.snapshot();
  }

  dispose() {
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
    this.rejectPending(new Error('Pyright language server disposed'));
    this.flushWaiters();
  }

  async ensureStarted(cells) {
    if (this.disposed) {
      return;
    }
    if (this.ready) {
      return;
    }
    if (this.startPromise) {
      await this.startPromise;
      return;
    }

    this.startPromise = this.start(cells);
    try {
      await this.startPromise;
    } finally {
      this.startPromise = null;
    }
  }

  async start(cells) {
    const proc = childProcess.spawn(this.serverCommand, this.serverArgs, {
      cwd: this.workspaceRoot,
      stdio: 'pipe',
    });

    await new Promise((resolve, reject) => {
      proc.once('spawn', resolve);
      proc.once('error', reject);
    }).catch((error) => {
      const detail = error instanceof Error ? error.message : String(error);
      this.status = { state: 'unavailable', message: `Python IDE features unavailable: ${detail}` };
      this.flushWaiters();
      throw error;
    });

    this.process = proc;
    proc.stdout.on('data', (chunk) => this.handleStdout(chunk));
    proc.stderr.on('data', (chunk) => {
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
        this.status = {
          state: 'unavailable',
          message: detail
            ? `Python IDE features unavailable: Pyright stopped (${suffix}). ${detail}`
            : `Python IDE features unavailable: Pyright stopped (${suffix}).`,
        };
        this.flushWaiters();
      }
    });

    try {
      await this.request('initialize', {
        processId: process.pid,
        rootUri: pathToFileURL(this.workspaceRoot).toString(),
        workspaceFolders: [{
          uri: pathToFileURL(this.workspaceRoot).toString(),
          name: path.basename(this.workspaceRoot),
        }],
        clientInfo: {
          name: 'agent-repl.standalone-canvas',
        },
        capabilities: {
          textDocument: {
            synchronization: {
              didSave: false,
              willSave: false,
              willSaveWaitUntil: false,
            },
            publishDiagnostics: {},
          },
          workspace: {
            workspaceFolders: true,
          },
        },
      });
      this.notify('initialized', {});
      this.ready = true;
      this.status = { state: 'ready', message: 'Python IDE features powered by Pyright.' };
      this.flushWaiters();
      await this.syncCells(cells);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.status = { state: 'unavailable', message: `Python IDE features unavailable: ${detail}` };
      this.flushWaiters();
      this.dispose();
    }
  }

  waitForUpdate(timeoutMs) {
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.updateWaiters = this.updateWaiters.filter((entry) => entry !== waiter);
        resolve();
      }, timeoutMs);
      const waiter = () => {
        clearTimeout(timer);
        resolve();
      };
      this.updateWaiters.push(waiter);
    });
  }

  flushWaiters() {
    const waiters = [...this.updateWaiters];
    this.updateWaiters.length = 0;
    for (const waiter of waiters) {
      waiter();
    }
  }

  rejectPending(error) {
    for (const pending of this.pending.values()) {
      pending.reject(error);
    }
    this.pending.clear();
  }

  handleStdout(chunk) {
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
        this.handleMessage(JSON.parse(body));
      } catch {
        // Ignore malformed messages and keep the server alive.
      }
    }
  }

  handleMessage(message) {
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

    if (message.method === 'textDocument/publishDiagnostics') {
      if (!this.virtualDocument) {
        return;
      }
      this.diagnosticsByCell = mapDiagnosticsToCells(this.virtualDocument, message.params ?? {});
      this.flushWaiters();
      return;
    }

    if ('method' in message && 'id' in message) {
      this.respond(message.id, null, { code: -32601, message: `Unsupported method ${message.method}` });
    }
  }

  request(method, params) {
    const id = this.requestId + 1;
    this.requestId = id;
    this.send({ jsonrpc: '2.0', id, method, params });
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
    });
  }

  notify(method, params) {
    this.send({ jsonrpc: '2.0', method, params });
  }

  respond(id, result, error) {
    this.send(error
      ? { jsonrpc: '2.0', id, error }
      : { jsonrpc: '2.0', id, result });
  }

  send(message) {
    if (!this.process || this.process.killed) {
      return;
    }
    const body = JSON.stringify(message);
    this.process.stdin.write(`Content-Length: ${Buffer.byteLength(body, 'utf8')}\r\n\r\n${body}`);
  }
}

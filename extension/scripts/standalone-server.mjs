import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { promisify } from 'node:util';

import { StandaloneNotebookLspSession } from './standalone-lsp.mjs';

const execFileAsync = promisify(execFile);
const RUNTIME_FILE_PREFIX = 'agent-repl-core-';

function standaloneDebug(event, data = {}) {
  const record = {
    at: new Date().toISOString(),
    pid: process.pid,
    event: `standalone:${event}`,
    data,
  };
  try {
    const logDir = path.join(process.cwd(), '.agent-repl');
    fs.mkdirSync(logDir, { recursive: true });
    fs.appendFileSync(path.join(logDir, 'notebook-debug.log'), `${JSON.stringify(record)}\n`, 'utf8');
  } catch {
    // best-effort
  }
}
const DEFAULT_SESSION_LABEL = 'Standalone Canvas';

function runtimeDir() {
  return process.env.AGENT_REPL_RUNTIME_DIR
    ? path.resolve(process.env.AGENT_REPL_RUNTIME_DIR)
    : path.join(os.homedir(), 'Library', 'Jupyter', 'runtime');
}

function normalizePathname(value) {
  return process.platform === 'win32' ? value.toLowerCase() : value;
}

function pathWithin(targetPath, rootPath) {
  const target = normalizePathname(path.resolve(targetPath));
  const root = normalizePathname(path.resolve(rootPath));
  return target === root || target.startsWith(`${root}${path.sep}`);
}

const IGNORED_WORKSPACE_DIRS = new Set([
  '.agent-repl',
  '.git',
  '.hg',
  '.ipynb_checkpoints',
  '.mypy_cache',
  '.pytest_cache',
  '.ruff_cache',
  '.svn',
  '.venv',
  '__pycache__',
  'node_modules',
]);

export function normalizeNotebookPath(workspaceRoot, requestedPath) {
  if (typeof requestedPath !== 'string' || !requestedPath.trim()) {
    throw new Error('Missing notebook path. Open the browser view with ?path=relative/notebook.ipynb');
  }

  const trimmed = requestedPath.trim();
  const absoluteCandidate = path.isAbsolute(trimmed)
    ? path.resolve(trimmed)
    : path.resolve(workspaceRoot, trimmed);
  if (!pathWithin(absoluteCandidate, workspaceRoot)) {
    throw new Error('Notebook path must stay inside the workspace root');
  }
  return path.relative(workspaceRoot, absoluteCandidate) || path.basename(absoluteCandidate);
}

function compareExplorerEntries(left, right) {
  if (left.kind !== right.kind) {
    return left.kind === 'directory' ? -1 : 1;
  }
  return left.name.localeCompare(right.name, undefined, { sensitivity: 'base' });
}

function shouldSkipWorkspaceEntry(entry) {
  if (entry.name === '.' || entry.name === '..') {
    return true;
  }
  if (entry.isSymbolicLink()) {
    return true;
  }
  if (entry.isDirectory()) {
    return IGNORED_WORKSPACE_DIRS.has(entry.name) || entry.name.startsWith('.');
  }
  return false;
}

function buildWorkspaceTreeNode(workspaceRoot, relativeDir = '') {
  const absoluteDir = relativeDir
    ? path.join(workspaceRoot, relativeDir)
    : workspaceRoot;

  let entries = [];
  try {
    entries = fs.readdirSync(absoluteDir, { withFileTypes: true });
  } catch {
    return null;
  }

  const children = [];
  for (const entry of entries) {
    if (shouldSkipWorkspaceEntry(entry)) {
      continue;
    }

    const relativePath = relativeDir
      ? path.join(relativeDir, entry.name)
      : entry.name;

    if (entry.isDirectory()) {
      const child = buildWorkspaceTreeNode(workspaceRoot, relativePath);
      if (child) {
        children.push(child);
      }
      continue;
    }

    if (entry.isFile() && path.extname(entry.name).toLowerCase() === '.ipynb') {
      children.push({
        kind: 'notebook',
        name: entry.name,
        path: relativePath,
      });
    }
  }

  children.sort(compareExplorerEntries);

  if (relativeDir !== '' && children.length === 0) {
    return null;
  }

  return {
    kind: 'directory',
    name: relativeDir ? path.basename(relativeDir) : path.basename(workspaceRoot),
    path: relativeDir,
    children,
  };
}

export function listNotebookWorkspaceTree(workspaceRoot) {
  const root = buildWorkspaceTreeNode(workspaceRoot) ?? {
    kind: 'directory',
    name: path.basename(workspaceRoot),
    path: '',
    children: [],
  };

  return {
    workspace_root: workspaceRoot,
    workspace_name: path.basename(workspaceRoot),
    root,
  };
}

function resolveKernelDirs() {
  const dirs = new Set();
  const home = os.homedir();

  if (process.platform === 'darwin') {
    dirs.add(path.join(home, 'Library', 'Jupyter', 'kernels'));
    dirs.add('/Library/Jupyter/kernels');
    dirs.add('/usr/local/share/jupyter/kernels');
    dirs.add('/opt/homebrew/share/jupyter/kernels');
    dirs.add('/opt/miniconda3/share/jupyter/kernels');
    dirs.add('/usr/share/jupyter/kernels');
  } else if (process.platform === 'win32') {
    if (process.env.APPDATA) {
      dirs.add(path.join(process.env.APPDATA, 'jupyter', 'kernels'));
    }
    if (process.env.PROGRAMDATA) {
      dirs.add(path.join(process.env.PROGRAMDATA, 'jupyter', 'kernels'));
    }
  } else {
    dirs.add(path.join(home, '.local', 'share', 'jupyter', 'kernels'));
    dirs.add('/usr/local/share/jupyter/kernels');
    dirs.add('/usr/share/jupyter/kernels');
  }

  for (const entry of (process.env.JUPYTER_PATH ?? '').split(path.delimiter)) {
    if (entry) {
      dirs.add(path.join(entry, 'kernels'));
    }
  }

  return [...dirs].filter((dir) => fs.existsSync(dir));
}

function discoverKernels(workspaceRoot) {
  const workspaceVenvPython = path.join(
    workspaceRoot,
    '.venv',
    process.platform === 'win32' ? 'Scripts' : 'bin',
    process.platform === 'win32' ? 'python.exe' : 'python',
  );
  const hasWorkspaceVenv = fs.existsSync(workspaceVenvPython);
  const kernels = [];

  const pushKernel = (kernel) => {
    if (!kernels.some((existing) => existing.id === kernel.id || (existing.python && existing.python === kernel.python))) {
      kernels.push(kernel);
    }
  };

  for (const kernelsDir of resolveKernelDirs()) {
    let entries = [];
    try {
      entries = fs.readdirSync(kernelsDir, { withFileTypes: true });
    } catch {
      continue;
    }

    for (const entry of entries) {
      if (!entry.isDirectory()) {
        continue;
      }
      const kernelspecDir = path.join(kernelsDir, entry.name);
      const kernelJson = path.join(kernelspecDir, 'kernel.json');
      if (!fs.existsSync(kernelJson)) {
        continue;
      }
      try {
        const spec = JSON.parse(fs.readFileSync(kernelJson, 'utf8'));
        const python = Array.isArray(spec.argv) && typeof spec.argv[0] === 'string' ? spec.argv[0] : null;
        const recommended = hasWorkspaceVenv && python === workspaceVenvPython;
        pushKernel({
          id: entry.name,
          label: spec.display_name ?? entry.name,
          recommended,
          python,
        });
      } catch {
        continue;
      }
    }
  }

  let preferredKernel = null;
  if (hasWorkspaceVenv) {
    preferredKernel = kernels.find((kernel) => kernel.python === workspaceVenvPython) ?? null;
    if (!preferredKernel) {
      preferredKernel = {
        id: workspaceVenvPython,
        label: '.venv (workspace)',
        recommended: true,
        python: workspaceVenvPython,
      };
      pushKernel(preferredKernel);
    }
  }

  kernels.sort((left, right) => {
    if ((left.recommended ?? false) !== (right.recommended ?? false)) {
      return left.recommended ? -1 : 1;
    }
    return left.label.localeCompare(right.label);
  });

  return {
    kernels: kernels.map((kernel) => ({
      id: kernel.id,
      label: kernel.label,
      recommended: Boolean(kernel.recommended),
    })),
    preferred_kernel: preferredKernel
      ? { id: preferredKernel.id, label: preferredKernel.label }
      : undefined,
  };
}

function readJsonBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on('data', (chunk) => chunks.push(chunk));
    request.on('end', () => {
      try {
        const raw = Buffer.concat(chunks).toString('utf8');
        resolve(raw ? JSON.parse(raw) : {});
      } catch (error) {
        reject(error);
      }
    });
    request.on('error', reject);
  });
}

function sendJson(response, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': String(body.length),
    'Cache-Control': 'no-store',
  });
  response.end(body);
}

async function fetchJson(url, init) {
  const response = await fetch(url, init);
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { error: text || `Request failed with status ${response.status}` };
  }
  if (!response.ok) {
    const error = new Error(payload?.error ?? payload?.message ?? `Request failed with status ${response.status}`);
    error.statusCode = response.status;
    error.conflict = Boolean(payload?.conflict || payload?.reason === 'lease-conflict');
    error.payload = payload;
    throw error;
  }
  return payload;
}

function createDaemonLocator(workspaceRoot) {
  return () => {
    const dir = runtimeDir();
    if (!fs.existsSync(dir)) {
      throw new Error(`Runtime directory not found: ${dir}`);
    }
    const files = fs.readdirSync(dir)
      .filter((name) => name.startsWith(RUNTIME_FILE_PREFIX) && name.endsWith('.json'))
      .map((name) => ({
        name,
        fullPath: path.join(dir, name),
        mtimeMs: fs.statSync(path.join(dir, name)).mtimeMs,
      }))
      .sort((left, right) => right.mtimeMs - left.mtimeMs);

    for (const file of files) {
      try {
        const info = JSON.parse(fs.readFileSync(file.fullPath, 'utf8'));
        if (!pathWithin(workspaceRoot, info.workspace_root)) {
          continue;
        }
        return {
          baseUrl: `http://127.0.0.1:${info.port}`,
          token: info.token,
        };
      } catch {
        continue;
      }
    }
    throw new Error(`No running agent-repl core daemon matched '${workspaceRoot}'`);
  };
}

export function createStandaloneServices({
  repoRoot,
  workspaceRoot,
  extensionRoot,
  locateDaemon: locateDaemonOverride,
  runAgentRepl: runAgentReplOverride,
  fetchJson: fetchJsonOverride,
}) {
  const sessions = new Map();
  const lspSessions = new Map();
  const backgroundNotebookCommands = new Map();
  const locateDaemon = locateDaemonOverride ?? createDaemonLocator(workspaceRoot);
  const fetchJsonFn = fetchJsonOverride ?? fetchJson;
  const pyrightServerScript = path.join(extensionRoot, 'node_modules', 'pyright', 'langserver.index.js');

  async function runAgentRepl(args, cwd = workspaceRoot) {
    const workspacePython = path.join(
      repoRoot,
      '.venv',
      process.platform === 'win32' ? 'Scripts' : 'bin',
      process.platform === 'win32' ? 'python.exe' : 'python',
    );
    const launchPlans = [
      {
        command: 'uv',
        args: ['run', '--project', repoRoot, 'agent-repl', ...args],
        env: process.env,
      },
      {
        command: workspacePython,
        args: ['-m', 'agent_repl.cli', ...args],
        env: {
          ...process.env,
          PYTHONPATH: path.join(repoRoot, 'src'),
        },
      },
      {
        command: 'python3',
        args: ['-m', 'agent_repl.cli', ...args],
        env: {
          ...process.env,
          PYTHONPATH: path.join(repoRoot, 'src'),
        },
      },
    ];

    let lastError = null;
    for (const plan of launchPlans) {
      try {
        const { stdout } = await execFileAsync(plan.command, plan.args, {
          cwd,
          env: plan.env,
        });
        return stdout ? JSON.parse(stdout) : {};
      } catch (error) {
        lastError = error;
      }
    }

    throw lastError instanceof Error
      ? lastError
      : new Error('Failed to launch agent-repl');
  }

  async function ensureSession(clientId) {
    const existing = sessions.get(clientId);
    if (existing) {
      return existing;
    }

    const daemon = locateDaemon();
    try {
      const payload = await fetchJsonFn(`${daemon.baseUrl}/api/sessions/resolve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `token ${daemon.token}`,
        },
        body: JSON.stringify({ actor: 'human' }),
      });
      const reusableSessionId = payload?.session?.session_id;
      if (reusableSessionId) {
        const session = {
          sessionId: reusableSessionId,
          daemon,
          owned: false,
        };
        sessions.set(clientId, session);
        return session;
      }
    } catch {
      // Fall back to creating a standalone-owned session when discovery fails.
    }

    const attachResult = await (runAgentReplOverride ?? runAgentRepl)([
      'core',
      'attach',
      '--workspace-root',
      workspaceRoot,
      '--actor',
      'human',
      '--client-type',
      'browser',
      '--label',
      DEFAULT_SESSION_LABEL,
      '--session-id',
      clientId,
    ], repoRoot);

    const session = {
      sessionId: attachResult?.session?.session_id ?? clientId,
      daemon,
      owned: true,
    };
    sessions.set(clientId, session);
    return session;
  }

  async function daemonPost(clientId, endpoint, body) {
    const session = await ensureSession(clientId);
    return fetchJsonFn(`${session.daemon.baseUrl}${endpoint}`, {
      method: 'POST',
      headers: {
        'Authorization': `token ${session.daemon.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });
  }

  function lspKey(clientId, notebookPath) {
    return `${clientId}:${notebookPath}`;
  }

  function getLspSession(clientId, notebookPath) {
    const key = lspKey(clientId, notebookPath);
    let session = lspSessions.get(key);
    if (!session) {
      session = new StandaloneNotebookLspSession({
        workspaceRoot,
        notebookPath: path.join(workspaceRoot, notebookPath),
        serverCommand: process.env.AGENT_REPL_PYRIGHT_COMMAND?.trim() || process.execPath,
        serverArgs: process.env.AGENT_REPL_PYRIGHT_COMMAND?.trim()
          ? ['--stdio']
          : [pyrightServerScript, '--stdio'],
      });
      lspSessions.set(key, session);
    }
    return session;
  }

  function disposeClient(clientId) {
    const session = sessions.get(clientId);
    sessions.delete(clientId);
    for (const [key, lsp] of lspSessions.entries()) {
      if (key.startsWith(`${clientId}:`)) {
        lsp.dispose();
        lspSessions.delete(key);
      }
    }
    return session;
  }

  async function handleAttach(payload) {
    const clientId = typeof payload.client_id === 'string' && payload.client_id ? payload.client_id : null;
    const notebookPath = normalizeNotebookPath(workspaceRoot, payload.path);
    if (!clientId) {
      throw new Error('Missing client_id');
    }
    const session = await ensureSession(clientId);
    return {
      status: 'ok',
      client_id: clientId,
      session_id: session.sessionId,
      path: notebookPath,
      workspace_root: workspaceRoot,
    };
  }

  async function handleSessionTouch(payload) {
    const clientId = typeof payload.client_id === 'string' && payload.client_id ? payload.client_id : null;
    if (!clientId) {
      throw new Error('Missing client_id');
    }
    const session = await ensureSession(clientId);
    return daemonPost(clientId, '/api/sessions/touch', { session_id: session.sessionId });
  }

  async function handleSessionEnd(payload) {
    const clientId = typeof payload.client_id === 'string' && payload.client_id ? payload.client_id : null;
    if (!clientId) {
      throw new Error('Missing client_id');
    }
    const session = disposeClient(clientId);
    if (!session) {
      return { status: 'ok', ended: false };
    }
    if (!session.owned) {
      return { status: 'ok', ended: false, borrowed: true };
    }
    return fetchJsonFn(`${session.daemon.baseUrl}/api/sessions/detach`, {
      method: 'POST',
      headers: {
        'Authorization': `token ${session.daemon.token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ session_id: session.sessionId }),
    }).catch(() => ({ status: 'ok', detached: true }));
  }

  async function handleNotebookProxy(endpoint, payload, { withOwnerSession = false } = {}) {
    const clientId = typeof payload.client_id === 'string' && payload.client_id ? payload.client_id : null;
    if (!clientId) {
      throw new Error('Missing client_id');
    }
    const notebookPath = normalizeNotebookPath(workspaceRoot, payload.path);
    const body = { ...payload, path: notebookPath };
    delete body.client_id;
    if (withOwnerSession) {
      const session = await ensureSession(clientId);
      body.owner_session_id = session.sessionId;
    }
    const startMs = Date.now();
    standaloneDebug('proxy:request', {
      endpoint,
      clientId,
      notebookPath,
      cell_id: payload.cell_id ?? null,
      cell_index: payload.cell_index ?? null,
      owner_session_id: body.owner_session_id ?? null,
    });
    try {
      const result = await daemonPost(clientId, endpoint, body);
      standaloneDebug('proxy:response', {
        endpoint,
        clientId,
        cell_id: payload.cell_id ?? null,
        status: result?.status ?? null,
        durationMs: Date.now() - startMs,
      });
      return result;
    } catch (error) {
      standaloneDebug('proxy:error', {
        endpoint,
        clientId,
        cell_id: payload.cell_id ?? null,
        error: error?.message ?? String(error),
        conflict: Boolean(error?.conflict),
        durationMs: Date.now() - startMs,
      });
      throw error;
    }
  }

  async function startBackgroundNotebookProxy(
    endpoint,
    payload,
    {
      withOwnerSession = false,
      responsePayload = {},
    } = {},
  ) {
    const clientId = typeof payload.client_id === 'string' && payload.client_id ? payload.client_id : null;
    if (!clientId) {
      throw new Error('Missing client_id');
    }
    const notebookPath = normalizeNotebookPath(workspaceRoot, payload.path);
    const commandId = randomUUID();
    const startMs = Date.now();

    const task = (async () => {
      standaloneDebug('proxy:background-request', {
        endpoint,
        commandId,
        clientId,
        notebookPath,
        cell_id: payload.cell_id ?? null,
        cell_index: payload.cell_index ?? null,
      });
      try {
        await handleNotebookProxy(endpoint, payload, { withOwnerSession });
        standaloneDebug('proxy:background-response', {
          endpoint,
          commandId,
          clientId,
          status: 'ok',
          durationMs: Date.now() - startMs,
        });
      } catch (error) {
        standaloneDebug('proxy:background-error', {
          endpoint,
          commandId,
          clientId,
          error: error?.message ?? String(error),
          conflict: Boolean(error?.conflict),
          durationMs: Date.now() - startMs,
        });
      } finally {
        backgroundNotebookCommands.delete(commandId);
      }
    })();

    backgroundNotebookCommands.set(commandId, task);
    return {
      status: 'started',
      path: notebookPath,
      command_id: commandId,
      ...responsePayload,
    };
  }

  async function handleWorkspaceTree(payload) {
    const notebookPath = typeof payload.path === 'string' && payload.path.trim()
      ? normalizeNotebookPath(workspaceRoot, payload.path)
      : null;
    return {
      status: 'ok',
      ...listNotebookWorkspaceTree(workspaceRoot),
      selected_path: notebookPath,
    };
  }

  return {
    async handleApiRequest(request, response) {
      const requestPath = new URL(request.url || '/', 'http://127.0.0.1').pathname;
      let payload = {};
      try {
        payload = await readJsonBody(request);

        if (requestPath === '/api/standalone/attach') {
          sendJson(response, 200, await handleAttach(payload));
          return true;
        }
        if (requestPath === '/api/standalone/session-touch') {
          sendJson(response, 200, await handleSessionTouch(payload));
          return true;
        }
        if (requestPath === '/api/standalone/session-end') {
          sendJson(response, 200, await handleSessionEnd(payload));
          return true;
        }
        if (requestPath === '/api/standalone/kernels') {
          sendJson(response, 200, discoverKernels(workspaceRoot));
          return true;
        }
        if (requestPath === '/api/standalone/workspace-tree') {
          sendJson(response, 200, await handleWorkspaceTree(payload));
          return true;
        }
        if (requestPath === '/api/standalone/lsp/sync') {
          const clientId = typeof payload.client_id === 'string' && payload.client_id ? payload.client_id : null;
          if (!clientId) {
            throw new Error('Missing client_id');
          }
          const notebookPath = normalizeNotebookPath(workspaceRoot, payload.path);
          if (!Array.isArray(payload.cells)) {
            throw new Error('Missing cells');
          }
          const lsp = getLspSession(clientId, notebookPath);
          const result = await lsp.syncCells(payload.cells.filter((cell) => cell && typeof cell === 'object'));
          sendJson(response, 200, {
            status: 'ok',
            diagnostics_by_cell: result.diagnosticsByCell,
            lsp_status: result.status,
          });
          return true;
        }

        const notebookRouteMap = new Map([
          ['/api/standalone/notebook/contents', ['/api/notebooks/contents', false]],
          ['/api/standalone/notebook/edit', ['/api/notebooks/edit', true]],
          ['/api/standalone/notebook/execute-cell', ['/api/notebooks/execute-cell', true]],
          ['/api/standalone/notebook/interrupt', ['/api/notebooks/interrupt', false]],
          ['/api/standalone/notebook/execute-all', ['/api/notebooks/execute-all', true]],
          ['/api/standalone/notebook/select-kernel', ['/api/notebooks/select-kernel', false]],
          ['/api/standalone/notebook/restart', ['/api/notebooks/restart', false]],
          ['/api/standalone/notebook/restart-and-run-all', ['/api/notebooks/restart-and-run-all', true]],
          ['/api/standalone/notebook/runtime', ['/api/notebooks/runtime', false]],
          ['/api/standalone/notebook/status', ['/api/notebooks/status', false]],
          ['/api/standalone/notebook/activity', ['/api/notebooks/activity', false]],
        ]);

        const notebookAsyncRouteMap = new Map([
          ['/api/standalone/notebook/execute-cell-async', ['/api/notebooks/execute-cell', true]],
          ['/api/standalone/notebook/execute-all-async', ['/api/notebooks/execute-all', true]],
          ['/api/standalone/notebook/restart-and-run-all-async', ['/api/notebooks/restart-and-run-all', true]],
        ]);

        if (notebookRouteMap.has(requestPath)) {
          const [endpoint, withOwnerSession] = notebookRouteMap.get(requestPath);
          sendJson(response, 200, await handleNotebookProxy(endpoint, payload, { withOwnerSession }));
          return true;
        }

        if (notebookAsyncRouteMap.has(requestPath)) {
          const [endpoint, withOwnerSession] = notebookAsyncRouteMap.get(requestPath);
          const responsePayload = requestPath === '/api/standalone/notebook/execute-cell-async'
            ? {
                cell_id: typeof payload.cell_id === 'string' ? payload.cell_id : null,
              }
            : {};
          sendJson(response, 200, await startBackgroundNotebookProxy(endpoint, payload, {
            withOwnerSession,
            responsePayload,
          }));
          return true;
        }

        return false;
      } catch (error) {
        const statusCode = error?.statusCode ?? 400;
        sendJson(response, statusCode, {
          error: error instanceof Error ? error.message : String(error),
          conflict: Boolean(error?.conflict),
        });
        return true;
      }
    },
    async dispose() {
      for (const clientId of [...sessions.keys()]) {
        const session = disposeClient(clientId);
        if (!session) {
          continue;
        }
        try {
          await fetchJson(`${session.daemon.baseUrl}/api/sessions/end`, {
            method: 'POST',
            headers: {
              'Authorization': `token ${session.daemon.token}`,
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({ session_id: session.sessionId }),
          });
        } catch {
          // Best-effort cleanup only.
        }
      }
    },
  };
}

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const test = require('node:test');

function isHealthCheck(url) {
  return url === 'http://daemon/api/health';
}

async function invokeApi(services, requestPath, payload) {
  const request = new PassThrough();
  request.method = 'POST';
  request.url = requestPath;
  request.headers = { 'content-type': 'application/json' };

  let statusCode = 0;
  let responseBody = '';
  const response = {
    writeHead(code) {
      statusCode = code;
    },
    end(chunk) {
      responseBody = chunk ? Buffer.from(chunk).toString('utf8') : '';
    },
  };

  request.end(JSON.stringify(payload));
  const handled = await services.handleApiRequest(request, response);
  return {
    handled,
    statusCode,
    body: responseBody ? JSON.parse(responseBody) : {},
  };
}

function withHealthyDaemon(handler) {
  return async (url, init) => {
    if (url === 'http://daemon/api/health') {
      return { status: 'ok' };
    }
    return handler(url, init);
  };
}

async function waitFor(check, timeoutMs = 1_000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    const result = check();
    if (result) {
      return result;
    }
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
  throw new Error(`Condition did not become true within ${timeoutMs}ms`);
}

test('normalizeNotebookPath keeps relative paths inside workspace', async () => {
  const { normalizeNotebookPath } = await import('../scripts/standalone-server.mjs');
  const workspaceRoot = path.resolve('/tmp/agent-repl-workspace');

  assert.equal(
    normalizeNotebookPath(workspaceRoot, 'notebooks/demo.ipynb'),
    path.join('notebooks', 'demo.ipynb'),
  );
});

test('normalizeNotebookPath accepts absolute paths inside workspace', async () => {
  const { normalizeNotebookPath } = await import('../scripts/standalone-server.mjs');
  const workspaceRoot = path.resolve('/tmp/agent-repl-workspace');
  const absolutePath = path.join(workspaceRoot, 'nested', 'example.ipynb');

  assert.equal(
    normalizeNotebookPath(workspaceRoot, absolutePath),
    path.join('nested', 'example.ipynb'),
  );
});

test('normalizeNotebookPath rejects escaping the workspace root', async () => {
  const { normalizeNotebookPath, resolveNotebookTarget } = await import('../scripts/standalone-server.mjs');
  const parentRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-repl-parent-'));
  const workspaceRoot = path.join(parentRoot, 'agent-repl');
  const siblingRoot = path.join(parentRoot, 'subtext');
  fs.mkdirSync(workspaceRoot, { recursive: true });
  fs.mkdirSync(path.join(siblingRoot, 'notebooks'), { recursive: true });
  fs.writeFileSync(path.join(siblingRoot, '.git'), '');

  try {
    const requestedPath = path.join('..', 'subtext', 'notebooks', 'demo.ipynb');
    const resolved = resolveNotebookTarget(workspaceRoot, requestedPath);

    assert.equal(resolved.workspaceRoot, siblingRoot);
    assert.equal(resolved.notebookPath, path.join('notebooks', 'demo.ipynb'));
    assert.equal(
      normalizeNotebookPath(workspaceRoot, requestedPath),
      path.join('notebooks', 'demo.ipynb'),
    );
  } finally {
    fs.rmSync(parentRoot, { recursive: true, force: true });
  }
});

test('createDaemonLocator requires an exact workspace-root match', async () => {
  const { createDaemonLocator } = await import('../scripts/standalone-server.mjs');
  const runtimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-repl-runtime-'));
  process.env.AGENT_REPL_RUNTIME_DIR = runtimeRoot;

  try {
    fs.writeFileSync(path.join(runtimeRoot, 'agent-repl-core-parent.json'), JSON.stringify({
      workspace_root: '/workspace',
      port: 4100,
      token: 'parent-token',
    }));
    fs.writeFileSync(path.join(runtimeRoot, 'agent-repl-core-child.json'), JSON.stringify({
      workspace_root: '/workspace/project',
      port: 4200,
      token: 'child-token',
    }));

    const locateDaemon = createDaemonLocator();
    const daemon = locateDaemon('/workspace/project');
    assert.deepEqual(daemon, {
      baseUrl: 'http://127.0.0.1:4200',
      token: 'child-token',
    });
  } finally {
    delete process.env.AGENT_REPL_RUNTIME_DIR;
    fs.rmSync(runtimeRoot, { recursive: true, force: true });
  }
});

test('standalone attach reuses the preferred human session instead of creating a new one', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let attachCalls = 0;
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      assert.equal(url, 'http://daemon/api/sessions/resolve');
      return {
        status: 'ok',
        session: {
          session_id: 'sess-vscode',
          actor: 'human',
          client: 'vscode',
          status: 'attached',
          capabilities: ['projection', 'editor', 'presence'],
          last_seen_at: 42,
          created_at: 24,
        },
      };
    }),
    runAgentRepl: async () => {
      attachCalls += 1;
      return { status: 'ok', session: { session_id: 'sess-new' } };
    },
  });

  const result = await invokeApi(services, '/api/standalone/attach', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.session_id, 'sess-vscode');
  assert.equal(result.body.path, path.join('tmp', 'demo.ipynb'));
  assert.equal(attachCalls, 0);
  assert.deepEqual(
    fetchCalls.map((call) => call.url),
    ['http://daemon/api/sessions/resolve'],
  );
});

test('standalone waits for a located daemon to become healthy before reusing its session', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let healthChecks = 0;
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/health') {
        healthChecks += 1;
        if (healthChecks < 3) {
          throw new Error('fetch failed');
        }
        return { status: 'ok' };
      }
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-vscode',
            actor: 'human',
            client: 'vscode',
            status: 'attached',
            capabilities: ['projection', 'editor', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/status') {
        return { status: 'ok', running: [], queued: [] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
    runAgentRepl: async () => {
      throw new Error('runAgentRepl should not be called when the located daemon becomes healthy');
    },
  });

  const result = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-reuse-wait',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { status: 'ok', running: [], queued: [] });
  assert.deepEqual(
    fetchCalls.map((call) => call.url),
    [
      'http://daemon/api/health',
      'http://daemon/api/health',
      'http://daemon/api/health',
      'http://daemon/api/sessions/resolve',
      'http://daemon/api/notebooks/status',
    ],
  );
});

test('standalone waits for a newly attached daemon to become healthy before proxying notebook requests', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let attached = false;
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => {
      if (!attached) {
        throw new Error('not running');
      }
      return { baseUrl: 'http://daemon', token: 'secret' };
    },
    runAgentRepl: async () => {
      attached = true;
      return { status: 'ok', session: { session_id: 'sess-new' } };
    },
    fetchJson: async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/health') {
        return { status: 'ok' };
      }
      if (url === 'http://daemon/api/notebooks/status') {
        return { status: 'ok', running: [], queued: [] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  });

  const result = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-attach',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { status: 'ok', running: [], queued: [] });
  assert.deepEqual(
    fetchCalls.map((call) => call.url),
    [
      'http://daemon/api/health',
      'http://daemon/api/sessions/resolve',
      'http://daemon/api/health',
      'http://daemon/api/notebooks/status',
    ],
  );
});

test('standalone starts a workspace daemon before attaching when none is running', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const commands = [];
  let daemonStarted = false;
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => {
      if (!daemonStarted) {
        throw new Error('not running');
      }
      return { baseUrl: 'http://daemon', token: 'secret' };
    },
    runAgentRepl: async (args) => {
      commands.push(args);
      if (args[0] === 'core' && args[1] === 'start') {
        daemonStarted = true;
        return { status: 'ok' };
      }
      if (args[0] === 'core' && args[1] === 'attach') {
        return { status: 'ok', session: { session_id: 'sess-new' } };
      }
      throw new Error(`Unexpected command: ${args.join(' ')}`);
    },
    fetchJson: async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/health') {
        return { status: 'ok' };
      }
      if (url === 'http://daemon/api/sessions/resolve') {
        return { status: 'ok', session: null };
      }
      if (url === 'http://daemon/api/notebooks/status') {
        return { status: 'ok', running: [], queued: [] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  });

  const result = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-start-daemon',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.statusCode, 200);
  assert.deepEqual(commands, [
    ['core', 'start', '--workspace-root', '/workspace'],
    ['core', 'attach', '--workspace-root', '/workspace', '--actor', 'human', '--client-type', 'browser', '--label', 'Standalone Canvas', '--session-id', 'client-start-daemon'],
  ]);
  assert.deepEqual(
    fetchCalls.map((call) => call.url),
    [
      'http://daemon/api/health',
      'http://daemon/api/sessions/resolve',
      'http://daemon/api/health',
      'http://daemon/api/notebooks/status',
    ],
  );
});

test('standalone retries daemon discovery after attach until the runtime file appears', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let startCalls = 0;
  let attachCalls = 0;
  let locateAttemptsAfterAttach = 0;
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => {
      if (attachCalls === 0) {
        throw new Error('not running');
      }
      locateAttemptsAfterAttach += 1;
      if (locateAttemptsAfterAttach < 3) {
        throw new Error('runtime file not visible yet');
      }
      return { baseUrl: 'http://daemon', token: 'secret' };
    },
    runAgentRepl: async (args) => {
      if (args[0] === 'core' && args[1] === 'start') {
        startCalls += 1;
        return { status: 'ok' };
      }
      attachCalls += 1;
      return { status: 'ok', session: { session_id: 'sess-new' } };
    },
    fetchJson: async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/health') {
        return { status: 'ok' };
      }
      if (url === 'http://daemon/api/notebooks/status') {
        return { status: 'ok', running: [], queued: [] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  });

  const result = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-retry',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body, { status: 'ok', running: [], queued: [] });
  assert.equal(startCalls, 1);
  assert.equal(attachCalls, 1);
  assert.equal(locateAttemptsAfterAttach, 3);
});

test('standalone refreshes a cached session when its daemon stops being healthy', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let currentDaemon = { baseUrl: 'http://daemon-a', token: 'secret-a' };
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => currentDaemon,
    fetchJson: async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon-a/api/health') {
        if (currentDaemon.baseUrl === 'http://daemon-a') {
          return { status: 'ok' };
        }
        throw new Error('fetch failed');
      }
      if (url === 'http://daemon-a/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-a',
            actor: 'human',
            client: 'browser',
            status: 'attached',
          },
        };
      }
      if (url === 'http://daemon-a/api/notebooks/status') {
        return { status: 'ok', running: [], queued: [] };
      }
      if (url === 'http://daemon-b/api/health') {
        return { status: 'ok' };
      }
      if (url === 'http://daemon-b/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-b',
            actor: 'human',
            client: 'browser',
            status: 'attached',
          },
        };
      }
      if (url === 'http://daemon-b/api/notebooks/runtime') {
        return { status: 'ok', runtime: { busy: false } };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  });

  const first = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-refresh',
    path: 'tmp/demo.ipynb',
  });
  assert.equal(first.statusCode, 200);

  currentDaemon = { baseUrl: 'http://daemon-b', token: 'secret-b' };
  fetchCalls.length = 0;
  const second = await invokeApi(services, '/api/standalone/notebook/runtime', {
    client_id: 'client-refresh',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(second.statusCode, 200);
  const fetchUrls = fetchCalls.map((call) => call.url);
  assert.ok(fetchUrls.some((url) => url === 'http://daemon-a/api/health'));
  assert.deepEqual(
    fetchUrls.slice(-3),
    [
      'http://daemon-b/api/health',
      'http://daemon-b/api/sessions/resolve',
      'http://daemon-b/api/notebooks/runtime',
    ],
  );
});

test('listNotebookWorkspaceTree keeps notebook folders, prunes empty ones, and sorts folders before files', async () => {
  const { listNotebookWorkspaceTree } = await import('../scripts/standalone-server.mjs');
  const workspaceRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-repl-tree-'));

  try {
    fs.mkdirSync(path.join(workspaceRoot, 'notebooks', 'nested'), { recursive: true });
    fs.mkdirSync(path.join(workspaceRoot, 'scratch'), { recursive: true });
    fs.mkdirSync(path.join(workspaceRoot, '.git'), { recursive: true });
    fs.writeFileSync(path.join(workspaceRoot, 'root.ipynb'), '{}');
    fs.writeFileSync(path.join(workspaceRoot, 'notes.md'), '# not a notebook');
    fs.writeFileSync(path.join(workspaceRoot, '.git', 'ignored.ipynb'), '{}');
    fs.writeFileSync(path.join(workspaceRoot, 'notebooks', 'b.ipynb'), '{}');
    fs.writeFileSync(path.join(workspaceRoot, 'notebooks', 'a.txt'), 'ignore me');
    fs.writeFileSync(path.join(workspaceRoot, 'notebooks', 'nested', 'a.ipynb'), '{}');

    const tree = listNotebookWorkspaceTree(workspaceRoot);

    assert.equal(tree.workspace_name, path.basename(workspaceRoot));
    assert.deepEqual(
      tree.root.children.map((child) => [child.kind, child.name]),
      [
        ['directory', 'notebooks'],
        ['notebook', 'root.ipynb'],
      ],
    );

    const notebooksDir = tree.root.children[0];
    assert.deepEqual(
      notebooksDir.children.map((child) => [child.kind, child.name]),
      [
        ['directory', 'nested'],
        ['notebook', 'b.ipynb'],
      ],
    );

    assert.deepEqual(
      notebooksDir.children[0].children.map((child) => child.path),
      [path.join('notebooks', 'nested', 'a.ipynb')],
    );
  } finally {
    fs.rmSync(workspaceRoot, { recursive: true, force: true });
  }
});

test('discoverKernels returns python-path ids and skips non-kernel-capable candidates', async () => {
  const { discoverKernels } = await import('../scripts/standalone-server.mjs');
  const parentRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-repl-kernels-'));
  const workspaceRoot = path.join(parentRoot, 'workspace');
  const kernelDir = path.join(parentRoot, 'jupyter', 'kernels');
  const workspacePython = path.join(workspaceRoot, '.venv', 'bin', 'python');
  const capablePython = '/capable/python';
  const brokenPython = '/broken/python';

  try {
    fs.mkdirSync(path.dirname(workspacePython), { recursive: true });
    fs.mkdirSync(path.join(kernelDir, 'python3'), { recursive: true });
    fs.mkdirSync(path.join(kernelDir, 'broken'), { recursive: true });
    fs.writeFileSync(workspacePython, '#!/bin/sh\n');
    fs.writeFileSync(path.join(kernelDir, 'python3', 'kernel.json'), JSON.stringify({
      argv: [capablePython, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
      display_name: 'Python 3 (ipykernel)',
    }));
    fs.writeFileSync(path.join(kernelDir, 'broken', 'kernel.json'), JSON.stringify({
      argv: [brokenPython, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
      display_name: 'Broken Python',
    }));

    const result = discoverKernels(workspaceRoot, {
      kernelDirs: [kernelDir],
      probeCapability: (pythonPath) => pythonPath === capablePython,
    });

    assert.deepEqual(result.kernels, [
      {
        id: capablePython,
        label: 'Python 3 (ipykernel)',
        recommended: false,
      },
    ]);
    assert.equal(result.preferred_kernel, null);
  } finally {
    fs.rmSync(parentRoot, { recursive: true, force: true });
  }
});

test('discoverKernels prefers a kernel-capable workspace venv using its python path as the id', async () => {
  const { discoverKernels } = await import('../scripts/standalone-server.mjs');
  const parentRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-repl-kernels-workspace-'));
  const workspaceRoot = path.join(parentRoot, 'workspace');
  const kernelDir = path.join(parentRoot, 'jupyter', 'kernels');
  const workspacePython = path.join(workspaceRoot, '.venv', 'bin', 'python');

  try {
    fs.mkdirSync(path.dirname(workspacePython), { recursive: true });
    fs.mkdirSync(path.join(kernelDir, 'python3'), { recursive: true });
    fs.writeFileSync(workspacePython, '#!/bin/sh\n');
    fs.writeFileSync(path.join(kernelDir, 'python3', 'kernel.json'), JSON.stringify({
      argv: [workspacePython, '-m', 'ipykernel_launcher', '-f', '{connection_file}'],
      display_name: 'Workspace Python',
    }));

    const result = discoverKernels(workspaceRoot, {
      kernelDirs: [kernelDir],
      probeCapability: (pythonPath) => pythonPath === workspacePython,
    });

    assert.deepEqual(result.preferred_kernel, {
      id: workspacePython,
      label: 'Workspace Python',
    });
    assert.deepEqual(result.kernels[0], {
      id: workspacePython,
      label: 'Workspace Python',
      recommended: true,
    });
  } finally {
    fs.rmSync(parentRoot, { recursive: true, force: true });
  }
});

test('standalone notebook status proxies to the daemon status endpoint', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/status') {
        return {
          status: 'ok',
          running: [{ cell_id: 'cell-1' }],
          queued: [{ cell_id: 'cell-2' }],
        };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.deepEqual(result.body.running, [{ cell_id: 'cell-1' }]);
  assert.deepEqual(result.body.queued, [{ cell_id: 'cell-2' }]);
  assert.equal(fetchCalls.at(-1)?.url, 'http://daemon/api/notebooks/status');
});

test('standalone re-resolves a cached daemon session after fetch failures', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let activeDaemon = 'stale';
  let staleStatusCalls = 0;
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({
      baseUrl: activeDaemon === 'stale' ? 'http://stale-daemon' : 'http://fresh-daemon',
      token: activeDaemon === 'stale' ? 'stale-token' : 'fresh-token',
    }),
    fetchJson: async (url, init) => {
      fetchCalls.push({ url, init });
      if (url.endsWith('/api/health')) {
        return { status: 'ok' };
      }
      if (url.endsWith('/api/sessions/resolve')) {
        return {
          status: 'ok',
          session: {
            session_id: activeDaemon === 'stale' ? 'sess-stale' : 'sess-fresh',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://stale-daemon/api/notebooks/status') {
        staleStatusCalls += 1;
        if (staleStatusCalls === 1) {
          return { status: 'ok', running: [], queued: [] };
        }
        throw new Error('fetch failed');
      }
      if (url === 'http://fresh-daemon/api/notebooks/status') {
        return { status: 'ok', running: [{ cell_id: 'fresh-cell' }], queued: [] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    },
  });

  const first = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-reconnect',
    path: 'tmp/demo.ipynb',
  });
  assert.equal(first.statusCode, 200);
  assert.deepEqual(first.body, { status: 'ok', running: [], queued: [] });

  activeDaemon = 'fresh';
  const second = await invokeApi(services, '/api/standalone/notebook/status', {
    client_id: 'client-reconnect',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(second.statusCode, 200);
  assert.deepEqual(second.body, { status: 'ok', running: [{ cell_id: 'fresh-cell' }], queued: [] });
  assert.deepEqual(
    fetchCalls.map((call) => call.url),
    [
      'http://stale-daemon/api/health',
      'http://stale-daemon/api/sessions/resolve',
      'http://stale-daemon/api/notebooks/status',
      'http://stale-daemon/api/health',
      'http://stale-daemon/api/notebooks/status',
      'http://fresh-daemon/api/health',
      'http://fresh-daemon/api/sessions/resolve',
      'http://fresh-daemon/api/notebooks/status',
    ],
  );
});

test('standalone shared-model proxies to the daemon shared-model endpoint', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/shared-model') {
        return {
          path: path.join('tmp', 'demo.ipynb'),
          document_version: 4,
          cells: [{ cell_id: 'cell-1', cell_type: 'code', source: 'x = 1', outputs: [], metadata: {} }],
        };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/shared-model', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.document_version, 4);
  assert.equal(result.body.cells[0].source, 'x = 1');
  assert.equal(fetchCalls.at(-1)?.url, 'http://daemon/api/notebooks/shared-model');
});

test('standalone notebook trust proxies to the daemon trust endpoint', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/trust') {
        return { status: 'ok', path: 'tmp/demo.ipynb', notebook_trusted: true };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/trust', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.notebook_trusted, true);
  assert.equal(fetchCalls.at(-1)?.url, 'http://daemon/api/notebooks/trust');
});

test('standalone notebook trust restarts onto the current daemon when the existing daemon is missing the route', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let trustCalls = 0;
  const runCommands = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    runAgentRepl: async (args) => {
      runCommands.push(args.join(' '));
      return { status: 'ok', session: { session_id: 'sess-browser' } };
    },
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/trust') {
        trustCalls += 1;
        if (trustCalls === 1) {
          const error = new Error('Not Found');
          error.statusCode = 404;
          throw error;
        }
        return { status: 'ok', path: 'tmp/demo.ipynb', notebook_trusted: true };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/trust', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.notebook_trusted, true);
  assert.deepEqual(runCommands, [
    'core stop --workspace-root /workspace',
    'core start --workspace-root /workspace',
  ]);
  assert.equal(trustCalls, 2);
});

test('standalone shared-model falls back to legacy contents when the daemon route is unavailable', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/shared-model') {
        const error = new Error('Not Found');
        error.statusCode = 404;
        throw error;
      }
      if (url === 'http://daemon/api/notebooks/contents') {
        return {
          path: path.join('tmp', 'demo.ipynb'),
          cells: [{ cell_id: 'cell-1', cell_type: 'code', source: 'legacy', outputs: [], metadata: {} }],
        };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/shared-model', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.cells[0].source, 'legacy');
  assert.equal(typeof result.body.document_version, 'number');
  assert.deepEqual(
    fetchCalls
      .filter((call) => call.url.startsWith('http://daemon/api/notebooks/'))
      .map((call) => call.url),
    ['http://daemon/api/notebooks/shared-model', 'http://daemon/api/notebooks/contents'],
  );
});

test('standalone shared-model restarts onto the current daemon when the existing daemon hits the sqlite thread error', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  let sharedModelCalls = 0;
  const runCommands = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    runAgentRepl: async (args) => {
      runCommands.push(args.join(' '));
      return { status: 'ok', session: { session_id: 'sess-browser' } };
    },
    fetchJson: withHealthyDaemon(async (url) => {
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/shared-model') {
        sharedModelCalls += 1;
        if (sharedModelCalls === 1) {
          const error = new Error('SQLite objects created in a thread can only be used in that same thread.');
          error.statusCode = 500;
          throw error;
        }
        return { path: 'tmp/demo.ipynb', document_version: 3, cells: [] };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/shared-model', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.document_version, 3);
  assert.deepEqual(runCommands, [
    'core stop --workspace-root /workspace',
    'core start --workspace-root /workspace',
  ]);
  assert.equal(sharedModelCalls, 2);
});

test('standalone execute-all forwards the browser owner session to the daemon', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/execute-all') {
        return { status: 'ok' };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/execute-all', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.status, 'ok');
  const executeCall = fetchCalls.find((call) => call.url === 'http://daemon/api/notebooks/execute-all');
  assert.ok(executeCall);
  assert.equal(
    JSON.parse(executeCall.init.body).owner_session_id,
    'sess-browser',
  );
});

test('standalone restart-and-run-all forwards the browser owner session to the daemon', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/restart-and-run-all') {
        return { status: 'ok' };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/restart-and-run-all', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.status, 'ok');
  const restartCall = fetchCalls.find((call) => call.url === 'http://daemon/api/notebooks/restart-and-run-all');
  assert.ok(restartCall);
  assert.equal(
    JSON.parse(restartCall.init.body).owner_session_id,
    'sess-browser',
  );
});

test('standalone execute-cell async endpoint acknowledges immediately while the daemon request stays in flight', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let resolveExecution;
  const executionPromise = new Promise((resolve) => {
    resolveExecution = resolve;
  });
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/execute-cell') {
        await executionPromise;
        return { status: 'ok', cell_id: 'cell-1' };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/execute-cell-async', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
    cell_id: 'cell-1',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.status, 'started');
  assert.equal(result.body.cell_id, 'cell-1');
  const executeCall = await waitFor(() => fetchCalls.find((call) => call.url === 'http://daemon/api/notebooks/execute-cell'));
  assert.ok(executeCall);
  resolveExecution();
  await executionPromise;
});

test('standalone restart-and-run-all async endpoint acknowledges immediately while the daemon request stays in flight', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  let resolveExecution;
  const executionPromise = new Promise((resolve) => {
    resolveExecution = resolve;
  });
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: withHealthyDaemon(async (url, init) => {
      fetchCalls.push({ url, init });
      if (url === 'http://daemon/api/sessions/resolve') {
        return {
          status: 'ok',
          session: {
            session_id: 'sess-browser',
            actor: 'human',
            client: 'browser',
            status: 'attached',
            capabilities: ['projection', 'presence'],
            last_seen_at: 42,
            created_at: 24,
          },
        };
      }
      if (url === 'http://daemon/api/notebooks/restart-and-run-all') {
        await executionPromise;
        return { status: 'ok' };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    }),
  });

  const result = await invokeApi(services, '/api/standalone/notebook/restart-and-run-all-async', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.status, 'started');
  const restartCall = await waitFor(() => fetchCalls.find((call) => call.url === 'http://daemon/api/notebooks/restart-and-run-all'));
  assert.ok(restartCall);
  resolveExecution();
  await executionPromise;
});

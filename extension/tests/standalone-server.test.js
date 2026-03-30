const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const test = require('node:test');

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
  const { normalizeNotebookPath } = await import('../scripts/standalone-server.mjs');
  const workspaceRoot = path.resolve('/tmp/agent-repl-workspace');

  assert.throws(
    () => normalizeNotebookPath(workspaceRoot, '../outside.ipynb'),
    /inside the workspace root/,
  );
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
    fetchJson: async (url, init) => {
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
    },
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
  assert.equal(fetchCalls.length, 1);
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

test('standalone notebook status proxies to the daemon status endpoint', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: async (url, init) => {
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
    },
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

test('standalone execute-all forwards the browser owner session to the daemon', async () => {
  const { createStandaloneServices } = await import('../scripts/standalone-server.mjs');
  const fetchCalls = [];
  const services = createStandaloneServices({
    repoRoot: '/repo',
    workspaceRoot: '/workspace',
    extensionRoot: '/extension',
    locateDaemon: () => ({ baseUrl: 'http://daemon', token: 'secret' }),
    fetchJson: async (url, init) => {
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
    },
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
    fetchJson: async (url, init) => {
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
    },
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
    fetchJson: async (url, init) => {
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
    },
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
  const executeCall = fetchCalls.find((call) => call.url === 'http://daemon/api/notebooks/execute-cell');
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
    fetchJson: async (url, init) => {
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
    },
  });

  const result = await invokeApi(services, '/api/standalone/notebook/restart-and-run-all-async', {
    client_id: 'client-1',
    path: 'tmp/demo.ipynb',
  });

  assert.equal(result.handled, true);
  assert.equal(result.statusCode, 200);
  assert.equal(result.body.status, 'started');
  const restartCall = fetchCalls.find((call) => call.url === 'http://daemon/api/notebooks/restart-and-run-all');
  assert.ok(restartCall);
  resolveExecution();
  await executionPromise;
});

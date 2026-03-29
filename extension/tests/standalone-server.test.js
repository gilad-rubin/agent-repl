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

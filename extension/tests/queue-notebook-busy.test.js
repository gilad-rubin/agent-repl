const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadQueueModule(vscode, overrides = {}) {
    const modulePath = path.resolve(__dirname, '../out/execution/queue.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
        }
        if (request === '../notebook/identity') {
            return {
                resolveCell: () => 0,
                getCellId: () => 'cell-1',
            };
        }
        if (request === '../notebook/outputs') {
            return {
                AGENT_REPL_OUTPUT_METADATA_KEY: 'agent-repl-output',
                toJupyter: () => [],
                stripForAgent: value => value,
                toVSCode: value => value,
            };
        }
        if (request === '../notebook/resolver') {
            return {
                resolveNotebook: overrides.resolveNotebook ?? (() => overrides.doc),
            };
        }
        if (request === '../session') {
            return {
                discoverDaemon: () => overrides.daemon ?? { url: 'http://127.0.0.1:9999', token: 'tok' },
                daemonPost: overrides.daemonPost ?? (async () => ({ status: 'ok' })),
                workspaceRootForPath: () => '/workspace',
                sessionIdForWorkspaceState: () => undefined,
            };
        }
        return originalLoad.call(this, request, parent, isMain);
    };

    delete require.cache[modulePath];
    try {
        return require(modulePath);
    } finally {
        Module._load = originalLoad;
    }
}

test('status is tracked per notebook path rather than by global busy state', { concurrency: false }, async () => {
    const docs = new Map([
        ['/workspace/a.ipynb', { uri: { fsPath: '/workspace/a.ipynb' }, cellAt: () => ({ document: { getText: () => 'print(1)' } }) }],
        ['/workspace/b.ipynb', { uri: { fsPath: '/workspace/b.ipynb' }, cellAt: () => ({ document: { getText: () => 'print(2)' } }) }],
    ]);

    let resolveA;
    const aBlocker = new Promise((r) => { resolveA = r; });

    const queue = loadQueueModule(
        {
            workspace: { getConfiguration: () => ({ get: (_n, fb) => fb }) },
            extensions: { getExtension: () => undefined },
        },
        {
            resolveNotebook: (targetPath) => docs.get(targetPath),
            daemonPost: async (_daemon, _endpoint, body) => {
                // Block execution of notebook A to simulate busy state.
                if (body.path === 'a.ipynb') {
                    await aBlocker;
                }
                return { status: 'ok' };
            },
        },
    );

    queue.resetExecutionState();

    // Start execution on A (will block, marking A as busy).
    const aPromise = queue.startExecution('/workspace/a.ipynb', { cell_id: 'cell-1' }, 20);

    // B should not be busy.
    const bStatus = await queue.getStatus('/workspace/b.ipynb');
    assert.equal(bStatus.busy, false);
    assert.equal(bStatus.kernel_state, 'idle');

    resolveA();
    await aPromise;
});

test('shared-kernel queue reports daemon-routed running work', { concurrency: false }, async () => {
    const doc = {
        uri: { fsPath: '/workspace/a.ipynb' },
        cellAt: () => ({ document: { getText: () => 'print(1)' } }),
    };

    let resolveExec;
    const execBlocker = new Promise((r) => { resolveExec = r; });

    const queue = loadQueueModule(
        {
            workspace: { getConfiguration: () => ({ get: (_n, fb) => fb }) },
            extensions: { getExtension: () => undefined },
        },
        {
            doc,
            daemonPost: async () => {
                await execBlocker;
                return { status: 'ok' };
            },
        },
    );

    queue.resetExecutionState();

    // Start an execution (will be marked running immediately).
    const result = await queue.startExecution('/workspace/a.ipynb', { cell_id: 'cell-1' }, 20);
    assert.equal(result.status, 'started');

    // Status should show busy.
    const status = await queue.getStatus('/workspace/a.ipynb');
    assert.equal(status.busy, true);
    assert.equal(status.running.length, 1);
    assert.equal(status.running[0].cell_id, 'cell-1');

    resolveExec();
    await new Promise((r) => setTimeout(r, 50));
});

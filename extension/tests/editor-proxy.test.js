const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadProxyModule() {
    const modulePath = path.resolve(__dirname, '../out/editor/proxy.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                workspace: {
                    getWorkspaceFolder() {
                        return { uri: { fsPath: '/workspace' } };
                    },
                    getConfiguration() {
                        return { get() { return ''; } };
                    },
                },
                Disposable: class Disposable {
                    dispose() {}
                },
            };
        }
        if (request.endsWith('/session')) {
            return {
                coreCliPlans() { return []; },
                sessionIdForWorkspaceState() { return 'session-1'; },
            };
        }
        if (request.endsWith('/lsp')) {
            return {
                PyrightNotebookLspClient: class {
                    dispose() {}
                },
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

const { DaemonProxy } = loadProxyModule();

test('loadContents includes the active notebook path in the contents message', async () => {
    const postedMessages = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        assert.equal(endpoint, '/api/notebooks/contents');
        assert.deepEqual(body, { path: 'notebooks/test3.ipynb' });
        return {
            cells: [{
                index: 0,
                cell_id: 'cell-1',
                cell_type: 'code',
                source: 'print("hello")',
                outputs: [],
                execution_count: null,
                display_number: null,
            }],
        };
    };
    proxy.syncLsp = () => {};

    await proxy.loadContents('req-contents');

    assert.equal(postedMessages.length, 1);
    assert.equal(postedMessages[0].type, 'contents');
    assert.equal(postedMessages[0].requestId, 'req-contents');
    assert.equal(postedMessages[0].path, 'notebooks/test3.ipynb');
});

test('handleExecuteCell persists a source override before starting the cell', async () => {
    const postedMessages = [];
    const httpCalls = [];
    const runtimeRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/execute-cell') {
            return { status: 'started', execution_id: 'exec-1', cell_id: 'cell-1' };
        }
        return { status: 'ok', cells: [] };
    };
    proxy.loadContents = async () => {};
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };
    proxy.syncLsp = () => {};

    await proxy.handleExecuteCell({
        requestId: 'req-1',
        cell_id: 'cell-1',
        source: 'os.getcwd()',
    });

    assert.equal(httpCalls.length, 2);
    assert.equal(httpCalls[0].endpoint, '/api/notebooks/edit');
    assert.deepEqual(httpCalls[0].body.operations, [
        { op: 'replace-source', cell_id: 'cell-1', source: 'os.getcwd()' },
    ]);
    assert.equal(httpCalls[1].endpoint, '/api/notebooks/execute-cell');
    assert.equal(httpCalls[1].body.cell_id, 'cell-1');
    assert.equal(httpCalls[1].body.owner_session_id, 'session-1');
    assert.equal(postedMessages[0].type, 'execute-started');
    assert.equal(postedMessages[0].execution_id, 'exec-1');
    assert.equal(postedMessages.length, 1);
    assert.deepEqual(runtimeRequests, ['execute-started-runtime']);
});

test('handleExecuteCell leaves a queued cell queued until activity marks it running', async () => {
    const postedMessages = [];
    const runtimeRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint) => {
        if (endpoint === '/api/notebooks/execute-cell') {
            return { status: 'queued', execution_id: 'exec-2', cell_id: 'cell-2' };
        }
        return { status: 'ok', cells: [] };
    };
    proxy.loadContents = async () => {};
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };
    proxy.syncLsp = () => {};

    await proxy.handleExecuteCell({
        requestId: 'req-2',
        cell_id: 'cell-2',
    });

    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-2' }]);
    assert.deepEqual(runtimeRequests, ['execute-queued-runtime']);
});

test('handleGetRuntime merges notebook status queue data into the runtime message', async () => {
    const postedMessages = [];
    const httpCalls = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/runtime') {
            return {
                active: true,
                runtime: {
                    busy: true,
                    current_execution: { cell_id: 'cell-running' },
                    runtime_id: 'rt-1',
                    kernel_generation: 4,
                },
                runtime_record: { label: 'Notebook Python' },
            };
        }
        if (endpoint === '/api/notebooks/status') {
            return {
                running: [{ run_id: 'run-1', cell_id: 'cell-running' }],
                queued: [{ run_id: 'run-2', cell_id: 'cell-queued', queue_position: 1 }],
            };
        }
        return { status: 'ok' };
    };

    await proxy.handleGetRuntime({ requestId: 'runtime-1' });

    assert.deepEqual(httpCalls.map((call) => call.endpoint), [
        '/api/notebooks/runtime',
        '/api/notebooks/status',
    ]);
    assert.deepEqual(postedMessages, [{
        type: 'runtime',
        requestId: 'runtime-1',
        active: true,
        busy: true,
        kernel_label: 'Notebook Python',
        runtime_id: 'rt-1',
        kernel_generation: 4,
        current_execution: { cell_id: 'cell-running' },
        running_cell_ids: ['cell-running'],
        queued_cell_ids: ['cell-queued'],
    }]);
});

test('handleExecuteCell treats a completed headless execution as finished and refreshes contents', async () => {
    const postedMessages = [];
    const httpCalls = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/execute-cell') {
            return { status: 'ok', cell_id: 'cell-3' };
        }
        return { status: 'ok', cells: [] };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };
    proxy.syncLsp = () => {};

    await proxy.handleExecuteCell({
        requestId: 'req-3',
        cell_id: 'cell-3',
    });

    assert.equal(httpCalls.length, 1);
    assert.equal(httpCalls[0].endpoint, '/api/notebooks/execute-cell');
    assert.deepEqual(postedMessages, [{
        type: 'execute-finished',
        requestId: 'req-3',
        cell_id: 'cell-3',
        ok: true,
    }]);
    assert.deepEqual(loadRequests, ['execute-finished']);
    assert.deepEqual(runtimeRequests, ['execute-finished-runtime']);
});

test('handleExecuteAll includes the owner session id', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        return { status: 'ok' };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };

    await proxy.handleExecuteAll({ requestId: 'req-3' });
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(httpCalls.length, 1);
    assert.equal(httpCalls[0].endpoint, '/api/notebooks/execute-all');
    assert.deepEqual(httpCalls[0].body, {
        path: 'notebooks/test3.ipynb',
        owner_session_id: 'session-1',
    });
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-3' }]);
    assert.deepEqual(loadRequests, ['execute-all-finished']);
    assert.deepEqual(runtimeRequests, ['execute-all-runtime']);
});

test('handleRestartAndRunAll includes the owner session id', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        return { status: 'ok' };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };

    await proxy.handleRestartAndRunAll({ requestId: 'req-4' });
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(httpCalls.length, 1);
    assert.equal(httpCalls[0].endpoint, '/api/notebooks/restart-and-run-all');
    assert.deepEqual(httpCalls[0].body, {
        path: 'notebooks/test3.ipynb',
        owner_session_id: 'session-1',
    });
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-4' }]);
    assert.deepEqual(loadRequests, ['restart-and-run-all-finished']);
    assert.deepEqual(runtimeRequests, ['restart-and-run-all-runtime']);
});

test('handleExecuteAll does not surface a refresh failure after the run starts', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        return { status: 'ok' };
    };
    proxy.loadContents = async () => {
        throw new Error('refresh failed');
    };
    proxy.handleGetRuntime = async () => {};

    await proxy.handleExecuteAll({ requestId: 'req-5' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(httpCalls.length, 1);
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-5' }]);
});

test('handleRestartAndRunAll does not surface a refresh failure after the run starts', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        return { status: 'ok' };
    };
    proxy.loadContents = async () => {
        throw new Error('refresh failed');
    };
    proxy.handleGetRuntime = async () => {};

    await proxy.handleRestartAndRunAll({ requestId: 'req-6' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(httpCalls.length, 1);
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-6' }]);
});

test('handleExecuteAll falls back to owned cell execution on a self lease conflict', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.cells = [
        { cell_id: 'cell-1', cell_type: 'code', index: 0, source: '', outputs: [], execution_count: null, display_number: null },
        { cell_id: 'cell-2', cell_type: 'markdown', index: 1, source: '', outputs: [], execution_count: null, display_number: null },
        { cell_id: 'cell-3', cell_type: 'code', index: 2, source: '', outputs: [], execution_count: null, display_number: null },
    ];
    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/execute-all') {
            const error = new Error('self lease conflict');
            error.conflict = true;
            error.payload = {
                conflict: {
                    holder: { session_id: 'session-1' },
                    lease: { session_id: 'session-1' },
                },
            };
            throw error;
        }
        return { status: 'ok' };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };

    await proxy.handleExecuteAll({ requestId: 'req-7' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(
        httpCalls.map((call) => call.endpoint),
        ['/api/notebooks/execute-all', '/api/notebooks/execute-cell', '/api/notebooks/execute-cell'],
    );
    assert.equal(httpCalls[1].body.owner_session_id, 'session-1');
    assert.equal(httpCalls[1].body.cell_id, 'cell-1');
    assert.equal(httpCalls[2].body.cell_id, 'cell-3');
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-7' }]);
    assert.deepEqual(loadRequests, ['execute-all-finished']);
    assert.deepEqual(runtimeRequests, ['execute-all-runtime']);
});

test('handleExecuteAll fallback stops after the first failed cell', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.cells = [
        { cell_id: 'cell-1', cell_type: 'code', index: 0, source: '', outputs: [], execution_count: null, display_number: null },
        { cell_id: 'cell-2', cell_type: 'code', index: 1, source: '', outputs: [], execution_count: null, display_number: null },
        { cell_id: 'cell-3', cell_type: 'code', index: 2, source: '', outputs: [], execution_count: null, display_number: null },
    ];
    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/execute-all') {
            const error = new Error('self lease conflict');
            error.conflict = true;
            error.payload = {
                conflict: {
                    holder: { session_id: 'session-1' },
                    lease: { session_id: 'session-1' },
                },
            };
            throw error;
        }
        if (endpoint === '/api/notebooks/execute-cell' && body.cell_id === 'cell-2') {
            return { status: 'error', cell_id: 'cell-2', error: 'boom' };
        }
        return { status: 'ok' };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };

    await proxy.handleExecuteAll({ requestId: 'req-7b' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(
        httpCalls.map((call) => call.endpoint),
        ['/api/notebooks/execute-all', '/api/notebooks/execute-cell', '/api/notebooks/execute-cell'],
    );
    assert.equal(httpCalls[1].body.cell_id, 'cell-1');
    assert.equal(httpCalls[2].body.cell_id, 'cell-2');
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-7b' }]);
    assert.deepEqual(loadRequests, ['execute-all-finished']);
    assert.deepEqual(runtimeRequests, ['execute-all-runtime']);
});

test('handleRestartAndRunAll falls back to restart then owned cell execution on a self lease conflict', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.cells = [
        { cell_id: 'cell-1', cell_type: 'code', index: 0, source: '', outputs: [], execution_count: null, display_number: null },
    ];
    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/restart-and-run-all') {
            const error = new Error('self lease conflict');
            error.conflict = true;
            error.payload = {
                conflict: {
                    holder: { session_id: 'session-1' },
                    lease: { session_id: 'session-1' },
                },
            };
            throw error;
        }
        return { status: 'ok' };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };

    await proxy.handleRestartAndRunAll({ requestId: 'req-8' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(
        httpCalls.map((call) => call.endpoint),
        ['/api/notebooks/restart-and-run-all', '/api/notebooks/restart', '/api/notebooks/execute-cell'],
    );
    assert.equal(httpCalls[2].body.owner_session_id, 'session-1');
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-8' }]);
    assert.deepEqual(loadRequests, ['restart-and-run-all-finished']);
    assert.deepEqual(runtimeRequests, ['restart-and-run-all-runtime']);
});

test('handleRestartAndRunAll fallback stops after the first failed cell', async () => {
    const httpCalls = [];
    const postedMessages = [];
    const runtimeRequests = [];
    const loadRequests = [];
    const proxy = new DaemonProxy(
        { fsPath: '/workspace/notebooks/test3.ipynb' },
        { webview: { postMessage(message) { postedMessages.push(message); } } },
        {
            extensionPath: '/extension',
            workspaceState: {
                get() { return 'session-1'; },
            },
        },
    );

    proxy.cells = [
        { cell_id: 'cell-1', cell_type: 'code', index: 0, source: '', outputs: [], execution_count: null, display_number: null },
        { cell_id: 'cell-2', cell_type: 'code', index: 1, source: '', outputs: [], execution_count: null, display_number: null },
        { cell_id: 'cell-3', cell_type: 'code', index: 2, source: '', outputs: [], execution_count: null, display_number: null },
    ];
    proxy.httpPost = async (endpoint, body) => {
        httpCalls.push({ endpoint, body });
        if (endpoint === '/api/notebooks/restart-and-run-all') {
            const error = new Error('self lease conflict');
            error.conflict = true;
            error.payload = {
                conflict: {
                    holder: { session_id: 'session-1' },
                    lease: { session_id: 'session-1' },
                },
            };
            throw error;
        }
        if (endpoint === '/api/notebooks/execute-cell' && body.cell_id === 'cell-2') {
            return { status: 'error', cell_id: 'cell-2', error: 'boom' };
        }
        return { status: 'ok' };
    };
    proxy.loadContents = async (requestId) => {
        loadRequests.push(requestId);
    };
    proxy.handleGetRuntime = async (msg) => {
        runtimeRequests.push(msg.requestId);
    };

    await proxy.handleRestartAndRunAll({ requestId: 'req-8b' });
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(
        httpCalls.map((call) => call.endpoint),
        ['/api/notebooks/restart-and-run-all', '/api/notebooks/restart', '/api/notebooks/execute-cell', '/api/notebooks/execute-cell'],
    );
    assert.equal(httpCalls[2].body.cell_id, 'cell-1');
    assert.equal(httpCalls[3].body.cell_id, 'cell-2');
    assert.deepEqual(postedMessages, [{ type: 'ok', requestId: 'req-8b' }]);
    assert.deepEqual(loadRequests, ['restart-and-run-all-finished']);
    assert.deepEqual(runtimeRequests, ['restart-and-run-all-runtime']);
});

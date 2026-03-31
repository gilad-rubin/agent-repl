const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadSessionModule(workspaceFolders = [], options = {}) {
    const modulePath = path.resolve(__dirname, '../out/session.js');
    const originalLoad = Module._load;
    const execCalls = [];
    const httpCalls = [];
    const execResponses = options.execResponses || {};
    const httpResponses = options.httpResponses || {};
    const daemonInfo = options.daemonInfo || null;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                workspace: {
                    workspaceFolders,
                    getConfiguration: () => ({ get: (_name, fallback) => fallback }),
                },
                env: { appName: 'VS Code' },
            };
        }
        if (request === 'child_process') {
            return {
                execFile: (...args) => args,
            };
        }
        if (request === 'http') {
            return {
                request: (url, opts, callback) => {
                    const endpoint = typeof url === 'string' ? url : (url.pathname || url.toString());
                    httpCalls.push({ url: endpoint, method: opts?.method, body: null });
                    const entry = httpCalls[httpCalls.length - 1];
                    const req = {
                        on: (event, handler) => {
                            if (event === 'error') req._errorHandler = handler;
                            if (event === 'timeout') req._timeoutHandler = handler;
                        },
                        write: (data) => { entry.body = JSON.parse(data); },
                        end: () => {
                            const key = _httpResponseKey(endpoint);
                            const configured = httpResponses[key] || httpResponses.default || { status: 'ok' };
                            const payload = Array.isArray(configured) ? configured.shift() : configured;
                            const resData = JSON.stringify(payload);
                            const res = {
                                statusCode: 200,
                                on: (event, handler) => {
                                    if (event === 'data') handler(Buffer.from(resData));
                                    if (event === 'end') handler();
                                },
                            };
                            callback(res);
                        },
                        destroy: () => {},
                    };
                    return req;
                },
            };
        }
        if (request === 'util') {
            return {
                promisify: () => async (command, args) => {
                    execCalls.push([command, args]);
                    const key = args.includes('session-resolve')
                        ? 'session-resolve'
                        : args.includes('attach')
                            ? 'default'
                            : 'default';
                    const configured = execResponses[key] || execResponses.default || { status: 'ok' };
                    const payload = Array.isArray(configured) ? configured.shift() : configured;
                    return { stdout: JSON.stringify(payload) };
                },
            };
        }
        return originalLoad.call(this, request, parent, isMain);
    };

    delete require.cache[modulePath];
    try {
        const mod = require(modulePath);
        mod._testDaemonDiscovery = daemonInfo ? () => daemonInfo : undefined;
        return {
            module: mod,
            execCalls,
            httpCalls,
        };
    } finally {
        Module._load = originalLoad;
    }
}

function _httpResponseKey(endpoint) {
    if (endpoint.includes('/sessions/resolve')) return 'session-resolve';
    if (endpoint.includes('/sessions/start')) return 'session-start';
    if (endpoint.includes('/sessions/touch')) return 'session-touch';
    if (endpoint.includes('/sessions/detach')) return 'session-detach';
    return 'default';
}

test('coreCliPlans prefers uv run when a pyproject exists in the workspace root', () => {
    const originalExistsSync = fs.existsSync;
    fs.existsSync = (target) => target === '/workspace/pyproject.toml';

    try {
        const { module: { coreCliPlans } } = loadSessionModule();
        const plans = coreCliPlans('/workspace', { get: () => undefined });
        assert.deepEqual(plans[0], { command: 'uv', args: ['run', 'agent-repl'], cwd: '/workspace' });
        assert.deepEqual(plans[1], { command: 'agent-repl', args: [], cwd: '/workspace' });
    } finally {
        fs.existsSync = originalExistsSync;
    }
});

test('coreCliPlans prefers configured and workspace-local launchers before PATH fallbacks', () => {
    const originalExistsSync = fs.existsSync;
    fs.existsSync = (target) => (
        target === '/workspace/.venv/bin/agent-repl' ||
        target === '/workspace/pyproject.toml'
    );

    try {
        const { module: { coreCliPlans } } = loadSessionModule();
        const plans = coreCliPlans('/workspace', { get: () => '/custom/agent-repl' });
        assert.deepEqual(plans[0], { command: '/custom/agent-repl', args: [], cwd: '/workspace' });
        assert.deepEqual(plans[1], { command: '/workspace/.venv/bin/agent-repl', args: [], cwd: '/workspace' });
        assert.deepEqual(plans[2], { command: 'uv', args: ['run', 'agent-repl'], cwd: '/workspace' });
        assert.deepEqual(plans[3], { command: 'agent-repl', args: [], cwd: '/workspace' });
    } finally {
        fs.existsSync = originalExistsSync;
    }
});

test('primaryWorkspaceRoot returns the first workspace folder path', () => {
    const { module: { primaryWorkspaceRoot } } = loadSessionModule([{ uri: { fsPath: '/workspace' } }]);
    assert.equal(primaryWorkspaceRoot(), '/workspace');
});

test('SessionAutoAttach reuses the preferred human session when no workspace session is stored', async () => {
    const store = new Map();
    const context = {
        workspaceState: {
            get: (key) => store.get(key),
            update: async (key, value) => {
                if (typeof value === 'undefined') {
                    store.delete(key);
                } else {
                    store.set(key, value);
                }
            },
        },
    };
    const {
        module: { SessionAutoAttach },
        execCalls,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'session-resolve': {
                    status: 'ok',
                    session: {
                        session_id: 'sess-vscode',
                        actor: 'human',
                        client: 'vscode',
                        status: 'attached',
                        capabilities: ['projection', 'editor', 'presence'],
                        last_seen_at: 9,
                        created_at: 2,
                    },
                },
                default: { status: 'ok', session: { session_id: 'sess-vscode' } },
            },
        },
    );
    const attach = new SessionAutoAttach(context);
    try {
        await attach.attachIfEnabled({ get: (_name, fallback) => fallback });
        assert.deepEqual(execCalls[0][1], ['core', 'session-resolve', '--workspace-root', '/workspace']);
        assert.ok(execCalls[1][1].includes('--session-id'));
        assert.ok(execCalls[1][1].includes('sess-vscode'));
        assert.equal(store.get('agent-repl.session:/workspace'), 'sess-vscode');
    } finally {
        await attach.detachIfAttached();
        attach.dispose();
    }
});

test('SessionAutoAttach resolves the preferred session via daemon HTTP when the daemon is available', async () => {
    const store = new Map();
    const context = {
        workspaceState: {
            get: (key) => store.get(key),
            update: async (key, value) => {
                if (typeof value === 'undefined') {
                    store.delete(key);
                } else {
                    store.set(key, value);
                }
            },
        },
    };
    const {
        module: { SessionAutoAttach },
        execCalls,
        httpCalls,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            daemonInfo: { url: 'http://127.0.0.1:9999', token: 'test-token' },
            httpResponses: {
                'session-resolve': {
                    status: 'ok',
                    session: {
                        session_id: 'sess-http',
                        actor: 'human',
                        client: 'vscode',
                        status: 'attached',
                        capabilities: ['projection', 'editor', 'presence'],
                        last_seen_at: 9,
                        created_at: 2,
                    },
                },
                'session-start': {
                    status: 'ok',
                    session: { session_id: 'sess-http' },
                },
                'session-detach': { status: 'ok' },
            },
        },
    );
    const daemonDiscovery = () => ({ url: 'http://127.0.0.1:9999', token: 'test-token' });
    const attach = new SessionAutoAttach(context, daemonDiscovery);
    try {
        await attach.attachIfEnabled({ get: (_name, fallback) => fallback });
        // Should use HTTP, not CLI, for resolve + start
        assert.equal(execCalls.length, 0);
        assert.ok(httpCalls.some(c => c.url.includes('/sessions/resolve')));
        assert.ok(httpCalls.some(c => c.url.includes('/sessions/start')));
        const startCall = httpCalls.find(c => c.url.includes('/sessions/start'));
        assert.equal(startCall.body.session_id, 'sess-http');
        assert.equal(startCall.body.actor, 'human');
        assert.equal(startCall.body.client, 'vscode');
        assert.deepEqual(startCall.body.capabilities, ['projection', 'editor', 'presence']);
        assert.equal(store.get('agent-repl.session:/workspace'), 'sess-http');
    } finally {
        await attach.detachIfAttached();
        attach.dispose();
    }
    // Detach should also use HTTP
    assert.ok(httpCalls.some(c => c.url.includes('/sessions/detach')));
});

test('HeadlessNotebookProjection was removed — session.ts exports only session management', () => {
    const { module } = loadSessionModule([{ uri: { fsPath: '/workspace' } }]);
    assert.equal(typeof module.SessionAutoAttach, 'function');
    assert.equal(typeof module.discoverDaemon, 'function');
    assert.equal(typeof module.daemonPost, 'function');
    assert.equal(typeof module.coreCliPlans, 'function');
    // HeadlessNotebookProjection and PROJECTION_CONTROLLER_ID must not exist
    assert.equal(module.HeadlessNotebookProjection, undefined);
    assert.equal(module.PROJECTION_CONTROLLER_ID, undefined);
});

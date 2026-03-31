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
    const commandCalls = [];
    const affinityCalls = [];
    const activityEvents = [];
    const executions = [];
    const docsByPath = new Map();
    const notebookEdits = [];
    let createdController;
    const execResponses = options.execResponses || {};
    const httpResponses = options.httpResponses || {};
    const daemonInfo = options.daemonInfo || null;
    const activeNotebookEditor = options.activeNotebookEditor;
    const visibleNotebookEditors = options.visibleNotebookEditors || [];
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            class WorkspaceEdit {
                constructor() {
                    this.entries = [];
                }
                set(uri, edits) {
                    this.entries.push({ uri, edits });
                }
            }

            class NotebookRange {
                constructor(start, end) {
                    this.start = start;
                    this.end = end;
                }
            }

            class NotebookCellData {
                constructor(kind, value, languageId) {
                    this.kind = kind;
                    this.value = value;
                    this.languageId = languageId;
                    this.metadata = {};
                    this.outputs = [];
                }
            }

            return {
                workspace: {
                    workspaceFolders,
                    getConfiguration: () => ({ get: (_name, fallback) => fallback }),
                    onDidOpenNotebookDocument: () => ({ dispose() {} }),
                    onDidCloseNotebookDocument: () => ({ dispose() {} }),
                    onDidSaveNotebookDocument: () => ({ dispose() {} }),
                    applyEdit: async (edit) => {
                        for (const entry of edit.entries) {
                            const doc = docsByPath.get(entry.uri.fsPath);
                            if (!doc) {
                                continue;
                            }
                            for (const notebookEdit of entry.edits) {
                                notebookEdits.push(notebookEdit);
                                if (notebookEdit.kind !== 'replaceCells') {
                                    continue;
                                }
                                const replacementCells = notebookEdit.cells.map((cellData) => ({
                                    kind: cellData.kind,
                                    document: {
                                        getText: () => cellData.value,
                                        languageId: cellData.languageId,
                                    },
                                    metadata: cellData.metadata ?? {},
                                    outputs: cellData.outputs ?? [],
                                    executionSummary: cellData.executionSummary,
                                    index: -1,
                                }));
                                doc.cells.splice(notebookEdit.range.start, notebookEdit.range.end - notebookEdit.range.start, ...replacementCells);
                                doc.cellCount = doc.cells.length;
                                doc.cellAt = (index) => doc.cells[index];
                                doc.cells.forEach((cell, index) => {
                                    cell.index = index;
                                });
                            }
                        }
                        return true;
                    },
                },
                window: {
                    activeNotebookEditor,
                    visibleNotebookEditors,
                    onDidChangeVisibleNotebookEditors: () => ({ dispose() {} }),
                },
                notebooks: {
                    createNotebookController: (id, notebookType, label) => {
                        createdController = {
                            id,
                            notebookType,
                            label,
                            supportedLanguages: [],
                            description: '',
                            executeHandler: undefined,
                            onDidChangeSelectedNotebooks: () => ({ dispose() {} }),
                            updateNotebookAffinity: (notebook, affinity) => {
                                affinityCalls.push({ notebook, affinity });
                            },
                            createNotebookCellExecution: () => {
                                const record = {
                                    outputs: [],
                                    executionOrder: undefined,
                                    started: false,
                                    ended: false,
                                    success: undefined,
                                    replaceOutput: async (outputs) => {
                                        record.outputs = outputs;
                                    },
                                    start: () => {
                                        record.started = true;
                                    },
                                    end: (success) => {
                                        record.ended = true;
                                        record.success = success;
                                    },
                                };
                                executions.push(record);
                                return record;
                            },
                            dispose() {},
                        };
                        return createdController;
                    },
                },
                WorkspaceEdit,
                NotebookRange,
                NotebookCellData,
                NotebookEdit: {
                    replaceCells: (range, cells) => ({ kind: 'replaceCells', range, cells }),
                },
                commands: {
                    executeCommand: async (...args) => {
                        commandCalls.push(args);
                    },
                },
                env: { appName: 'VS Code' },
                NotebookControllerAffinity: { Preferred: 2 },
                NotebookCellKind: { Markup: 1, Code: 2 },
                NotebookCellOutput: class NotebookCellOutput {
                    constructor(items, metadata) {
                        this.items = items;
                        this.metadata = metadata;
                    }
                },
                NotebookCellOutputItem: {
                    text: (value, mime = 'text/plain') => ({ mime, value }),
                    error: (error) => ({ mime: 'application/vnd.code.notebook.error', error }),
                },
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
                    const chunks = [];
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
        if (request === './routes') {
            return {
                pushActivityEvent: (event) => {
                    activityEvents.push(event);
                },
            };
        }
        if (request === 'util') {
            return {
                promisify: () => async (command, args) => {
                    execCalls.push([command, args]);
                    const key = args.includes('execute-visible-cell')
                        ? 'execute-visible-cell'
                        : args.includes('project-visible-notebook')
                            ? 'project-visible-notebook'
                            : args.includes('notebook-activity')
                                ? 'notebook-activity'
                                : args.includes('notebook-projection')
                                    ? 'notebook-projection'
                                : args.includes('session-resolve')
                                    ? 'session-resolve'
                                : args.includes('sessions')
                                    ? 'sessions'
                                    : args.includes('session-presence-upsert')
                                        ? 'session-presence-upsert'
                                    : args.includes('session-presence-clear')
                                        ? 'session-presence-clear'
                                : args.includes('notebook-runtime')
                                    ? 'notebook-runtime'
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
        // Expose daemonInfo for constructing SessionAutoAttach with injection
        mod._testDaemonDiscovery = daemonInfo ? () => daemonInfo : undefined;
        return {
            module: mod,
            execCalls,
            httpCalls,
            commandCalls,
            affinityCalls,
            activityEvents,
            executions,
            docsByPath,
            notebookEdits,
            getController: () => createdController,
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
    if (endpoint.includes('/notebooks/execute-cell')) return 'execute-cell';
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

test('HeadlessNotebookProjection selects the shared runtime controller when a notebook already has a live headless runtime', async () => {
    const doc = { notebookType: 'jupyter-notebook', uri: { fsPath: '/workspace/notebooks/demo.ipynb' } };
    const editor = { notebook: doc };
    const {
        module: { HeadlessNotebookProjection, PROJECTION_CONTROLLER_ID },
        commandCalls,
        affinityCalls,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        { execResponses: { 'notebook-runtime': { status: 'ok', path: 'notebooks/demo.ipynb', active: true, mode: 'headless', runtime: { python_path: '/opt/miniconda3/bin/python3' } } } },
    );
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl');
    try {
        const attached = await projection.attachNotebookIfRunning(doc, editor);
        assert.equal(attached, true);
        assert.equal(affinityCalls.length, 1);
        assert.equal(commandCalls.length, 1);
        assert.equal(commandCalls[0][0], 'notebook.selectKernel');
        assert.equal(commandCalls[0][1].id, PROJECTION_CONTROLLER_ID);
        assert.equal(commandCalls[0][1].extension, 'agent-repl.agent-repl');
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection syncs agent-created cells and outputs into an already-open notebook', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        saveCalls: 0,
        save: async function save() {
            this.saveCalls += 1;
            return true;
        },
        cells: [
            {
                kind: 2,
                document: { getText: () => 'x = 1\nx', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { HeadlessNotebookProjection },
        docsByPath,
        notebookEdits,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'notebook-projection': {
                    status: 'ok',
                    path: 'notebooks/demo.ipynb',
                    active: true,
                    mode: 'headless',
                    runtime: { busy: false, current_execution: null },
                    contents: {
                        path: 'notebooks/demo.ipynb',
                        cells: [
                            {
                                index: 0,
                                cell_id: 'cell-1',
                                cell_type: 'code',
                                source: 'x = 1\nx',
                                outputs: [{ output_type: 'execute_result', data: { 'text/plain': '1' }, metadata: {} }],
                                execution_count: 1,
                                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                            },
                            {
                                index: 1,
                                cell_id: 'cell-2',
                                cell_type: 'code',
                                source: 'y = x + 1\ny',
                                outputs: [{ output_type: 'execute_result', data: { 'text/plain': '2' }, metadata: {} }],
                                execution_count: 2,
                                metadata: { custom: { 'agent-repl': { cell_id: 'cell-2' } } },
                            },
                        ],
                    },
                },
            },
        },
    );
    docsByPath.set(doc.uri.fsPath, doc);
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl');
    try {
        const changed = await projection.syncNotebookProjection(doc);
        assert.equal(changed, true);
        assert.equal(notebookEdits.length, 1);
        assert.equal(doc.cells.length, 2);
        assert.equal(doc.cells[0].outputs[0].items[0].value, '1');
        assert.equal(doc.cells[1].document.getText(), 'y = x + 1\ny');
        assert.equal(doc.cells[1].outputs[0].items[0].value, '2');
        assert.equal(doc.saveCalls, 0);
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection does not replay remote snapshots over a dirty notebook', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: true,
        saveCalls: 0,
        save: async function save() {
            this.saveCalls += 1;
            return true;
        },
        cells: [
            {
                kind: 2,
                document: { getText: () => 'x = 1\nx', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { HeadlessNotebookProjection },
        docsByPath,
        notebookEdits,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'notebook-projection': {
                    status: 'ok',
                    path: 'notebooks/demo.ipynb',
                    active: true,
                    mode: 'headless',
                    runtime: { busy: false, current_execution: null },
                    contents: {
                        path: 'notebooks/demo.ipynb',
                        cells: [
                            {
                                index: 0,
                                cell_id: 'cell-1',
                                cell_type: 'code',
                                source: 'x = 1\nx',
                                outputs: [],
                                execution_count: null,
                                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                            },
                            {
                                index: 1,
                                cell_id: 'cell-2',
                                cell_type: 'code',
                                source: 'y = x + 1\ny',
                                outputs: [],
                                execution_count: null,
                                metadata: { custom: { 'agent-repl': { cell_id: 'cell-2' } } },
                            },
                        ],
                    },
                },
                'notebook-activity': {
                    status: 'ok',
                    path: 'notebooks/demo.ipynb',
                    cursor: 1,
                    recent_events: [],
                },
            },
        },
    );
    docsByPath.set(doc.uri.fsPath, doc);
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl');
    try {
        const changed = await projection.syncNotebookProjection(doc);
        assert.equal(changed, false);
        assert.equal(notebookEdits.length, 0);
        assert.equal(doc.cells.length, 1);
        assert.equal(doc.cells[0].metadata.custom['agent-repl'].cell_id, 'cell-1');
        assert.equal(doc.saveCalls, 0);
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection shows agent execution as running and resolves it when the runtime goes idle', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        save: async () => true,
        cells: [
            {
                kind: 2,
                document: { getText: () => 'x = 9\nx', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { HeadlessNotebookProjection },
        docsByPath,
        executions,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'notebook-projection': [
                    {
                        status: 'ok',
                        path: 'notebooks/demo.ipynb',
                        active: true,
                        mode: 'headless',
                        runtime: { busy: true, current_execution: { cell_index: 0, cell_id: 'cell-1' } },
                        contents: {
                            path: 'notebooks/demo.ipynb',
                            cells: [
                                {
                                    index: 0,
                                    cell_id: 'cell-1',
                                    cell_type: 'code',
                                    source: 'x = 9\nx',
                                    outputs: [],
                                    execution_count: null,
                                    metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                                },
                            ],
                        },
                    },
                    {
                        status: 'ok',
                        path: 'notebooks/demo.ipynb',
                        active: true,
                        mode: 'headless',
                        runtime: { busy: false, current_execution: null },
                        contents: {
                            path: 'notebooks/demo.ipynb',
                            cells: [
                                {
                                    index: 0,
                                    cell_id: 'cell-1',
                                    cell_type: 'code',
                                    source: 'x = 9\nx',
                                    outputs: [{ output_type: 'execute_result', data: { 'text/plain': '9' }, metadata: {} }],
                                    execution_count: 3,
                                    metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                                },
                            ],
                        },
                    },
                ],
            },
        },
    );
    docsByPath.set(doc.uri.fsPath, doc);
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl');
    try {
        const firstChanged = await projection.syncNotebookProjection(doc);
        assert.equal(firstChanged, true);
        assert.equal(executions.length, 1);
        assert.equal(executions[0].started, true);
        assert.equal(executions[0].ended, false);

        const secondChanged = await projection.syncNotebookProjection(doc);
        assert.equal(secondChanged, true);
        assert.equal(executions[0].ended, true);
        assert.equal(executions[0].success, true);
        assert.equal(executions[0].executionOrder, 3);
        assert.equal(executions[0].outputs[0].items[0].value, '9');
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection executes the visible cell source against the shared runtime', async () => {
    const doc = { notebookType: 'jupyter-notebook', uri: { fsPath: '/workspace/notebooks/demo.ipynb' }, save: async () => true };
    const cell = {
        kind: 2,
        index: 0,
        document: { getText: () => 'x = 9\nx' },
    };
    const {
        module: { HeadlessNotebookProjection },
        execCalls,
        httpCalls,
        executions,
        getController,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'project-visible-notebook': {
                    status: 'ok',
                    cell_count: 1,
                },
            },
            httpResponses: {
                'execute-cell': {
                    status: 'ok',
                    execution_count: 3,
                    outputs: [{ output_type: 'execute_result', data: { 'text/plain': '9' }, metadata: {} }],
                },
            },
        },
    );
    const daemonDiscovery = () => ({ url: 'http://127.0.0.1:9999', token: 'tok' });
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl', daemonDiscovery);
    try {
        const controller = getController();
        await controller.executeHandler([cell], doc, controller);
        // project-visible-notebook still uses CLI
        assert.equal(execCalls.length, 1);
        assert.ok(execCalls[0][1].includes('project-visible-notebook'));
        assert.ok(execCalls[0][1].includes('/workspace/notebooks/demo.ipynb'));
        // execute-cell now routes through daemon HTTP
        const execCall = httpCalls.find(c => c.url.includes('/notebooks/execute-cell'));
        assert.ok(execCall, 'should call daemon execute-cell endpoint');
        assert.equal(execCall.body.cell_index, 0);
        assert.equal(execCall.body.wait, true);
        assert.equal(executions.length, 1);
        assert.equal(executions[0].executionOrder, 3);
        assert.equal(executions[0].success, true);
        assert.equal(executions[0].outputs[0].items[0].value, '9');
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection falls back to the selected first cell when VS Code passes no cells to executeHandler', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        save: async () => true,
    };
    const firstCell = {
        kind: 2,
        index: 0,
        document: { getText: () => 'x = 1\nx' },
        metadata: {},
        outputs: [],
        executionSummary: null,
    };
    const secondCell = {
        kind: 2,
        index: 1,
        document: { getText: () => 'x = 2\nx' },
        metadata: {},
        outputs: [],
        executionSummary: null,
    };
    doc.cells = [firstCell, secondCell];
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const editor = {
        notebook: doc,
        selections: [{ start: 0, end: 1 }],
    };
    const {
        module: { HeadlessNotebookProjection },
        execCalls,
        httpCalls,
        executions,
        getController,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            activeNotebookEditor: editor,
            visibleNotebookEditors: [editor],
            execResponses: {
                'project-visible-notebook': {
                    status: 'ok',
                    cell_count: 2,
                },
            },
            httpResponses: {
                'execute-cell': {
                    status: 'ok',
                    execution_count: 1,
                    outputs: [{ output_type: 'execute_result', data: { 'text/plain': '1' }, metadata: {} }],
                },
            },
        },
    );
    const daemonDiscovery = () => ({ url: 'http://127.0.0.1:9999', token: 'tok' });
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl', daemonDiscovery);
    try {
        const controller = getController();
        await controller.executeHandler([], doc, controller);
        // project-visible-notebook still uses CLI
        assert.equal(execCalls.length, 1);
        assert.ok(execCalls[0][1].includes('project-visible-notebook'));
        // execute-cell routes through daemon HTTP; verify cell_index 0 (first selected cell)
        const execCall = httpCalls.find(c => c.url.includes('/notebooks/execute-cell'));
        assert.ok(execCall, 'should call daemon execute-cell endpoint');
        assert.equal(execCall.body.cell_index, 0);
        assert.equal(execCall.body.wait, true);
        assert.equal(executions.length, 1);
        assert.equal(executions[0].success, true);
        assert.equal(executions[0].outputs[0].items[0].value, '1');
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection syncNotebookProjection does not poll activity (activity arrives via WS push)', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        save: async () => true,
        cells: [
            {
                kind: 2,
                document: { getText: () => 'x = 1\nx', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { HeadlessNotebookProjection },
        docsByPath,
        execCalls,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'notebook-projection': {
                    status: 'ok',
                    path: 'notebooks/demo.ipynb',
                    active: true,
                    mode: 'headless',
                    runtime: { busy: false, current_execution: null },
                    contents: {
                        path: 'notebooks/demo.ipynb',
                        cells: [
                            {
                                index: 0,
                                cell_id: 'cell-1',
                                cell_type: 'code',
                                source: 'x = 1\nx',
                                outputs: [],
                                execution_count: null,
                                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                            },
                        ],
                    },
                },
            },
        },
    );
    docsByPath.set(doc.uri.fsPath, doc);
    const context = {
        workspaceState: {
            get: (key) => key === 'agent-repl.session:/workspace' ? 'sess-1' : undefined,
            update: async () => {},
        },
    };
    const projection = new HeadlessNotebookProjection(context, 'agent-repl.agent-repl');
    try {
        await projection.syncNotebookProjection(doc);
        // Activity polling was removed — syncNotebookProjection should NOT call
        // notebook-activity or session-presence-upsert. Activity events arrive via WS push.
        assert.ok(!execCalls.some(([, args]) => args.includes('notebook-activity')),
            'should not call notebook-activity (removed in favor of WS push)');
        assert.ok(!execCalls.some(([, args]) => args.includes('session-presence-upsert')),
            'should not call session-presence-upsert (removed in favor of WS push)');
        // notebook-projection is still called for snapshot sync
        assert.ok(execCalls.some(([, args]) => args.includes('notebook-projection')));
    } finally {
        projection.dispose();
    }
});

test('applyIncrementalActivityEvents applies incremental inserted-cell activity without replacing the whole notebook', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        saveCalls: 0,
        save: async function save() {
            this.saveCalls += 1;
            return true;
        },
        cells: [
            {
                kind: 2,
                document: { getText: () => 'x = 1\nx', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { applyIncrementalActivityEvents },
        docsByPath,
        notebookEdits,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
    );
    docsByPath.set(doc.uri.fsPath, doc);

    const tracked = { notebook: doc };
    const events = [
        {
            event_id: 'evt-insert',
            type: 'cell-inserted',
            path: 'notebooks/demo.ipynb',
            cell_id: 'cell-2',
            cell_index: 1,
            timestamp: 2,
            data: {
                cell: {
                    index: 1,
                    cell_id: 'cell-2',
                    cell_type: 'code',
                    source: 'y = x + 1\ny',
                    outputs: [],
                    execution_count: null,
                    metadata: { custom: { 'agent-repl': { cell_id: 'cell-2' } } },
                },
            },
        },
    ];
    const result = await applyIncrementalActivityEvents(tracked, events);
    assert.equal(result.changed, true);
    assert.equal(result.needsSnapshot, false);
    assert.equal(doc.cells.length, 2);
    assert.equal(doc.cells[1].document.getText(), 'y = x + 1\ny');
    assert.equal(notebookEdits.length, 1);
    assert.equal(notebookEdits[0].range.start, 1);
    assert.equal(notebookEdits[0].range.end, 1);
    assert.equal(doc.saveCalls, 0);
});

test('applyIncrementalActivityEvents applies output-append activity without falling back to a full snapshot replace', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        saveCalls: 0,
        save: async function save() {
            this.saveCalls += 1;
            return true;
        },
        cells: [
            {
                kind: 2,
                document: { getText: () => 'import time\nprint("start")', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
            {
                kind: 2,
                document: { getText: () => 'y = 2', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-2' } } },
                outputs: [],
                index: 1,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { applyIncrementalActivityEvents },
        docsByPath,
        notebookEdits,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
    );
    docsByPath.set(doc.uri.fsPath, doc);

    const tracked = { notebook: doc };
    const events = [
        {
            event_id: 'evt-output',
            type: 'cell-output-appended',
            path: 'notebooks/demo.ipynb',
            cell_id: 'cell-1',
            cell_index: 0,
            timestamp: 2,
            data: {
                output: { output_type: 'stream', name: 'stdout', text: 'start\n' },
                cell: {
                    index: 0,
                    cell_id: 'cell-1',
                    cell_type: 'code',
                    source: 'import time\nprint("start")',
                    outputs: [{ output_type: 'stream', name: 'stdout', text: 'start\n' }],
                    execution_count: 1,
                    metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                },
            },
        },
    ];
    const result = await applyIncrementalActivityEvents(tracked, events);
    assert.equal(result.changed, true);
    assert.equal(result.needsSnapshot, false);
    assert.equal(notebookEdits.length, 1);
    assert.equal(notebookEdits[0].range.start, 0);
    assert.equal(notebookEdits[0].range.end, 1);
    assert.equal(doc.cells[0].outputs[0].items[0].value, 'start\n');
    assert.equal(doc.saveCalls, 0);
});

test('applyIncrementalActivityEvents ends the active execution when the executing cell is deleted', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        saveCalls: 0,
        save: async function save() {
            this.saveCalls += 1;
            return true;
        },
        cells: [
            {
                kind: 2,
                document: { getText: () => 'long_running()', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { applyIncrementalActivityEvents },
        docsByPath,
        notebookEdits,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
    );
    docsByPath.set(doc.uri.fsPath, doc);

    // Simulate an active execution that will be ended by the cell removal.
    const execution = {
        outputs: [],
        executionOrder: undefined,
        started: true,
        ended: false,
        success: undefined,
        replaceOutput: async (outputs) => { execution.outputs = outputs; },
        start: () => { execution.started = true; },
        end: (success) => { execution.ended = true; execution.success = success; },
    };
    const tracked = {
        notebook: doc,
        activeExecution: {
            cellId: 'cell-1',
            cellIndex: 0,
            execution,
            outputs: [],
        },
    };
    const events = [
        {
            event_id: 'evt-delete',
            type: 'cell-removed',
            path: 'notebooks/demo.ipynb',
            cell_id: 'cell-1',
            cell_index: 0,
            timestamp: 2,
        },
    ];
    const result = await applyIncrementalActivityEvents(tracked, events);
    assert.equal(result.changed, true);
    assert.equal(doc.cells.length, 0);
    assert.equal(notebookEdits.length, 1);
    assert.equal(notebookEdits[0].range.start, 0);
    assert.equal(notebookEdits[0].range.end, 1);
    // The active execution should have been ended by the cell removal.
    assert.equal(execution.ended, true);
    assert.equal(execution.success, false);
    assert.equal(tracked.activeExecution, undefined);
});

test('HeadlessNotebookProjection includes the collaboration session id when projecting and executing cells', async () => {
    const doc = {
        notebookType: 'jupyter-notebook',
        uri: { fsPath: '/workspace/notebooks/demo.ipynb' },
        isDirty: false,
        save: async () => true,
        cells: [
            {
                kind: 2,
                document: { getText: () => '21 * 2', languageId: 'python' },
                metadata: { custom: { 'agent-repl': { cell_id: 'cell-1' } } },
                outputs: [],
                executionSummary: {},
                index: 0,
            },
        ],
    };
    doc.cellCount = doc.cells.length;
    doc.cellAt = (index) => doc.cells[index];

    const {
        module: { HeadlessNotebookProjection },
        docsByPath,
        execCalls,
        httpCalls,
        getController,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'project-visible-notebook': { status: 'ok', path: 'notebooks/demo.ipynb', cell_count: 1, mode: 'headless' },
            },
            httpResponses: {
                'execute-cell': { status: 'ok', outputs: [], execution_count: 1 },
            },
        },
    );
    docsByPath.set(doc.uri.fsPath, doc);
    const context = {
        workspaceState: {
            get: (key) => key === 'agent-repl.session:/workspace' ? 'sess-1' : undefined,
            update: async () => {},
        },
    };
    const daemonDiscovery = () => ({ url: 'http://127.0.0.1:9999', token: 'tok' });
    const projection = new HeadlessNotebookProjection(context, 'agent-repl.agent-repl', daemonDiscovery);
    try {
        const controller = getController();
        await controller.executeHandler([doc.cellAt(0)], doc);
        // project-visible-notebook still uses CLI and should include session id
        const projectCall = execCalls.find(([, args]) => args.includes('project-visible-notebook'));
        assert.ok(projectCall);
        assert.ok(projectCall[1].includes('--session-id'));
        assert.ok(projectCall[1].includes('sess-1'));
        // execute-cell now routes through daemon HTTP and should include session id
        const execCall = httpCalls.find(c => c.url.includes('/notebooks/execute-cell'));
        assert.ok(execCall, 'should call daemon execute-cell endpoint');
        assert.equal(execCall.body.owner_session_id, 'sess-1');
    } finally {
        projection.dispose();
    }
});

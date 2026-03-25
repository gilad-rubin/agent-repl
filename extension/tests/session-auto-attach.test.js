const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadSessionModule(workspaceFolders = [], options = {}) {
    const modulePath = path.resolve(__dirname, '../out/session.js');
    const originalLoad = Module._load;
    const execCalls = [];
    const commandCalls = [];
    const affinityCalls = [];
    const activityEvents = [];
    const executions = [];
    const docsByPath = new Map();
    const notebookEdits = [];
    let createdController;
    const execResponses = options.execResponses || {};
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
                    visibleNotebookEditors: [],
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
        return {
            module: require(modulePath),
            execCalls,
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
        assert.equal(doc.saveCalls, 1);
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
                'execute-visible-cell': {
                    status: 'ok',
                    execution_count: 3,
                    outputs: [{ output_type: 'execute_result', data: { 'text/plain': '9' }, metadata: {} }],
                },
            },
        },
    );
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl');
    try {
        const controller = getController();
        await controller.executeHandler([cell], doc, controller);
        assert.equal(execCalls.length, 2);
        assert.ok(execCalls[0][1].includes('project-visible-notebook'));
        assert.ok(execCalls[0][1].includes('/workspace/notebooks/demo.ipynb'));
        assert.ok(execCalls[1][1].includes('execute-visible-cell'));
        assert.ok(execCalls[1][1].includes('/workspace/notebooks/demo.ipynb'));
        assert.ok(execCalls[1][1].includes('--cell-index'));
        assert.ok(execCalls[1][1].includes('0'));
        assert.ok(execCalls[1][1].includes('--source'));
        assert.ok(execCalls[1][1].includes('x = 9\nx'));
        assert.equal(executions.length, 1);
        assert.equal(executions[0].executionOrder, 3);
        assert.equal(executions[0].success, true);
        assert.equal(executions[0].outputs[0].items[0].value, '9');
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection forwards core notebook activity events and syncs notebook presence', async () => {
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
        activityEvents,
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
                'notebook-activity': [
                    {
                        status: 'ok',
                        path: 'notebooks/demo.ipynb',
                        cursor: 10,
                        recent_events: [
                            {
                                event_id: 'evt-1',
                                type: 'execution-started',
                                detail: 'Executing cell 1',
                                path: 'notebooks/demo.ipynb',
                                timestamp: 10,
                            },
                        ],
                    },
                    {
                        status: 'ok',
                        path: 'notebooks/demo.ipynb',
                        cursor: 10,
                        recent_events: [],
                    },
                ],
                'session-presence-upsert': { status: 'ok' },
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
        await projection.syncNotebookProjection(doc);
        assert.equal(activityEvents.length, 1);
        assert.equal(activityEvents[0].type, 'execution-started');
        assert.ok(execCalls.some(([, args]) => args.includes('session-presence-upsert')));
        assert.ok(execCalls.some(([, args]) => args.includes('notebook-activity')));
    } finally {
        projection.dispose();
    }
});

test('HeadlessNotebookProjection applies incremental inserted-cell activity without replacing the whole notebook', async () => {
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
                'notebook-projection': [
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
                                    source: 'x = 1\nx',
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
                ],
                'notebook-activity': [
                    { status: 'ok', path: 'notebooks/demo.ipynb', cursor: 1, recent_events: [] },
                    {
                        status: 'ok',
                        path: 'notebooks/demo.ipynb',
                        cursor: 2,
                        recent_events: [
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
                        ],
                    },
                ],
                'session-presence-upsert': { status: 'ok' },
            },
        },
    );
    docsByPath.set(doc.uri.fsPath, doc);
    const projection = new HeadlessNotebookProjection({ workspaceState: { get: () => undefined, update: async () => {} } }, 'agent-repl.agent-repl');
    try {
        const firstChanged = await projection.syncNotebookProjection(doc);
        assert.equal(firstChanged, true);

        const secondChanged = await projection.syncNotebookProjection(doc);
        assert.equal(secondChanged, true);
        assert.equal(doc.cells.length, 2);
        assert.equal(doc.cells[1].document.getText(), 'y = x + 1\ny');
        assert.equal(notebookEdits.length, 2);
        assert.equal(notebookEdits[1].range.start, 1);
        assert.equal(notebookEdits[1].range.end, 1);
        assert.equal(doc.saveCalls, 2);
    } finally {
        projection.dispose();
    }
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
        getController,
    } = loadSessionModule(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
                'project-visible-notebook': { status: 'ok', path: 'notebooks/demo.ipynb', cell_count: 1, mode: 'headless' },
                'execute-visible-cell': { status: 'ok', outputs: [], execution_count: 1 },
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
        const controller = getController();
        await controller.executeHandler([doc.cellAt(0)], doc);
        const projectCall = execCalls.find(([, args]) => args.includes('project-visible-notebook'));
        const executeCall = execCalls.find(([, args]) => args.includes('execute-visible-cell'));
        assert.ok(projectCall);
        assert.ok(executeCall);
        assert.ok(projectCall[1].includes('--session-id'));
        assert.ok(projectCall[1].includes('sess-1'));
        assert.ok(executeCall[1].includes('--session-id'));
        assert.ok(executeCall[1].includes('sess-1'));
    } finally {
        projection.dispose();
    }
});

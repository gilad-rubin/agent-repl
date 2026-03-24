const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadV2Module(workspaceFolders = [], options = {}) {
    const modulePath = path.resolve(__dirname, '../out/v2.js');
    const originalLoad = Module._load;
    const execCalls = [];
    const commandCalls = [];
    const affinityCalls = [];
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
        if (request === 'util') {
            return {
                promisify: () => async (command, args) => {
                    execCalls.push([command, args]);
                    const key = args.includes('execute-visible-cell')
                        ? 'execute-visible-cell'
                        : args.includes('notebook-projection')
                            ? 'notebook-projection'
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
            executions,
            docsByPath,
            notebookEdits,
            getController: () => createdController,
        };
    } finally {
        Module._load = originalLoad;
    }
}

test('v2CliPlans prefers uv run when a pyproject exists in the workspace root', () => {
    const originalExistsSync = fs.existsSync;
    fs.existsSync = (target) => target === '/workspace/pyproject.toml';

    try {
        const { module: { v2CliPlans } } = loadV2Module();
        const plans = v2CliPlans('/workspace', { get: () => undefined });
        assert.deepEqual(plans[0], { command: 'uv', args: ['run', 'agent-repl'], cwd: '/workspace' });
        assert.deepEqual(plans[1], { command: 'agent-repl', args: [], cwd: '/workspace' });
    } finally {
        fs.existsSync = originalExistsSync;
    }
});

test('v2CliPlans prefers configured and workspace-local launchers before PATH fallbacks', () => {
    const originalExistsSync = fs.existsSync;
    fs.existsSync = (target) => (
        target === '/workspace/.venv/bin/agent-repl' ||
        target === '/workspace/pyproject.toml'
    );

    try {
        const { module: { v2CliPlans } } = loadV2Module();
        const plans = v2CliPlans('/workspace', { get: () => '/custom/agent-repl' });
        assert.deepEqual(plans[0], { command: '/custom/agent-repl', args: [], cwd: '/workspace' });
        assert.deepEqual(plans[1], { command: '/workspace/.venv/bin/agent-repl', args: [], cwd: '/workspace' });
        assert.deepEqual(plans[2], { command: 'uv', args: ['run', 'agent-repl'], cwd: '/workspace' });
        assert.deepEqual(plans[3], { command: 'agent-repl', args: [], cwd: '/workspace' });
    } finally {
        fs.existsSync = originalExistsSync;
    }
});

test('primaryWorkspaceRoot returns the first workspace folder path', () => {
    const { module: { primaryWorkspaceRoot } } = loadV2Module([{ uri: { fsPath: '/workspace' } }]);
    assert.equal(primaryWorkspaceRoot(), '/workspace');
});

test('HeadlessNotebookProjection selects the shared runtime controller when a notebook already has a live headless runtime', async () => {
    const doc = { notebookType: 'jupyter-notebook', uri: { fsPath: '/workspace/notebooks/demo.ipynb' } };
    const editor = { notebook: doc };
    const {
        module: { HeadlessNotebookProjection, PROJECTION_CONTROLLER_ID },
        commandCalls,
        affinityCalls,
    } = loadV2Module(
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
    } = loadV2Module(
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
    } = loadV2Module(
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
    } = loadV2Module(
        [{ uri: { fsPath: '/workspace' } }],
        {
            execResponses: {
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
        assert.equal(execCalls.length, 1);
        assert.ok(execCalls[0][1].includes('execute-visible-cell'));
        assert.ok(execCalls[0][1].includes('/workspace/notebooks/demo.ipynb'));
        assert.ok(execCalls[0][1].includes('--cell-index'));
        assert.ok(execCalls[0][1].includes('0'));
        assert.ok(execCalls[0][1].includes('--source'));
        assert.ok(execCalls[0][1].includes('x = 9\nx'));
        assert.equal(executions.length, 1);
        assert.equal(executions[0].executionOrder, 3);
        assert.equal(executions[0].success, true);
        assert.equal(executions[0].outputs[0].items[0].value, '9');
    } finally {
        projection.dispose();
    }
});

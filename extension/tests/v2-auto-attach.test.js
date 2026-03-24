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
    let createdController;
    const execResponses = options.execResponses || {};
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                workspace: {
                    workspaceFolders,
                    getConfiguration: () => ({ get: (_name, fallback) => fallback }),
                    onDidOpenNotebookDocument: () => ({ dispose() {} }),
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
                commands: {
                    executeCommand: async (...args) => {
                        commandCalls.push(args);
                    },
                },
                env: { appName: 'VS Code' },
                NotebookControllerAffinity: { Preferred: 2 },
                NotebookCellKind: { Code: 2 },
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
                    const key = args.includes('execute-visible-cell') ? 'execute-visible-cell' : args.includes('notebook-runtime') ? 'notebook-runtime' : 'default';
                    const payload = execResponses[key] || execResponses.default || { status: 'ok' };
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

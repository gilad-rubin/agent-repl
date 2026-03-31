const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadRoutesModule({ vscode, resolver, queue, session }) {
    const modulePath = path.resolve(__dirname, '../out/routes.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
        }
        if (request.endsWith('/server')) {
            return {};
        }
        if (request.endsWith('/notebook/resolver')) {
            return resolver;
        }
        if (request.endsWith('/notebook/operations')) {
            return {};
        }
        if (request.endsWith('/notebook/identity')) {
            return {
                getCellId: () => 'cell-1',
                ensureIds: async () => {},
                resolveCell: () => 0,
                withCellId: (cellId) => ({ custom: { 'agent-repl': { cell_id: cellId } } }),
                newCellId: () => 'new-cell',
            };
        }
        if (request.endsWith('/notebook/outputs')) {
            return {
                toJupyter: () => [],
                stripForAgent: (value) => value,
            };
        }
        if (request.endsWith('/execution/queue')) {
            return queue;
        }
        if (request.endsWith('/session')) {
            return session ?? {
                discoverDaemon: () => ({ url: 'http://127.0.0.1:9999', token: 'tok' }),
                daemonPost: async () => ({ status: 'ok' }),
                workspaceRootForPath: () => '/workspace',
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

test('create route keeps notebook in the background when quiet kernel attach succeeds', async () => {
    const originalEnv = process.env.JUPYTER_PATH;
    const originalExistsSync = fs.existsSync;
    const originalReaddirSync = fs.readdirSync;
    const originalReadFileSync = fs.readFileSync;

    const fakeRoot = path.join('/tmp', 'agent-repl-test');
    const kernelsDir = path.join(fakeRoot, 'kernels');
    const specDir = path.join(kernelsDir, 'subtext-venv');
    const specFile = path.join(specDir, 'kernel.json');
    const workspacePython = path.join('/workspace', '.venv', 'bin', 'python');
    const uri = { fsPath: '/workspace/tmp/demo.ipynb', toString: () => 'file:///workspace/tmp/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 0 };
    let selected = false;
    let ensureCalls = 0;
    let showCalls = 0;

    process.env.JUPYTER_PATH = fakeRoot;
    fs.existsSync = (target) => (
        target === kernelsDir ||
        target === specFile ||
        target === workspacePython
    );
    fs.readdirSync = (target) => {
        if (target !== kernelsDir) {
            throw new Error(`Unexpected dir: ${target}`);
        }
        return [{ name: 'subtext-venv', isDirectory: () => true }];
    };
    fs.readFileSync = (target, ...args) => {
        if (target !== specFile) {
            return originalReadFileSync(target, ...args);
        }
        return JSON.stringify({
            argv: [workspacePython],
            display_name: 'subtext (.venv)',
            language: 'python',
        });
    };

    const routesModule = loadRoutesModule({
        vscode: {
            Uri: { file: (value) => ({ fsPath: value, toString: () => `file://${value}` }) },
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [],
                fs: {
                    writeFile: async () => {},
                },
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => {
                    showCalls += 1;
                    return {};
                },
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: {
                executeCommand: async () => {
                    throw new Error('command fallback should not run');
                },
            },
            extensions: {
                getExtension: (id) => {
                    if (id === 'ms-python.python') {
                        return {
                            isActive: true,
                            exports: {
                                environments: {
                                    known: [{ id: 'subtext-venv', executable: { uri: { fsPath: workspacePython } } }],
                                    resolveEnvironment: async () => ({ id: 'subtext-venv', executable: { uri: { fsPath: workspacePython } } }),
                                },
                            },
                        };
                    }
                    return undefined;
                },
            },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => undefined,
            findEditor: () => {
                throw new Error('not needed');
            },
            ensureNotebookEditor: async () => {
                ensureCalls += 1;
                return {};
            },
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            executeCell: async () => ({ status: 'ok' }),
            getExecution: () => ({ status: 'ok' }),
            getStatus: async () => ({ kernel_state: 'idle' }),
            insertAndExecute: async () => ({ status: 'ok' }),
            resetExecutionState: () => {},
            resetJupyterApiCache: () => {},
            getJupyterApi: async () => ({
                openNotebook: async () => {
                    selected = true;
                },
                kernels: {
                    getKernel: async () => undefined,
                },
                getPythonEnvironment: async () => (
                    selected ? { executable: { uri: { fsPath: workspacePython } } } : undefined
                ),
            }),
            startExecution: async () => ({ status: 'started', execution_id: 'exec-1' }),
            startNotebookExecutionAll: async () => [],
        },
    });

    try {
        const routes = routesModule.buildRoutes(20);
        const result = await routes['POST /api/notebook/create']({
            path: 'tmp/demo.ipynb',
            cwd: '/workspace',
            cells: [{ type: 'code', source: 'x = 1' }],
        });
        assert.equal(result.status, 'ok');
        assert.equal(result.ready, true);
        assert.equal(result.kernel_status, 'selected');
        assert.equal(ensureCalls, 0);
        assert.equal(showCalls, 0);
    } finally {
        if (originalEnv === undefined) {
            delete process.env.JUPYTER_PATH;
        } else {
            process.env.JUPYTER_PATH = originalEnv;
        }
        fs.existsSync = originalExistsSync;
        fs.readdirSync = originalReaddirSync;
        fs.readFileSync = originalReadFileSync;
    }
});

test('create route fails clearly when no workspace venv kernel exists and none is specified', async () => {
    const originalEnv = process.env.JUPYTER_PATH;
    const originalExistsSync = fs.existsSync;
    const originalReaddirSync = fs.readdirSync;

    const fakeRoot = path.join('/tmp', 'agent-repl-test-missing-kernel');
    const kernelsDir = path.join(fakeRoot, 'kernels');
    const uri = { fsPath: '/workspace/tmp/demo.ipynb', toString: () => 'file:///workspace/tmp/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 0 };
    let showCalls = 0;

    process.env.JUPYTER_PATH = fakeRoot;
    fs.existsSync = (target) => target === kernelsDir ? true : false;
    fs.readdirSync = (target) => {
        if (target !== kernelsDir) {
            throw new Error(`Unexpected dir: ${target}`);
        }
        return [];
    };

    const routesModule = loadRoutesModule({
        vscode: {
            Uri: { file: (value) => ({ fsPath: value, toString: () => `file://${value}` }) },
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [],
                fs: {
                    writeFile: async () => {},
                },
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => {
                    showCalls += 1;
                    return {};
                },
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: {
                executeCommand: async () => {
                    throw new Error('interactive kernel picker should not run');
                },
            },
            extensions: {
                getExtension: () => undefined,
            },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => undefined,
            findEditor: () => {
                throw new Error('not needed');
            },
            ensureNotebookEditor: async () => {
                throw new Error('should not ensure editor when kernel is missing');
            },
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            executeCell: async () => ({ status: 'ok' }),
            getExecution: () => ({ status: 'ok' }),
            getStatus: async () => ({ kernel_state: 'idle' }),
            insertAndExecute: async () => ({ status: 'ok' }),
            resetExecutionState: () => {},
            resetJupyterApiCache: () => {},
            getJupyterApi: async () => undefined,
            startExecution: async () => ({ status: 'started', execution_id: 'exec-1' }),
            startNotebookExecutionAll: async () => [],
        },
    });

    try {
        const routes = routesModule.buildRoutes(20);
        await assert.rejects(
            () => routes['POST /api/notebook/create']({
                path: 'tmp/demo.ipynb',
                cwd: '/workspace',
            }),
            (error) => {
                assert.equal(error.statusCode, 400);
                assert.match(error.message, /No workspace \.venv kernel/i);
                return true;
            },
        );
        assert.equal(showCalls, 0);
    } finally {
        if (originalEnv === undefined) {
            delete process.env.JUPYTER_PATH;
        } else {
            process.env.JUPYTER_PATH = originalEnv;
        }
        fs.existsSync = originalExistsSync;
        fs.readdirSync = originalReaddirSync;
    }
});

test('execute-all route forwards to daemon HTTP', async () => {
    const uri = { fsPath: '/workspace/tmp/demo.ipynb', toString: () => 'file:///workspace/tmp/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 2 };
    let showCalls = 0;
    const daemonCalls = [];

    const routesModule = loadRoutesModule({
        vscode: {
            NotebookCellKind: { Markup: 1, Code: 2 },
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [],
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => { showCalls += 1; return {}; },
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: { executeCommand: async () => { throw new Error('should not run'); } },
            extensions: { getExtension: () => undefined },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => undefined,
            findEditor: () => { throw new Error('not needed'); },
            ensureNotebookEditor: async () => { throw new Error('should not ensure editor'); },
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            resetJupyterApiCache: () => {},
            getJupyterApi: async () => undefined,
        },
        session: {
            discoverDaemon: () => ({ url: 'http://127.0.0.1:9999', token: 'tok' }),
            daemonPost: async (_daemon, endpoint, body) => {
                daemonCalls.push({ endpoint, body });
                return { status: 'started', executions: [{ execution_id: 'exec-1', cell_index: 1 }] };
            },
            workspaceRootForPath: () => '/workspace',
        },
    });

    const routes = routesModule.buildRoutes(20);
    const result = await routes['POST /api/notebook/execute-all']({
        path: 'tmp/demo.ipynb',
        cwd: '/workspace',
    });

    assert.equal(result.status, 'started');
    assert.equal(daemonCalls.length, 1);
    assert.equal(daemonCalls[0].endpoint, '/api/notebooks/execute-all');
    assert.equal(daemonCalls[0].body.path, 'tmp/demo.ipynb');
    assert.equal(showCalls, 0);
});

test('insert-and-execute route forwards to daemon HTTP', async () => {
    const uri = { fsPath: '/workspace/tmp/demo.ipynb', toString: () => 'file:///workspace/tmp/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 1 };
    let showCalls = 0;
    const daemonCalls = [];

    const routesModule = loadRoutesModule({
        vscode: {
            NotebookCellKind: { Markup: 1, Code: 2 },
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [doc],
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => { showCalls += 1; return {}; },
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: { executeCommand: async () => { throw new Error('should not run'); } },
            extensions: { getExtension: () => undefined },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => doc,
            findEditor: () => undefined,
            ensureNotebookEditor: async () => { throw new Error('should not ensure editor'); },
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            resetJupyterApiCache: () => {},
            getJupyterApi: async () => undefined,
        },
        session: {
            discoverDaemon: () => ({ url: 'http://127.0.0.1:9999', token: 'tok' }),
            daemonPost: async (_daemon, endpoint, body) => {
                daemonCalls.push({ endpoint, body });
                return { status: 'started', execution_id: 'exec-ix', cell_id: 'cell-2' };
            },
            workspaceRootForPath: () => '/workspace',
        },
    });

    const routes = routesModule.buildRoutes(20);
    const result = await routes['POST /api/notebook/insert-and-execute']({
        path: 'tmp/demo.ipynb',
        cwd: '/workspace',
        source: 'x = 2',
        cell_type: 'code',
        at_index: -1,
    });

    assert.equal(result.status, 'started');
    assert.equal(result.execution_id, 'exec-ix');
    assert.equal(daemonCalls.length, 1);
    assert.equal(daemonCalls[0].endpoint, '/api/notebooks/insert-and-execute');
    assert.equal(daemonCalls[0].body.source, 'x = 2');
    assert.equal(daemonCalls[0].body.cell_type, 'code');
    assert.equal(showCalls, 0);
});

test('restart routes use background shutdown and quiet reattach without opening the notebook UI', async () => {
    const originalEnv = process.env.JUPYTER_PATH;
    const originalExistsSync = fs.existsSync;
    const originalReaddirSync = fs.readdirSync;
    const originalReadFileSync = fs.readFileSync;

    const fakeRoot = path.join('/tmp', 'agent-repl-restart');
    const kernelsDir = path.join(fakeRoot, 'kernels');
    const specDir = path.join(kernelsDir, 'subtext-venv');
    const specFile = path.join(specDir, 'kernel.json');
    const workspacePython = path.join('/workspace', '.venv', 'bin', 'python');
    const uri = { fsPath: '/workspace/notebooks/demo.ipynb', toString: () => 'file:///workspace/notebooks/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 1 };

    let shutdownCalls = 0;
    let openNotebookCalls = 0;
    let showCalls = 0;
    let commandCalls = 0;
    let resetApiCalls = 0;

    process.env.JUPYTER_PATH = fakeRoot;
    fs.existsSync = (target) => (
        target === kernelsDir ||
        target === specFile ||
        target === workspacePython
    );
    fs.readdirSync = (target) => {
        if (target !== kernelsDir) {
            throw new Error(`Unexpected dir: ${target}`);
        }
        return [{ name: 'subtext-venv', isDirectory: () => true }];
    };
    fs.readFileSync = (target, ...args) => {
        if (target !== specFile) {
            return originalReadFileSync(target, ...args);
        }
        return JSON.stringify({
            argv: [workspacePython],
            display_name: 'subtext (.venv)',
            language: 'python',
        });
    };

    const daemonCalls = [];

    const routesModule = loadRoutesModule({
        vscode: {
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [doc],
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => {
                    showCalls += 1;
                    return {};
                },
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: {
                executeCommand: async () => {
                    commandCalls += 1;
                    throw new Error('interactive restart should not run');
                },
            },
            extensions: {
                getExtension: (id) => {
                    if (id !== 'ms-toolsai.jupyter') {
                        return undefined;
                    }
                    return {
                        isActive: true,
                        exports: {
                            openNotebook: async () => {
                                openNotebookCalls += 1;
                            },
                            kernels: {
                                getKernel: async () => ({
                                    status: 'idle',
                                    shutdown: async () => {
                                        shutdownCalls += 1;
                                    },
                                }),
                            },
                            getPythonEnvironment: async () => ({
                                executable: { uri: { fsPath: workspacePython } },
                            }),
                        },
                    };
                },
            },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => doc,
            findEditor: () => {
                throw new Error('not needed');
            },
            ensureNotebookEditor: async () => {
                throw new Error('should not ensure editor for restart');
            },
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            resetJupyterApiCache: () => {
                resetApiCalls += 1;
            },
            getJupyterApi: async () => ({
                openNotebook: async () => {
                    openNotebookCalls += 1;
                },
                kernels: {
                    getKernel: async () => ({
                        status: 'idle',
                        shutdown: async () => {
                            shutdownCalls += 1;
                        },
                    }),
                },
                getPythonEnvironment: async () => ({
                    executable: { uri: { fsPath: workspacePython } },
                }),
            }),
        },
        session: {
            discoverDaemon: () => ({ url: 'http://127.0.0.1:9999', token: 'tok' }),
            daemonPost: async (_daemon, endpoint, body) => {
                daemonCalls.push({ endpoint, body });
                return { status: 'started', executions: [{ execution_id: 'exec-1', cell_index: 0 }] };
            },
            workspaceRootForPath: () => '/workspace',
        },
    });

    try {
        const routes = routesModule.buildRoutes(20);

        const restart = await routes['POST /api/notebook/restart-kernel']({
            path: 'notebooks/demo.ipynb',
            cwd: '/workspace',
        });
        assert.equal(restart.status, 'ok');
        assert.match(restart.method, /jupyter\.openNotebook/);

        const restartRunAll = await routes['POST /api/notebook/restart-and-run-all']({
            path: 'notebooks/demo.ipynb',
            cwd: '/workspace',
        });
        assert.equal(restartRunAll.status, 'started');

        assert.equal(shutdownCalls, 2);
        assert.equal(openNotebookCalls, 2);
        assert.equal(resetApiCalls, 2);
        assert.equal(daemonCalls.length, 1);
        assert.equal(daemonCalls[0].endpoint, '/api/notebooks/execute-all');
        assert.equal(showCalls, 0);
        assert.equal(commandCalls, 0);
    } finally {
        if (originalEnv === undefined) {
            delete process.env.JUPYTER_PATH;
        } else {
            process.env.JUPYTER_PATH = originalEnv;
        }
        fs.existsSync = originalExistsSync;
        fs.readdirSync = originalReaddirSync;
        fs.readFileSync = originalReadFileSync;
    }
});

test('open route defaults to the Agent REPL canvas editor', async () => {
    const uri = { fsPath: '/workspace/tmp/demo.ipynb', toString: () => 'file:///workspace/tmp/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 0 };
    let ensureCalls = 0;
    const executeCalls = [];

    const routesModule = loadRoutesModule({
        vscode: {
            Uri: { file: (value) => ({ fsPath: value, toString: () => `file://${value}` }) },
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [],
                getConfiguration: () => ({ get: (_key, fallback) => fallback }),
                fs: { writeFile: async () => {} },
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => ({}),
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: {
                executeCommand: async (...args) => {
                    executeCalls.push(args);
                },
            },
            env: {
                openExternal: async () => {
                    throw new Error('browser open should not run');
                },
            },
            extensions: {
                getExtension: () => undefined,
            },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => undefined,
            findEditor: () => {
                throw new Error('not needed');
            },
            ensureNotebookEditor: async () => {
                ensureCalls += 1;
                return {};
            },
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            executeCell: async () => ({ status: 'ok' }),
            getExecution: () => ({ status: 'ok' }),
            getStatus: async () => ({ kernel_state: 'idle' }),
            insertAndExecute: async () => ({ status: 'ok' }),
            resetExecutionState: () => {},
            resetJupyterApiCache: () => {},
            getJupyterApi: async () => undefined,
            startExecution: async () => ({ status: 'started', execution_id: 'exec-1' }),
            startNotebookExecutionAll: async () => [],
        },
    });

    const routes = routesModule.buildRoutes(20);
    const result = await routes['POST /api/notebook/open']({
        path: 'tmp/demo.ipynb',
        cwd: '/workspace',
    });

    assert.equal(result.status, 'ok');
    assert.equal(result.editor, 'canvas');
    assert.equal(result.view_type, 'agent-repl.canvasEditor');
    assert.deepEqual(executeCalls, [['vscode.openWith', uri, 'agent-repl.canvasEditor']]);
    assert.equal(ensureCalls, 0);
});

test('open route can target the standalone browser canvas', async () => {
    const uri = { fsPath: '/workspace/tmp/demo.ipynb', toString: () => 'file:///workspace/tmp/demo.ipynb' };
    const doc = { uri, notebookType: 'jupyter-notebook', cellCount: 0 };
    const openedUrls = [];
    const executeCalls = [];

    const routesModule = loadRoutesModule({
        vscode: {
            Uri: {
                file: (value) => ({ fsPath: value, toString: () => `file://${value}` }),
                parse: (value) => ({ toString: () => value }),
            },
            workspace: {
                workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
                notebookDocuments: [],
                getConfiguration: () => ({ get: (_key, fallback) => fallback }),
                fs: { writeFile: async () => {} },
                openNotebookDocument: async () => doc,
            },
            window: {
                showNotebookDocument: async () => ({}),
                activeNotebookEditor: undefined,
                activeTextEditor: undefined,
                visibleNotebookEditors: [],
            },
            commands: {
                executeCommand: async (...args) => {
                    executeCalls.push(args);
                },
            },
            env: {
                openExternal: async (target) => {
                    openedUrls.push(target.toString());
                },
            },
            extensions: {
                getExtension: () => undefined,
            },
        },
        resolver: {
            resolveNotebook: () => doc,
            resolveNotebookUri: () => uri,
            resolveOrOpenNotebook: async () => doc,
            findOpenNotebook: () => undefined,
            findEditor: () => {
                throw new Error('not needed');
            },
            ensureNotebookEditor: async () => ({}),
            captureEditorFocus: () => ({ kind: 'none' }),
            restoreEditorFocus: async () => {},
        },
        queue: {
            executeCell: async () => ({ status: 'ok' }),
            getExecution: () => ({ status: 'ok' }),
            getStatus: async () => ({ kernel_state: 'idle' }),
            insertAndExecute: async () => ({ status: 'ok' }),
            resetExecutionState: () => {},
            resetJupyterApiCache: () => {},
            getJupyterApi: async () => undefined,
            startExecution: async () => ({ status: 'started', execution_id: 'exec-1' }),
            startNotebookExecutionAll: async () => [],
        },
    });

    const routes = routesModule.buildRoutes(20);
    const result = await routes['POST /api/notebook/open']({
        path: 'tmp/demo.ipynb',
        cwd: '/workspace',
        target: 'browser',
        browser_url: 'http://127.0.0.1:4183/preview.html',
    });

    assert.equal(result.status, 'ok');
    assert.equal(result.target, 'browser');
    assert.equal(result.editor, 'canvas');
    assert.equal(result.url, 'http://127.0.0.1:4183/preview.html?path=tmp%2Fdemo.ipynb');
    assert.deepEqual(openedUrls, ['http://127.0.0.1:4183/preview.html?path=tmp%2Fdemo.ipynb']);
    assert.deepEqual(executeCalls, []);
});

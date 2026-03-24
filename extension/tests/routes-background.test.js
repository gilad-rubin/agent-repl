const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadRoutesModule({ vscode, resolver, queue }) {
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
                getCellId: () => undefined,
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

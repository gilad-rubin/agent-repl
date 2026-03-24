const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadExtensionModule() {
    const modulePath = path.resolve(__dirname, '../out/extension.js');
    const originalLoad = Module._load;
    const registeredCommands = new Map();
    const attachCalls = [];
    const detachCalls = [];
    const infoMessages = [];
    const errors = [];
    const connectionWrites = [];

    const config = {
        get(name, fallback) {
            const values = {
                autoStart: true,
                maxQueueSize: 20,
                port: 0,
                sessionAutoAttach: true,
            };
            return Object.prototype.hasOwnProperty.call(values, name) ? values[name] : fallback;
        },
    };

    class FakeBridgeServer {
        constructor(token, routes) {
            this.token = token;
            this.routes = routes;
            this.port = 31337;
        }

        addRoute() {}

        getRoute() {
            return () => ({ status: 'ok' });
        }

        setRoutes(routes) {
            this.routes = routes;
        }

        async start(port) {
            this.port = port || 31337;
            return this.port;
        }

        dispose() {}
    }

    class FakeV2AutoAttach {
        constructor(context) {
            this.context = context;
        }

        async attachIfEnabled(configArg) {
            attachCalls.push(configArg);
        }

        async detachIfAttached() {
            detachCalls.push(true);
        }

        dispose() {}
    }

    const vscode = {
        StatusBarAlignment: { Left: 1 },
        workspace: {
            workspaceFolders: [{ uri: { fsPath: '/workspace' } }],
            getConfiguration: () => config,
            onDidChangeNotebookDocument: () => ({ dispose() {} }),
        },
        notebooks: {
            registerNotebookCellStatusBarItemProvider: () => ({ dispose() {} }),
        },
        window: {
            createStatusBarItem: () => ({
                text: '',
                tooltip: '',
                command: '',
                show() {},
                dispose() {},
            }),
            registerWebviewViewProvider: () => ({ dispose() {} }),
            showInformationMessage: (message) => {
                infoMessages.push(message);
            },
            showErrorMessage: (message) => {
                errors.push(message);
            },
        },
        commands: {
            registerCommand: (name, callback) => {
                registeredCommands.set(name, callback);
                return { dispose() {} };
            },
        },
    };

    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
        }
        if (request === './server') {
            return { BridgeServer: FakeBridgeServer };
        }
        if (request === './routes') {
            return { buildRoutes: () => ({}) };
        }
        if (request === './discovery') {
            return {
                writeConnectionFile: (payload) => {
                    connectionWrites.push(payload);
                },
                removeConnectionFile: () => {},
                generateToken: () => 'token',
            };
        }
        if (request === './prompts/statusBar') {
            return {
                PromptStatusBarProvider: class PromptStatusBarProvider {
                    refresh() {}
                    dispose() {}
                },
            };
        }
        if (request === './prompts/commands') {
            return { insertPromptCell: () => {} };
        }
        if (request === './activity/panel') {
            return { ActivityPanelProvider: class ActivityPanelProvider {} };
        }
        if (request === './execution/queue') {
            return { initExecutionMonitor: () => ({ dispose() {} }) };
        }
        if (request === './v2') {
            return { V2AutoAttach: FakeV2AutoAttach };
        }
        return originalLoad.call(this, request, parent, isMain);
    };

    delete require.cache[modulePath];
    try {
        return {
            extension: require(modulePath),
            registeredCommands,
            attachCalls,
            detachCalls,
            infoMessages,
            errors,
            connectionWrites,
        };
    } finally {
        Module._load = originalLoad;
    }
}

test('extension lifecycle auto-attaches to the shared core on start and detaches on stop', async () => {
    const {
        extension,
        registeredCommands,
        attachCalls,
        detachCalls,
        infoMessages,
        errors,
        connectionWrites,
    } = loadExtensionModule();
    const context = {
        subscriptions: [],
        workspaceState: {
            get: () => undefined,
            update: async () => {},
        },
    };

    await extension.activate(context);

    assert.equal(errors.length, 0);
    assert.equal(attachCalls.length, 1);
    assert.equal(connectionWrites.length, 1);
    assert.match(infoMessages[0], /Agent REPL started/);

    await registeredCommands.get('agent-repl.start')();
    assert.equal(attachCalls.length, 2);
    assert.match(infoMessages[1], /already running/);

    await registeredCommands.get('agent-repl.stop')();
    assert.equal(detachCalls.length, 1);
});

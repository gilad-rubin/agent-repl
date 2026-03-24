import * as path from 'path';
import * as vscode from 'vscode';
import { BridgeServer } from './server';
import { buildRoutes } from './routes';
import { writeConnectionFile, removeConnectionFile, generateToken } from './discovery';
import { PromptStatusBarProvider } from './prompts/statusBar';
import { insertPromptCell } from './prompts/commands';
import { ActivityPanelProvider } from './activity/panel';
import { initExecutionMonitor } from './execution/queue';
import { V2AutoAttach } from './v2';

let server: BridgeServer | undefined;
let statusBarItem: vscode.StatusBarItem;
let extensionContext: vscode.ExtensionContext | undefined;
let executionMonitorDisposable: vscode.Disposable | undefined;
let v2AutoAttach: V2AutoAttach | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    extensionContext = context;
    const config = vscode.workspace.getConfiguration('agent-repl');
    v2AutoAttach = new V2AutoAttach(context);

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    statusBarItem.command = 'agent-repl.start';
    statusBarItem.text = '$(circle-outline) Agent REPL';
    statusBarItem.tooltip = 'Agent REPL: stopped';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    context.subscriptions.push(v2AutoAttach);

    // Execution monitor (watches cell executionSummary for completion detection)
    executionMonitorDisposable = initExecutionMonitor();
    context.subscriptions.push(executionMonitorDisposable);

    // Prompt badges
    const promptProvider = new PromptStatusBarProvider();
    context.subscriptions.push(
        vscode.notebooks.registerNotebookCellStatusBarItemProvider('jupyter-notebook', promptProvider)
    );

    // Activity panel
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('agent-repl.activity', new ActivityPanelProvider())
    );

    // Commands
    context.subscriptions.push(
        vscode.commands.registerCommand('agent-repl.start', () => startBridge(config, promptProvider)),
        vscode.commands.registerCommand('agent-repl.stop', () => stopBridge()),
        vscode.commands.registerCommand('agent-repl.askAgent', () => insertPromptCell())
    );

    // Auto-start
    if (config.get<boolean>('autoStart', true)) {
        await startBridge(config, promptProvider);
    }
}

export function deactivate(): void { stopBridge(); }

async function startBridge(
    config: vscode.WorkspaceConfiguration,
    promptProvider: PromptStatusBarProvider
): Promise<void> {
    if (server) {
        try {
            await v2AutoAttach?.attachIfEnabled(config);
        } catch (err: any) {
            console.warn('[agent-repl] session auto-attach retry failed:', err?.message ?? String(err));
        }
        vscode.window.showInformationMessage(`Agent REPL already running on port ${server.port}`);
        return;
    }

    const token = generateToken();
    const maxQueue = config.get<number>('maxQueueSize', 20);
    const routes = buildRoutes(maxQueue);
    server = new BridgeServer(token, routes);

    // Hot-reload: clear require cache for our modules, rebuild routes in-place
    const outDir = path.join(__dirname);
    server.addRoute('POST /api/reload', async () => {
        for (const key of Object.keys(require.cache)) {
            if (
                key.startsWith(outDir) &&
                !key.endsWith('extension.js') &&
                !key.endsWith('server.js')
            ) {
                delete require.cache[key];
            }
        }
        const fresh = require('./routes') as { buildRoutes: typeof buildRoutes };
        const freshQueue = require('./execution/queue') as { initExecutionMonitor: typeof initExecutionMonitor };
        const newRoutes = fresh.buildRoutes(maxQueue);
        newRoutes['POST /api/reload'] = server!.getRoute('POST /api/reload')!;
        server!.setRoutes(newRoutes);

        executionMonitorDisposable?.dispose();
        executionMonitorDisposable = freshQueue.initExecutionMonitor();
        extensionContext?.subscriptions.push(executionMonitorDisposable);

        return {
            status: 'ok',
            message: 'Routes hot-reloaded',
            extension_root: path.resolve(outDir, '..'),
            routes_module: require.resolve('./routes'),
        };
    });

    try {
        const port = await server.start(config.get<number>('port', 0));

        writeConnectionFile({
            port, token, pid: process.pid, version: '0.3.0',
            workspace_folders: (vscode.workspace.workspaceFolders ?? []).map(f => f.uri.fsPath)
        });

        statusBarItem.text = `$(circle-filled) Agent REPL :${port}`;
        statusBarItem.tooltip = `Agent REPL: running on port ${port}`;

        // Refresh prompt badges on notebook changes
        context_subscriptions_push(
            vscode.workspace.onDidChangeNotebookDocument(e => {
                if (e.notebook.notebookType === 'jupyter-notebook') { promptProvider.refresh(); }
            })
        );

        try {
            await v2AutoAttach?.attachIfEnabled(config);
        } catch (err: any) {
            console.warn('[agent-repl] session auto-attach failed:', err?.message ?? String(err));
        }

        vscode.window.showInformationMessage(`Agent REPL started on port ${port}`);
    } catch (err: any) {
        vscode.window.showErrorMessage(`Failed to start Agent REPL: ${err.message}`);
        server = undefined;
    }
}

// Store disposables outside context for startBridge
const extraDisposables: vscode.Disposable[] = [];
function context_subscriptions_push(d: vscode.Disposable): void { extraDisposables.push(d); }

function stopBridge(): void {
    void v2AutoAttach?.detachIfAttached();
    server?.dispose();
    server = undefined;
    removeConnectionFile();
    for (const d of extraDisposables) { d.dispose(); }
    extraDisposables.length = 0;
    statusBarItem.text = '$(circle-outline) Agent REPL';
    statusBarItem.tooltip = 'Agent REPL: stopped';
}

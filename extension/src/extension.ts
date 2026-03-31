import * as path from 'path';
import * as vscode from 'vscode';
import { BridgeServer } from './server';
import { buildRoutes } from './routes';
import { writeConnectionFile, removeConnectionFile, generateToken } from './discovery';
import { PromptStatusBarProvider } from './prompts/statusBar';
import { insertPromptCell } from './prompts/commands';
import { ActivityPanelProvider } from './activity/panel';
import { SessionAutoAttach } from './session';
import { CanvasEditorProvider } from './editor/provider';
import { logNotebookDiagnostic } from './debug';

let server: BridgeServer | undefined;
let statusBarItem: vscode.StatusBarItem;
let extensionContext: vscode.ExtensionContext | undefined;
let sessionAutoAttach: SessionAutoAttach | undefined;
let canvasEditorProvider: CanvasEditorProvider | undefined;

export async function activate(context: vscode.ExtensionContext): Promise<void> {
    extensionContext = context;
    const config = vscode.workspace.getConfiguration('agent-repl');
    sessionAutoAttach = new SessionAutoAttach(context);

    // Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
    statusBarItem.command = 'agent-repl.start';
    statusBarItem.text = '$(circle-outline) Agent REPL';
    statusBarItem.tooltip = 'Agent REPL: stopped';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    context.subscriptions.push(sessionAutoAttach);

    // Prompt badges
    const promptProvider = new PromptStatusBarProvider();
    context.subscriptions.push(
        vscode.notebooks.registerNotebookCellStatusBarItemProvider('jupyter-notebook', promptProvider)
    );

    // Activity panel
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('agent-repl.activity', new ActivityPanelProvider())
    );

    // Canvas editor for .ipynb files
    canvasEditorProvider = new CanvasEditorProvider(context);
    context.subscriptions.push(
        vscode.window.registerCustomEditorProvider(
            CanvasEditorProvider.viewType,
            canvasEditorProvider,
            { webviewOptions: { retainContextWhenHidden: true } }
        )
    );

    // Commands
    context.subscriptions.push(
        vscode.commands.registerCommand('agent-repl.start', () => startBridge(config, promptProvider)),
        vscode.commands.registerCommand('agent-repl.stop', async () => stopBridge()),
        vscode.commands.registerCommand('agent-repl.askAgent', () => insertPromptCell()),
        vscode.commands.registerCommand('agent-repl.reload', async () => reloadBridge())
    );
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration(async (event) => {
            if (event.affectsConfiguration('agent-repl.browserCanvasUrl')) {
                await canvasEditorProvider?.refreshOpenEditors();
            }
        })
    );

    // Auto-start
    if (config.get<boolean>('autoStart', true)) {
        await startBridge(config, promptProvider);
    }
}

export async function deactivate(): Promise<void> {
    await stopBridge();
}

async function reloadBridge(): Promise<void> {
    if (!server) {
        vscode.window.showInformationMessage('Agent REPL is not running.');
        return;
    }
    const reloadRoute = server.getRoute('POST /api/reload');
    if (!reloadRoute) {
        vscode.window.showErrorMessage('Agent REPL reload route is unavailable.');
        return;
    }
    try {
        const result = await reloadRoute({}, new URLSearchParams());
        await canvasEditorProvider?.refreshOpenEditors();
        vscode.window.showInformationMessage(result?.message ?? 'Agent REPL reloaded');
    } catch (err: any) {
        vscode.window.showErrorMessage(`Agent REPL reload failed: ${err?.message ?? String(err)}`);
    }
}

async function startBridge(
    config: vscode.WorkspaceConfiguration,
    promptProvider: PromptStatusBarProvider
): Promise<void> {
    if (server) {
        try {
            await sessionAutoAttach?.attachIfEnabled(config);
        } catch (err: any) {
            console.warn('[agent-repl] session auto-attach retry failed:', err?.message ?? String(err));
        }
        vscode.window.showInformationMessage(`Agent REPL already running on port ${server.port}`);
        return;
    }

    const token = generateToken();
    const routes = buildRoutes();
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
        const newRoutes = fresh.buildRoutes();
        newRoutes['POST /api/reload'] = server!.getRoute('POST /api/reload')!;
        server!.setRoutes(newRoutes);

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
                logNotebookDiagnostic(e.notebook.uri.fsPath, 'workspace.onDidChangeNotebookDocument', {
                    notebookType: e.notebook.notebookType,
                    dirty: e.notebook.isDirty,
                    cellCount: e.notebook.cellCount,
                    contentChanges: (e.contentChanges ?? []).map((change: any) => ({
                        start: change.range?.start ?? null,
                        end: change.range?.end ?? null,
                        deletedCellCount: change.range
                            ? Math.max(0, (change.range.end ?? 0) - (change.range.start ?? 0))
                            : null,
                        addedCellCount: change.addedCells?.length ?? 0,
                        addedCellKinds: (change.addedCells ?? []).map((cell: any) => cell.kind),
                        addedCellIds: (change.addedCells ?? []).map((cell: any) => cell.metadata?.custom?.['agent-repl']?.cell_id ?? null),
                    })),
                    cellChanges: (e.cellChanges ?? []).map((change: any) => ({
                        index: change.cell?.index ?? null,
                        cellId: change.cell?.metadata?.custom?.['agent-repl']?.cell_id ?? null,
                        executionSummaryChanged: change.executionSummary !== undefined,
                        documentChanged: change.document !== undefined,
                        metadataChanged: change.metadata !== undefined,
                        outputsChanged: change.outputs !== undefined,
                    })),
                });
                if (e.notebook.notebookType === 'jupyter-notebook') { promptProvider.refresh(); }
            })
        );
        context_subscriptions_push(
            vscode.workspace.onDidSaveNotebookDocument(notebook => {
                logNotebookDiagnostic(notebook.uri.fsPath, 'workspace.onDidSaveNotebookDocument', {
                    notebookType: notebook.notebookType,
                    dirty: notebook.isDirty,
                    cellCount: notebook.cellCount,
                });
            })
        );

        try {
            await sessionAutoAttach?.attachIfEnabled(config);
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

async function stopBridge(): Promise<void> {
    await sessionAutoAttach?.detachIfAttached();
    canvasEditorProvider = undefined;
    server?.dispose();
    server = undefined;
    removeConnectionFile();
    for (const d of extraDisposables) { d.dispose(); }
    extraDisposables.length = 0;
    statusBarItem.text = '$(circle-outline) Agent REPL';
    statusBarItem.tooltip = 'Agent REPL: stopped';
}

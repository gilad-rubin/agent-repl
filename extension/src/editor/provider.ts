import * as fs from 'fs';
import * as vscode from 'vscode';

// ---------------------------------------------------------------------------
// CustomDocument — the daemon owns persistence, this is a thin wrapper
// ---------------------------------------------------------------------------

class CanvasDocument implements vscode.CustomDocument {
    constructor(readonly uri: vscode.Uri) {}

    dispose(): void {
        // Nothing to clean up — daemon owns the file.
    }
}

type CanvasProxy = {
    disposables: vscode.Disposable[];
    start(): Promise<void>;
    dispose(): void;
    handleMessage(message: any): Promise<void>;
    onVisibilityChanged(visible: boolean): void;
};

type CanvasEditorHandle = {
    document: CanvasDocument;
    panel: vscode.WebviewPanel;
    proxy: CanvasProxy;
};

const openCanvasDocuments = new Map<string, number>();

function normalizeFsPath(fsPath: string): string {
    try {
        const resolved = fs.realpathSync(fsPath);
        return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
    } catch {
        return process.platform === 'win32' ? fsPath.toLowerCase() : fsPath;
    }
}

function trackCanvasDocument(fsPath: string): void {
    const normalized = normalizeFsPath(fsPath);
    openCanvasDocuments.set(normalized, (openCanvasDocuments.get(normalized) ?? 0) + 1);
}

function untrackCanvasDocument(fsPath: string): void {
    const normalized = normalizeFsPath(fsPath);
    const nextCount = (openCanvasDocuments.get(normalized) ?? 0) - 1;
    if (nextCount > 0) {
        openCanvasDocuments.set(normalized, nextCount);
        return;
    }
    openCanvasDocuments.delete(normalized);
}

export function listOpenCanvasNotebookPaths(): string[] {
    return [...openCanvasDocuments.keys()];
}

export function isCanvasNotebookOpen(fsPath: string): boolean {
    return openCanvasDocuments.has(normalizeFsPath(fsPath));
}

// ---------------------------------------------------------------------------
// CustomEditorProvider — opens .ipynb files in our Canvas WebView
// ---------------------------------------------------------------------------

export class CanvasEditorProvider implements vscode.CustomReadonlyEditorProvider<CanvasDocument> {
    static readonly viewType = 'agent-repl.canvasEditor';
    private readonly editors = new Set<CanvasEditorHandle>();

    constructor(private readonly context: vscode.ExtensionContext) {}

    openCustomDocument(uri: vscode.Uri): CanvasDocument {
        return new CanvasDocument(uri);
    }

    async resolveCustomEditor(
        document: CanvasDocument,
        webviewPanel: vscode.WebviewPanel,
        _token: vscode.CancellationToken
    ): Promise<void> {
        const handle = this.attachEditor(document, webviewPanel);
        this.editors.add(handle);
        trackCanvasDocument(document.uri.fsPath);

        webviewPanel.onDidDispose(() => {
            handle.proxy.dispose();
            this.editors.delete(handle);
            untrackCanvasDocument(document.uri.fsPath);
        });
    }

    async refreshOpenEditors(): Promise<void> {
        for (const handle of this.editors) {
            handle.proxy.dispose();
            handle.proxy = this.mountProxy(handle.document, handle.panel);
        }
    }

    private attachEditor(
        document: CanvasDocument,
        webviewPanel: vscode.WebviewPanel,
    ): CanvasEditorHandle {
        const mediaRoot = vscode.Uri.joinPath(this.context.extensionUri, 'media');

        webviewPanel.webview.options = {
            enableScripts: true,
            localResourceRoots: [mediaRoot],
        };

        return {
            document,
            panel: webviewPanel,
            proxy: this.mountProxy(document, webviewPanel),
        };
    }

    private mountProxy(
        document: CanvasDocument,
        webviewPanel: vscode.WebviewPanel,
    ): CanvasProxy {
        // Dynamic require so `agent-repl reload` picks up changes without window restart
        const { buildWebviewHtml } = require('./webview') as {
            buildWebviewHtml: (
                webview: vscode.Webview,
                extensionUri: vscode.Uri,
                options?: { browserCanvasUrl?: string }
            ) => string;
        };
        const { DaemonProxy } = require('./proxy') as {
            DaemonProxy: new (documentUri: vscode.Uri, panel: vscode.WebviewPanel, context: vscode.ExtensionContext) => CanvasProxy;
        };

        webviewPanel.webview.html = buildWebviewHtml(webviewPanel.webview, this.context.extensionUri, {
            browserCanvasUrl: vscode.workspace
                .getConfiguration('agent-repl')
                .get<string>('browserCanvasUrl', ''),
        });

        const proxy = new DaemonProxy(document.uri, webviewPanel, this.context);

        webviewPanel.webview.onDidReceiveMessage(
            msg => proxy.handleMessage(msg),
            undefined,
            proxy.disposables
        );

        webviewPanel.onDidChangeViewState(
            () => proxy.onVisibilityChanged(webviewPanel.visible),
            undefined,
            proxy.disposables
        );

        void proxy.start();
        return proxy;
    }
}

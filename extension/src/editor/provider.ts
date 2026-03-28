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

// ---------------------------------------------------------------------------
// CustomEditorProvider — opens .ipynb files in our Canvas WebView
// ---------------------------------------------------------------------------

export class CanvasEditorProvider implements vscode.CustomReadonlyEditorProvider<CanvasDocument> {
    static readonly viewType = 'agent-repl.canvasEditor';

    constructor(private readonly context: vscode.ExtensionContext) {}

    openCustomDocument(uri: vscode.Uri): CanvasDocument {
        return new CanvasDocument(uri);
    }

    async resolveCustomEditor(
        document: CanvasDocument,
        webviewPanel: vscode.WebviewPanel,
        _token: vscode.CancellationToken
    ): Promise<void> {
        const mediaRoot = vscode.Uri.joinPath(this.context.extensionUri, 'media');

        webviewPanel.webview.options = {
            enableScripts: true,
            localResourceRoots: [mediaRoot],
        };

        // Dynamic require so `agent-repl reload` picks up changes without window restart
        const { buildWebviewHtml } = require('./webview') as { buildWebviewHtml: (w: vscode.Webview, u: vscode.Uri) => string };
        const { DaemonProxy } = require('./proxy') as { DaemonProxy: new (...args: any[]) => any };

        webviewPanel.webview.html = buildWebviewHtml(webviewPanel.webview, this.context.extensionUri);

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

        webviewPanel.onDidDispose(() => proxy.dispose());

        // Kick off initial load
        proxy.start();
    }
}

import * as vscode from 'vscode';
import { onActivity, getActivityEvents } from '../routes';

export class ActivityPanelProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private disposable?: vscode.Disposable;

    resolveWebviewView(webviewView: vscode.WebviewView): void {
        this.view = webviewView;
        webviewView.webview.options = { enableScripts: true };
        webviewView.webview.html = this.buildHtml(getActivityEvents());

        this.disposable = onActivity((event) => {
            webviewView.webview.postMessage({ type: 'activity', event });
        });

        webviewView.onDidDispose(() => {
            this.disposable?.dispose();
            this.disposable = undefined;
            this.view = undefined;
        });
    }

    private buildHtml(events: any[]): string {
        const rows = events.map(e => this.eventRow(e)).join('');
        return `<!DOCTYPE html>
<html>
<head>
<style>
    body {
        font-family: var(--vscode-font-family);
        font-size: var(--vscode-font-size);
        color: var(--vscode-foreground);
        background: var(--vscode-sideBar-background);
        padding: 8px;
        margin: 0;
    }
    .event {
        padding: 6px 8px;
        border-bottom: 1px solid var(--vscode-widget-border, rgba(128,128,128,0.2));
        word-break: break-word;
    }
    .event .time {
        color: var(--vscode-descriptionForeground);
        font-size: 0.85em;
        margin-right: 6px;
    }
    .event .type {
        font-weight: bold;
        margin-right: 6px;
    }
    .empty {
        color: var(--vscode-descriptionForeground);
        font-style: italic;
        padding: 16px 8px;
    }
    #container { overflow-y: auto; }
</style>
</head>
<body>
<div id="container">
    ${rows || '<div class="empty">No activity yet</div>'}
</div>
<script>
    const container = document.getElementById('container');
    const vscode = acquireVsCodeApi();

    function formatTime(ts) {
        if (!ts) return '';
        const d = new Date(ts);
        return d.toLocaleTimeString();
    }

    function renderEvent(e) {
        const div = document.createElement('div');
        div.className = 'event';
        const time = e.timestamp || e.ts || '';
        const type = e.type || e.event || 'event';
        const detail = e.message || e.detail || e.path || JSON.stringify(e);
        div.innerHTML = '<span class="time">' + formatTime(time) + '</span>'
            + '<span class="type">' + type + '</span>'
            + '<span class="detail">' + detail + '</span>';
        return div;
    }

    window.addEventListener('message', (msg) => {
        if (msg.data?.type === 'activity') {
            const empty = container.querySelector('.empty');
            if (empty) { empty.remove(); }
            container.appendChild(renderEvent(msg.data.event));
            container.scrollTop = container.scrollHeight;
        }
    });
</script>
</body>
</html>`;
    }

    private eventRow(e: any): string {
        const type = e.type || e.event || 'event';
        const detail = e.message || e.detail || e.path || '';
        const time = e.timestamp || e.ts || '';
        return `<div class="event"><span class="time">${time}</span><span class="type">${type}</span><span class="detail">${detail}</span></div>`;
    }
}

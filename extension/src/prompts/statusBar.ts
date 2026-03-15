import * as vscode from 'vscode';

const NS = 'agent-repl';

export class PromptStatusBarProvider implements vscode.NotebookCellStatusBarItemProvider {
    private _onDidChange = new vscode.EventEmitter<void>();
    readonly onDidChangeCellStatusBarItems = this._onDidChange.event;

    refresh(): void { this._onDidChange.fire(); }

    provideCellStatusBarItems(cell: vscode.NotebookCell): vscode.NotebookCellStatusBarItem[] {
        const ar = (cell.metadata as any)?.custom?.[NS];
        if (!ar?.type) { return []; }

        if (ar.type === 'prompt') {
            const labels: Record<string, string> = {
                'pending': '\u{1F916} Agent Prompt \u00B7 \u23F3 Pending',
                'in-progress': '\u{1F916} Agent Prompt \u00B7 \u2699\uFE0F Working...',
                'answered': '\u{1F916} Agent Prompt \u00B7 \u2705 Answered',
            };
            return [new vscode.NotebookCellStatusBarItem(
                labels[ar.status] ?? '\u{1F916} Agent Prompt',
                vscode.NotebookCellStatusBarAlignment.Left
            )];
        }

        if (ar.type === 'response') {
            return [new vscode.NotebookCellStatusBarItem(
                '\u{1F916} Agent Response',
                vscode.NotebookCellStatusBarAlignment.Left
            )];
        }

        if (ar.type === 'agent-run') {
            const labels: Record<string, string> = {
                'running': '\u{1F916} Agent Run \u00B7 \u2699\uFE0F Working...',
                'error': '\u{1F916} Agent Run \u00B7 \u274C Failed',
            };
            return [new vscode.NotebookCellStatusBarItem(
                labels[ar.run_status] ?? '\u{1F916} Agent Run',
                vscode.NotebookCellStatusBarAlignment.Left
            )];
        }

        return [];
    }
}

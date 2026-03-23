import * as vscode from 'vscode';

export const AGENT_REPL_OUTPUT_METADATA_KEY = 'agent-repl';

// --- Jupyter → VS Code ---

export interface JupyterOutput {
    output_type: string;
    name?: string;
    text?: string;
    data?: Record<string, any>;
    metadata?: Record<string, any>;
    transient?: Record<string, any>;
    execution_count?: number;
    ename?: string;
    evalue?: string;
    traceback?: string[];
    wait?: boolean;
}

export function toVSCode(output: JupyterOutput): vscode.NotebookCellOutput {
    switch (output.output_type) {
        case 'stream': {
            const mime = output.name === 'stderr'
                ? 'application/vnd.code.notebook.stderr'
                : 'application/vnd.code.notebook.stdout';
            return new vscode.NotebookCellOutput([
                vscode.NotebookCellOutputItem.text(output.text ?? '', mime)
            ]);
        }
        case 'execute_result':
        case 'update_display_data':
        case 'display_data': {
            const items: vscode.NotebookCellOutputItem[] = [];
            for (const [mime, val] of Object.entries(output.data ?? {})) {
                if (mime.startsWith('image/') && mime !== 'image/svg+xml') {
                    items.push(new vscode.NotebookCellOutputItem(Buffer.from(val as string, 'base64'), mime));
                } else {
                    items.push(vscode.NotebookCellOutputItem.text(typeof val === 'string' ? val : JSON.stringify(val), mime));
                }
            }
            if (!items.length) { items.push(vscode.NotebookCellOutputItem.text('', 'text/plain')); }
            return new vscode.NotebookCellOutput(items, buildNotebookOutputMetadata(output));
        }
        case 'error':
            return new vscode.NotebookCellOutput([
                vscode.NotebookCellOutputItem.error(
                    new Error(`${output.ename ?? 'Error'}: ${output.evalue ?? ''}\n${(output.traceback ?? []).join('\n')}`)
                )
            ]);
        default:
            return new vscode.NotebookCellOutput([
                vscode.NotebookCellOutputItem.text(JSON.stringify(output), 'text/plain')
            ]);
    }
}

// --- VS Code → Jupyter (for API responses) ---

export function toJupyter(cell: vscode.NotebookCell): any[] {
    if (cell.kind !== vscode.NotebookCellKind.Code) { return []; }
    return cell.outputs.map(output => {
        const first = output.items[0];
        if (!first) { return { output_type: 'stream', name: 'stdout', text: '' }; }
        const m = first.mime;
        if (m === 'application/vnd.code.notebook.stdout') {
            return { output_type: 'stream', name: 'stdout', text: buf(first) };
        }
        if (m === 'application/vnd.code.notebook.stderr') {
            return { output_type: 'stream', name: 'stderr', text: buf(first) };
        }
        if (m === 'application/vnd.code.notebook.error') {
            try { const e = JSON.parse(buf(first)); return { output_type: 'error', ename: e.name, evalue: e.message, traceback: [] }; }
            catch { return { output_type: 'error', ename: 'Error', evalue: '', traceback: [] }; }
        }
        const data: Record<string, any> = {};
        for (const item of output.items) {
            data[item.mime] = item.mime.startsWith('image/') && item.mime !== 'image/svg+xml'
                ? Buffer.from(item.data).toString('base64')
                : buf(item);
        }
        return { output_type: 'display_data', data, metadata: {} };
    });
}

function buf(item: vscode.NotebookCellOutputItem): string {
    return Buffer.from(item.data).toString('utf-8');
}

function buildNotebookOutputMetadata(output: JupyterOutput): Record<string, any> | undefined {
    const metadata = output.metadata && typeof output.metadata === 'object'
        ? { ...output.metadata }
        : {};
    const transient = output.transient && typeof output.transient === 'object'
        ? output.transient
        : undefined;
    if (transient) {
        const existing = metadata.transient;
        metadata.transient = existing && typeof existing === 'object'
            ? { ...existing, ...transient }
            : { ...transient };
    }

    const internal: Record<string, any> = {};
    if (typeof output.transient?.display_id === 'string') {
        internal.display_id = output.transient.display_id;
    }
    if (output.output_type === 'execute_result') {
        internal.output_type = output.output_type;
    }
    if (Object.keys(internal).length > 0) {
        metadata[AGENT_REPL_OUTPUT_METADATA_KEY] = internal;
    }

    return Object.keys(metadata).length > 0 ? metadata : undefined;
}

// --- Strip for agent responses ---

const IMAGE_MIMES = new Set(['image/png', 'image/jpeg', 'image/svg+xml']);
const WIDGET_PREFIX = 'application/vnd.jupyter.widget';
const CAP_BYTES = 256 * 1024;

/** Strip rich media from Jupyter outputs for agent consumption. */
export function stripForAgent(outputs: any[]): any[] {
    return outputs.map(o => {
        if ((o.output_type === 'display_data' || o.output_type === 'execute_result') && o.data) {
            const data: Record<string, any> = {};
            const hasPlain = 'text/plain' in o.data;
            for (const [mime, val] of Object.entries(o.data)) {
                if (mime === 'text/html' && hasPlain) { continue; }
                if (IMAGE_MIMES.has(mime)) { data[mime] = `[image: ${mime}]`; continue; }
                if (mime.startsWith(WIDGET_PREFIX)) { data[mime] = '[widget]'; continue; }
                if (hasPlain && typeof val === 'string' && val.length > CAP_BYTES && mime !== 'text/plain') {
                    data[mime] = `[capped ${mime}: ${val.length} bytes]`;
                    continue;
                }
                data[mime] = val;
            }
            return { ...o, data };
        }
        return o;
    });
}

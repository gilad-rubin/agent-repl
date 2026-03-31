import * as vscode from 'vscode';
import { AGENT_REPL_OUTPUT_METADATA_KEY, JupyterOutput } from '../notebook/outputs';

// -- Output helpers (used by webview/renderer) --------------------------------

export function iopubMessageToJupyterOutput(msg: any): JupyterOutput | undefined {
    const type = msg?.header?.msg_type;
    const content = msg?.content ?? {};
    if (!type) { return undefined; }

    if (type === 'stream') {
        return { output_type: 'stream', name: content.name, text: content.text ?? '' };
    }
    if (type === 'execute_result') {
        return {
            output_type: 'execute_result',
            data: content.data ?? {},
            metadata: content.metadata ?? {},
            transient: content.transient ?? {},
            execution_count: content.execution_count,
        };
    }
    if (type === 'display_data') {
        return {
            output_type: 'display_data',
            data: content.data ?? {},
            metadata: content.metadata ?? {},
            transient: content.transient ?? {},
        };
    }
    if (type === 'update_display_data') {
        return {
            output_type: 'update_display_data',
            data: content.data ?? {},
            metadata: content.metadata ?? {},
            transient: content.transient ?? {},
        };
    }
    if (type === 'clear_output') {
        return { output_type: 'clear_output', wait: content.wait === true };
    }
    if (type === 'error') {
        return {
            output_type: 'error',
            ename: content.ename,
            evalue: content.evalue,
            traceback: Array.isArray(content.traceback) ? content.traceback : [],
        };
    }
    return undefined;
}

export function applyNotebookOutput(
    outputs: vscode.NotebookCellOutput[],
    next: vscode.NotebookCellOutput
): vscode.NotebookCellOutput[] {
    const displayId = getNotebookDisplayId(next);
    if (!displayId) {
        return [...outputs, next];
    }
    const index = outputs.findIndex(output => getNotebookDisplayId(output) === displayId);
    if (index === -1) {
        return [...outputs, next];
    }
    const updated = outputs.slice();
    updated[index] = next;
    return updated;
}

export function applyJupyterOutput(outputs: JupyterOutput[], next: JupyterOutput): JupyterOutput[] {
    if (next.output_type !== 'update_display_data') {
        return [...outputs, next];
    }

    const displayId = getJupyterDisplayId(next);
    const normalized: JupyterOutput = { ...next, output_type: 'display_data' };
    if (!displayId) {
        return [...outputs, normalized];
    }

    const index = outputs.findIndex(output => getJupyterDisplayId(output) === displayId);
    if (index === -1) {
        return [...outputs, normalized];
    }

    const existing = outputs[index];
    const updated = outputs.slice();
    updated[index] = {
        ...existing,
        ...normalized,
        output_type: existing.output_type === 'execute_result' ? 'execute_result' : normalized.output_type,
        data: normalized.data ?? existing.data,
        metadata: normalized.metadata ?? existing.metadata,
        transient: { ...(existing.transient ?? {}), ...(normalized.transient ?? {}) },
    };
    return updated;
}

function getNotebookDisplayId(output: vscode.NotebookCellOutput | undefined): string | undefined {
    const transient = output?.metadata?.transient;
    if (transient && typeof transient === 'object' && typeof transient.display_id === 'string') {
        return transient.display_id;
    }
    const internal = output?.metadata?.[AGENT_REPL_OUTPUT_METADATA_KEY];
    if (internal && typeof internal === 'object' && typeof internal.display_id === 'string') {
        return internal.display_id;
    }
    return undefined;
}

function getJupyterDisplayId(output: JupyterOutput | undefined): string | undefined {
    return typeof output?.transient?.display_id === 'string'
        ? output.transient.display_id
        : undefined;
}

// -- Jupyter API (kept for kernel selection in routes.ts) ---------------------

let cachedJupyterApi: any | undefined;

export async function getJupyterApi(): Promise<any | undefined> {
    if (cachedJupyterApi) { return cachedJupyterApi; }
    const jupyterExt = vscode.extensions.getExtension('ms-toolsai.jupyter');
    if (!jupyterExt) { return undefined; }
    cachedJupyterApi = jupyterExt.isActive ? jupyterExt.exports : await jupyterExt.activate();
    return cachedJupyterApi;
}

export function resetJupyterApiCache(): void {
    cachedJupyterApi = undefined;
}

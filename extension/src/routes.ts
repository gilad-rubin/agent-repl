import * as vscode from 'vscode';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';
import { Routes } from './server';
import { resolveNotebook, resolveNotebookUri, resolveOrOpenNotebook, findOpenNotebook, findEditor, ensureNotebookEditor, captureEditorFocus, restoreEditorFocus } from './notebook/resolver';
import { applyEdits, EditOp } from './notebook/operations';
import { getCellId, ensureIds, resolveCell, withCellId, newCellId } from './notebook/identity';
import { toJupyter, stripForAgent } from './notebook/outputs';
import { executeCell, getExecution, getStatus, insertAndExecute, resetExecutionState, resetJupyterApiCache, getJupyterApi } from './execution/queue';

type KernelRecord = {
    id: string;
    label: string;
    type: 'workspace-venv' | 'kernelspec';
    python: string | null;
    kernelspec_name: string | null;
    kernelspec_display_name: string | null;
    source: string;
    recommended: boolean;
};

type KernelDiscovery = {
    workspace: string | null;
    workspace_venv_python: string | null;
    preferred_kernel: KernelRecord | null;
    kernels: KernelRecord[];
};

function normalizePath(p: string): string {
    try {
        const resolved = fs.realpathSync(p);
        return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
    } catch {
        const resolved = path.resolve(p);
        return process.platform === 'win32' ? resolved.toLowerCase() : resolved;
    }
}

function samePath(a: string | null | undefined, b: string | null | undefined): boolean {
    if (!a || !b) { return false; }
    return normalizePath(a) === normalizePath(b);
}

function shellQuote(arg: string): string {
    return `'${arg.replace(/'/g, `'\\''`)}'`;
}

function jupyterKernelDirs(): string[] {
    const dirs = new Set<string>();
    const home = os.homedir();

    if (process.platform === 'darwin') {
        dirs.add(path.join(home, 'Library', 'Jupyter', 'kernels'));
        dirs.add('/Library/Jupyter/kernels');
        dirs.add('/usr/local/share/jupyter/kernels');
        dirs.add('/opt/homebrew/share/jupyter/kernels');
        dirs.add('/opt/miniconda3/share/jupyter/kernels');
        dirs.add('/usr/share/jupyter/kernels');
    } else if (process.platform === 'win32') {
        const appData = process.env.APPDATA;
        if (appData) {
            dirs.add(path.join(appData, 'jupyter', 'kernels'));
        }
        const programData = process.env.PROGRAMDATA;
        if (programData) {
            dirs.add(path.join(programData, 'jupyter', 'kernels'));
        }
    } else {
        dirs.add(path.join(home, '.local', 'share', 'jupyter', 'kernels'));
        dirs.add('/usr/local/share/jupyter/kernels');
        dirs.add('/usr/share/jupyter/kernels');
    }

    for (const entry of (process.env.JUPYTER_PATH ?? '').split(path.delimiter)) {
        if (entry) {
            dirs.add(path.join(entry, 'kernels'));
        }
    }

    return [...dirs].filter(dir => fs.existsSync(dir));
}

/**
 * Resolve the workspace directory for path resolution and kernel discovery.
 * If `cwd` is provided (from the CLI's working directory), find the matching
 * VS Code workspace folder, or use `cwd` directly. Falls back to workspaceFolders[0].
 */
function resolveWorkspaceDir(cwd?: string): string | null {
    const folders = vscode.workspace.workspaceFolders ?? [];
    if (cwd) {
        const normalCwd = normalizePath(cwd);
        // Find the workspace folder that contains (or is) the cwd
        for (const folder of folders) {
            const normalFolder = normalizePath(folder.uri.fsPath);
            if (normalCwd === normalFolder || normalCwd.startsWith(normalFolder + path.sep)) {
                return folder.uri.fsPath;
            }
        }
        // cwd doesn't match any workspace folder — use it directly
        return cwd;
    }
    return folders[0]?.uri.fsPath ?? null;
}

function discoverKernels(workspaceDir?: string | null): KernelDiscovery {
    const workspace = workspaceDir ?? null;
    const workspaceVenvPython = workspace
        ? path.join(workspace, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin', process.platform === 'win32' ? 'python.exe' : 'python')
        : null;
    const hasWorkspaceVenv = !!workspaceVenvPython && fs.existsSync(workspaceVenvPython);
    const kernels: KernelRecord[] = [];

    const pushKernel = (kernel: KernelRecord) => {
        if (!kernels.some(existing => existing.type === kernel.type && samePath(existing.python, kernel.python) && existing.kernelspec_name === kernel.kernelspec_name)) {
            kernels.push(kernel);
        }
    };

    for (const kernelsDir of jupyterKernelDirs()) {
        let entries: fs.Dirent[];
        try {
            entries = fs.readdirSync(kernelsDir, { withFileTypes: true });
        } catch {
            continue;
        }

        for (const entry of entries) {
            if (!entry.isDirectory()) { continue; }
            const kernelspecDir = path.join(kernelsDir, entry.name);
            const kernelJson = path.join(kernelspecDir, 'kernel.json');
            if (!fs.existsSync(kernelJson)) { continue; }

            try {
                const spec = JSON.parse(fs.readFileSync(kernelJson, 'utf8')) as {
                    argv?: string[];
                    display_name?: string;
                    language?: string;
                };
                const python = Array.isArray(spec.argv) && typeof spec.argv[0] === 'string' ? spec.argv[0] : null;
                const matchesWorkspaceVenv = hasWorkspaceVenv && samePath(python, workspaceVenvPython);
                pushKernel({
                    id: entry.name,
                    label: spec.display_name ?? entry.name,
                    type: 'kernelspec',
                    python,
                    kernelspec_name: entry.name,
                    kernelspec_display_name: spec.display_name ?? entry.name,
                    source: kernelspecDir,
                    recommended: !!matchesWorkspaceVenv,
                });
            } catch {
                continue;
            }
        }
    }

    let preferredKernel: KernelRecord | null = null;

    if (hasWorkspaceVenv && workspaceVenvPython) {
        const matchingSpec = kernels.find(kernel => kernel.type === 'kernelspec' && samePath(kernel.python, workspaceVenvPython)) ?? null;
        preferredKernel = {
            id: workspaceVenvPython,
            label: matchingSpec?.label ?? '.venv (workspace)',
            type: 'workspace-venv',
            python: workspaceVenvPython,
            kernelspec_name: matchingSpec?.kernelspec_name ?? null,
            kernelspec_display_name: matchingSpec?.kernelspec_display_name ?? '.venv (workspace)',
            source: matchingSpec?.source ?? workspaceVenvPython,
            recommended: true,
        };
        pushKernel(preferredKernel);
    }

    kernels.sort((a, b) => {
        if (a.recommended !== b.recommended) {
            return a.recommended ? -1 : 1;
        }
        return a.label.localeCompare(b.label);
    });

    return {
        workspace,
        workspace_venv_python: hasWorkspaceVenv ? workspaceVenvPython : null,
        preferred_kernel: preferredKernel,
        kernels,
    };
}

function kernelSelectionGuidance(relPath: string, discovery: KernelDiscovery, reason: 'missing' | 'failed') {
    const command = `agent-repl select-kernel ${shellQuote(relPath)}`;
    const intro = reason === 'failed'
        ? 'agent-repl could not attach a kernel automatically. Select one to continue.'
        : 'No workspace .venv was detected. Select a kernel to continue.';

    return {
        message: intro,
        selection_required: true,
        available_kernels: discovery.kernels,
        select_kernel_command: command,
        next_step: `Run ${command} and choose one of the available kernels in VS Code.`,
    };
}

function rawCellSource(source: unknown): string {
    if (Array.isArray(source)) {
        return source.map(part => `${part ?? ''}`).join('');
    }
    return typeof source === 'string' ? source : '';
}

function rawCellId(metadata: Record<string, any> | undefined, fallback: string): string {
    return metadata?.custom?.['agent-repl']?.cell_id ?? metadata?.custom?.id ?? metadata?.id ?? fallback;
}

async function readNotebookContents(relPath: string, cwd?: string): Promise<{ path: string; cells: any[] }> {
    const openDoc = findOpenNotebook(relPath, cwd);
    if (openDoc) {
        await ensureIds(openDoc);

        const cells = [];
        let codeIndex = 0;
        for (let i = 0; i < openDoc.cellCount; i++) {
            const cell = openDoc.cellAt(i);
            const isCode = cell.kind === vscode.NotebookCellKind.Code;
            const outputs = toJupyter(cell);
            cells.push({
                index: i,
                display_number: isCode ? ++codeIndex : null,
                cell_id: getCellId(cell) ?? `index-${i}`,
                cell_type: isCode ? 'code' : 'markdown',
                source: cell.document.getText(),
                outputs: stripForAgent(outputs),
                execution_count: cell.executionSummary?.executionOrder ?? null,
                metadata: cell.metadata
            });
        }
        return { path: relPath, cells };
    }

    const uri = resolveNotebookUri(relPath, cwd);
    let data: Uint8Array;
    try {
        data = await vscode.workspace.fs.readFile(uri);
    } catch (err: any) {
        const wrapped = new Error(`Notebook '${relPath}' was not found`) as any;
        wrapped.statusCode = err?.code === 'FileNotFound' ? 404 : err?.statusCode ?? 500;
        throw wrapped;
    }

    let notebook: { cells?: Array<Record<string, any>> };
    try {
        notebook = JSON.parse(Buffer.from(data).toString('utf8'));
    } catch {
        const err = new Error(`Notebook '${relPath}' is not valid JSON`) as any;
        err.statusCode = 400;
        throw err;
    }

    const cells = [];
    let codeIndex = 0;
    for (const [index, cell] of (notebook.cells ?? []).entries()) {
        const cellType = cell.cell_type === 'markdown' ? 'markdown' : 'code';
        const isCode = cellType === 'code';
        cells.push({
            index,
            display_number: isCode ? ++codeIndex : null,
            cell_id: rawCellId(cell.metadata, `index-${index}`),
            cell_type: cellType,
            source: rawCellSource(cell.source),
            outputs: stripForAgent(Array.isArray(cell.outputs) ? cell.outputs : []),
            execution_count: cell.execution_count ?? null,
            metadata: cell.metadata ?? {}
        });
    }
    return { path: relPath, cells };
}

export function buildRoutes(maxQueue: number): Routes {
    return {
        // --- Health ---
        'GET /api/health': async () => ({
            status: 'ok',
            version: '0.2.0',
            extension_root: path.resolve(__dirname, '..'),
            routes_module: __filename,
            open_notebooks: vscode.workspace.notebookDocuments
                .filter(d => d.notebookType === 'jupyter-notebook')
                .map(d => d.uri.fsPath)
        }),

        'GET /api/debug/jupyter-api': async (_body, q) => {
            const jupyterExt = vscode.extensions.getExtension('ms-toolsai.jupyter');
            if (!jupyterExt) {
                return { installed: false };
            }

            const api = jupyterExt.isActive ? jupyterExt.exports : await jupyterExt.activate();
            const apiAny = api as Record<string, any>;
            const kernels = apiAny?.kernels as Record<string, any> | undefined;
            const kernelServiceFactory = apiAny?.getKernelService;
            const relPath = q.get('path');

            let notebookProbe: Record<string, any> | undefined;
            if (relPath && typeof kernels?.getKernel === 'function') {
                const doc = resolveNotebook(relPath);
                const attempts = [
                    ['document', doc],
                    ['uri', doc.uri],
                    ['fsPath', doc.uri.fsPath],
                ] as const;

                notebookProbe = {};
                for (const [name, arg] of attempts) {
                    try {
                        const kernel = await kernels.getKernel(arg);
                        notebookProbe[name] = kernel
                            ? {
                                type: typeof kernel,
                                keys: Object.keys(kernel).sort(),
                                executeCodeType: typeof kernel.executeCode,
                                status: kernel.status,
                                hasKernelConnectionMetadata: !!kernel.kernelConnectionMetadata,
                            }
                            : null;
                    } catch (err: any) {
                        notebookProbe[name] = { error: err?.message ?? String(err) };
                    }
                }

                const code = q.get('code');
                if (code) {
                    try {
                        const kernel = await kernels.getKernel(doc.uri);
                        const outputs = [];
                        for await (const output of kernel.executeCode(code)) {
                            outputs.push({
                                items: Array.isArray(output?.items) ? output.items.length : 0,
                                metadata: output?.metadata ?? {},
                            });
                        }
                        notebookProbe.executeCode = { status: 'ok', outputs };
                    } catch (err: any) {
                        notebookProbe.executeCode = { error: err?.message ?? String(err) };
                    }
                }
            }

            let kernelServiceProbe: Record<string, any> | undefined;
            if (relPath && typeof kernelServiceFactory === 'function') {
                const doc = resolveNotebook(relPath);
                kernelServiceProbe = {};
                try {
                    const service = await kernelServiceFactory();
                    kernelServiceProbe.serviceKeys = Object.keys(service ?? {}).sort();
                    kernelServiceProbe.getKernelType = typeof service?.getKernel;
                    if (typeof service?.getKernel === 'function') {
                        const result = await service.getKernel(doc.uri);
                        kernelServiceProbe.kernel = result
                            ? {
                                keys: Object.keys(result).sort(),
                                metadataKeys: Object.keys(result.metadata ?? {}).sort(),
                                connectionKeys: Object.keys(result.connection ?? {}).sort(),
                                hasKernel: !!result.connection?.kernel,
                                kernelKeys: Object.keys(result.connection?.kernel ?? {}).sort(),
                                requestExecuteType: typeof result.connection?.kernel?.requestExecute,
                            }
                            : null;
                    }
                } catch (err: any) {
                    kernelServiceProbe.error = err?.message ?? String(err);
                }
            }

            return {
                installed: true,
                active: jupyterExt.isActive,
                extensionPath: jupyterExt.extensionPath,
                exportKeys: Object.keys(apiAny ?? {}).sort(),
                kernelsKeys: Object.keys(kernels ?? {}).sort(),
                getKernelType: typeof kernels?.getKernel,
                openNotebookType: typeof apiAny?.openNotebook,
                getKernelServiceType: typeof apiAny?.getKernelService,
                notebookProbe,
                kernelServiceProbe,
            };
        },

        // --- Read ---
        'GET /api/notebook/contents': async (_body, q) => {
            const relPath = q.get('path');
            const cwd = q.get('cwd') ?? undefined;
            if (!relPath) { throw new Error('Missing ?path='); }
            return readNotebookContents(relPath, cwd);
        },

        // --- Status ---
        'GET /api/notebook/status': async (_body, q) => {
            const relPath = q.get('path');
            const cwd = q.get('cwd') ?? undefined;
            if (!relPath) { throw new Error('Missing ?path='); }
            const doc = findOpenNotebook(relPath, cwd);
            if (!doc) {
                return {
                    path: relPath,
                    open: false,
                    kernel_state: 'not_open',
                    busy: false,
                    running: [],
                    queued: [],
                };
            }
            const status = await getStatus(doc.uri.fsPath);
            return { ...status, path: relPath, open: true };
        },

        // --- Edit ---
        'POST /api/notebook/edit': async (body) => {
            const { path, cwd, operations } = body as { path: string; cwd?: string; operations: EditOp[] };
            if (!path || !operations?.length) { throw new Error('Missing path or operations'); }
            const doc = await resolveOrOpenNotebook(path, cwd);
            await ensureIds(doc);
            const results = await applyEdits(doc, operations);
            return { path, results };
        },

        // --- Execute ---
        'POST /api/notebook/execute-cell': async (body) => {
            const { path, cwd, cell_id, cell_index } = body as {
                path: string; cwd?: string; cell_id?: string; cell_index?: number;
            };
            const doc = await resolveOrOpenNotebook(path, cwd);
            return executeCell(doc.uri.fsPath, { cell_id, cell_index }, maxQueue);
        },

        'GET /api/notebook/execution': async (_body, q) => {
            const id = q.get('id');
            if (!id) { throw new Error('Missing ?id='); }
            return getExecution(id);
        },

        'POST /api/notebook/insert-and-execute': async (body) => {
            const { path, cwd, source, cell_type, at_index } = body as {
                path: string; cwd?: string; source: string; cell_type?: string; at_index?: number;
            };
            const doc = await resolveOrOpenNotebook(path, cwd);
            return insertAndExecute(doc.uri.fsPath, source, cell_type ?? 'code', at_index ?? -1, maxQueue);
        },

        // --- Lifecycle ---
        'POST /api/notebook/execute-all': async (body) => {
            const { path, cwd } = body as { path: string; cwd?: string };
            const doc = await resolveOrOpenNotebook(path, cwd);
            await vscode.window.showNotebookDocument(doc);
            await vscode.commands.executeCommand('notebook.execute');
            // Collect results
            const cells = [];
            for (let i = 0; i < doc.cellCount; i++) {
                const cell = doc.cellAt(i);
                if (cell.kind === vscode.NotebookCellKind.Code) {
                    cells.push({
                        index: i,
                        cell_id: getCellId(cell),
                        outputs: stripForAgent(toJupyter(cell)),
                        execution_count: cell.executionSummary?.executionOrder ?? null
                    });
                }
            }
            return { status: 'ok', path, cells };
        },

        'POST /api/notebook/restart-kernel': async (body) => {
            const { path, cwd } = body as { path: string; cwd?: string };
            const doc = await resolveOrOpenNotebook(path, cwd);
            const focus = captureEditorFocus();
            try {
                await ensureNotebookEditor(doc, { preserveFocus: true, preview: false });
                await restartKernel(doc.uri);
            } finally {
                resetExecutionState();
                resetJupyterApiCache();
                await restoreEditorFocus(focus);
            }
            return { status: 'ok', path };
        },

        'POST /api/notebook/restart-and-run-all': async (body) => {
            const { path, cwd } = body as { path: string; cwd?: string };
            const doc = await resolveOrOpenNotebook(path, cwd);
            const focus = captureEditorFocus();
            try {
                await ensureNotebookEditor(doc, { preserveFocus: true, preview: false });
                await restartKernel(doc.uri);
                resetExecutionState();
                resetJupyterApiCache();
                // notebook.execute requires the notebook to be active
                await vscode.window.showNotebookDocument(doc, { preserveFocus: true });
                await vscode.commands.executeCommand('notebook.execute');
            } finally {
                await restoreEditorFocus(focus);
            }
            const cells = [];
            for (let i = 0; i < doc.cellCount; i++) {
                const cell = doc.cellAt(i);
                if (cell.kind === vscode.NotebookCellKind.Code) {
                    cells.push({
                        index: i, cell_id: getCellId(cell),
                        outputs: stripForAgent(toJupyter(cell)),
                        execution_count: cell.executionSummary?.executionOrder ?? null
                    });
                }
            }
            return { status: 'ok', path, cells };
        },

        'POST /api/notebook/select-kernel': async (body) => {
            const { path: relPath, cwd, kernel_id, extension: kernelExt } = body as {
                path: string; cwd?: string; kernel_id?: string; extension?: string;
            };
            const doc = await resolveOrOpenNotebook(relPath, cwd);

            if (kernel_id) {
                const editor = await ensureNotebookEditor(doc, {
                    preserveFocus: true,
                    preview: false,
                });
                await vscode.commands.executeCommand('notebook.selectKernel', {
                    id: kernel_id,
                    extension: kernelExt ?? 'ms-toolsai.jupyter',
                    notebookEditor: editor,
                });
                return { status: 'ok', path: relPath, kernel_id, method: 'programmatic' };
            }

            await vscode.window.showNotebookDocument(doc);
            await vscode.commands.executeCommand('notebook.selectKernel');
            return { status: 'ok', path: relPath, method: 'interactive' };
        },

        'GET /api/notebook/kernels': async (_body, q) => {
            const cwd = q.get('cwd') ?? undefined;
            const workspace = resolveWorkspaceDir(cwd);
            const discovery = discoverKernels(workspace);
            return {
                kernels: discovery.kernels,
                workspace: discovery.workspace,
                workspace_venv_python: discovery.workspace_venv_python,
                preferred_kernel: discovery.preferred_kernel,
            };
        },

        'POST /api/notebook/create': async (body) => {
            const { path: relPath, kernel_name, kernel_id, cells, cwd } = body as {
                path: string; kernel_name?: string; kernel_id?: string;
                cells?: Array<{ type: string; source: string }>; cwd?: string;
            };
            const workspace = resolveWorkspaceDir(cwd);
            if (!workspace) { throw new Error('No workspace folder open and no cwd provided'); }
            const uri = vscode.Uri.file(path.resolve(workspace, relPath));
            const nbCells = (cells ?? []).map(c => ({
                cell_type: c.type === 'code' ? 'code' : 'markdown',
                source: c.source, metadata: {},
                ...(c.type === 'code' ? { outputs: [], execution_count: null } : {})
            }));
            const discovery = discoverKernels(workspace);
            const kernelspec = kernel_name
                ? { display_name: kernel_name, language: 'python', name: kernel_name }
                : discovery.preferred_kernel?.kernelspec_name
                    ? {
                        display_name: discovery.preferred_kernel.kernelspec_display_name ?? discovery.preferred_kernel.label,
                        language: 'python',
                        name: discovery.preferred_kernel.kernelspec_name,
                    }
                    : undefined;
            const nb = {
                nbformat: 4, nbformat_minor: 5,
                metadata: kernelspec ? { kernelspec } : {},
                cells: nbCells
            };
            await vscode.workspace.fs.writeFile(uri, Buffer.from(JSON.stringify(nb, null, 2)));

            const focus = captureEditorFocus();
            const doc = await vscode.workspace.openNotebookDocument(uri);
            const hasPreferredKernel = !!discovery.preferred_kernel?.python;

            let kernelStatus: string;
            try {
                await ensureNotebookEditor(doc, {
                    preserveFocus: true,
                    preview: false,
                });

                if (kernel_id) {
                    // Explicit kernel_id: use notebook.selectKernel with the Jupyter extension's controller ID
                    await new Promise(r => setTimeout(r, 800));
                    try {
                        const editor = findEditor(doc);
                        await vscode.commands.executeCommand('notebook.selectKernel', {
                            id: kernel_id,
                            extension: 'ms-toolsai.jupyter',
                            notebookEditor: editor,
                        });
                        kernelStatus = 'selected';
                    } catch {
                        kernelStatus = 'selection_failed';
                    }
                } else if (hasPreferredKernel) {
                    // Auto-select .venv kernel via Jupyter extension's openNotebook API
                    await new Promise(r => setTimeout(r, 800));
                    try {
                        const api = await getJupyterApi();
                        if (api) {
                            if (api.openNotebook) {
                                await api.openNotebook(uri, {
                                    id: discovery.preferred_kernel?.id.toLowerCase(),
                                    path: discovery.preferred_kernel?.python,
                                });
                                kernelStatus = 'selected';
                            } else {
                                kernelStatus = 'needs_selection';
                            }
                        } else {
                            kernelStatus = 'needs_selection';
                        }
                    } catch {
                        kernelStatus = 'selection_failed';
                    }
                } else {
                    kernelStatus = 'needs_selection';
                }
            } finally {
                await restoreEditorFocus(focus);
            }

            const response: Record<string, unknown> = { status: 'ok', path: relPath, kernel_status: kernelStatus };

            if (kernelStatus === 'selected' && discovery.preferred_kernel) {
                response.kernel = discovery.preferred_kernel;
                response.message = `Selected workspace .venv kernel: ${discovery.preferred_kernel.label}`;
            } else if (kernelStatus === 'needs_selection') {
                Object.assign(response, kernelSelectionGuidance(relPath, discovery, 'missing'));
            } else if (kernelStatus === 'selection_failed') {
                Object.assign(response, kernelSelectionGuidance(relPath, discovery, 'failed'));
            }

            return response;
        },

        'POST /api/notebook/open': async (body) => {
            const { path: relPath, cwd } = body as { path: string; cwd?: string };
            const uri = resolveNotebookUri(relPath, cwd);
            const doc = await resolveOrOpenNotebook(relPath, cwd);
            await vscode.commands.executeCommand('vscode.openWith', uri, 'jupyter-notebook');
            await ensureNotebookEditor(doc, { preserveFocus: false, preview: false });
            return { status: 'ok', path: relPath };
        },

        // --- Prompts ---
        'POST /api/notebook/prompt': async (body) => {
            const { path, cwd, instruction, at_index } = body as {
                path: string; cwd?: string; instruction: string; at_index?: number;
            };
            const doc = await resolveOrOpenNotebook(path, cwd);
            const index = (at_index === undefined || at_index === -1) ? doc.cellCount : at_index;
            const cellId = newCellId();
            const cellData = new vscode.NotebookCellData(vscode.NotebookCellKind.Markup, instruction, 'markdown');
            cellData.metadata = { custom: { 'agent-repl': { cell_id: cellId, type: 'prompt', status: 'pending' } } };
            const edit = new vscode.WorkspaceEdit();
            edit.set(doc.uri, [vscode.NotebookEdit.insertCells(index, [cellData])]);
            await vscode.workspace.applyEdit(edit);
            await doc.save();
            return { status: 'ok', cell_id: cellId, cell_index: index };
        },

        'POST /api/notebook/prompt-status': async (body) => {
            const { path, cwd, cell_id, status: promptStatus } = body as {
                path: string; cwd?: string; cell_id: string; status: string;
            };
            const doc = await resolveOrOpenNotebook(path, cwd);
            const idx = resolveCell(doc, { cell_id });
            const cell = doc.cellAt(idx);
            const meta = { ...(cell.metadata ?? {}) } as Record<string, any>;
            const custom = { ...(meta.custom ?? {}) };
            const ar = { ...(custom['agent-repl'] ?? {}) };
            ar.status = promptStatus;
            custom['agent-repl'] = ar;
            meta.custom = custom;
            const edit = new vscode.WorkspaceEdit();
            edit.set(doc.uri, [vscode.NotebookEdit.updateCellMetadata(idx, meta)]);
            await vscode.workspace.applyEdit(edit);
            await doc.save();
            return { status: 'ok', cell_id, prompt_status: promptStatus };
        },

        // --- Activity ---
        'POST /api/notebook/activity': async (body) => {
            activityEvents.push(body);
            if (activityEvents.length > 500) { activityEvents.splice(0, activityEvents.length - 500); }
            for (const l of activityListeners) { l(body); }
            return { status: 'ok' };
        }
    };
}

// --- Kernel restart helper ---
// Try multiple command IDs — availability depends on VS Code version and Cursor.
// Pass the notebook URI so the command doesn't rely on activeNotebookEditor (which would require focus).
async function restartKernel(notebookUri?: vscode.Uri): Promise<void> {
    const commands = [
        'jupyter.notebookeditor.restartkernel',
        'notebook.restartKernel',
        'jupyter.restartkernel',
    ];
    for (const cmd of commands) {
        try {
            await vscode.commands.executeCommand(cmd, notebookUri);
            return;
        } catch {
            // Command not available or failed — try next
        }
    }
    throw new Error('No kernel restart command succeeded');
}

// Activity event bus
const activityEvents: any[] = [];
const activityListeners: Array<(e: any) => void> = [];

export function onActivity(listener: (e: any) => void): vscode.Disposable {
    activityListeners.push(listener);
    return new vscode.Disposable(() => {
        const i = activityListeners.indexOf(listener);
        if (i >= 0) { activityListeners.splice(i, 1); }
    });
}

export function getActivityEvents(): any[] { return [...activityEvents]; }

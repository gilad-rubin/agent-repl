import * as vscode from 'vscode';
import * as os from 'os';
import * as path from 'path';
import * as fs from 'fs';
import { Routes } from './server';
import { resolveNotebook, resolveNotebookUri, resolveOrOpenNotebook, findOpenNotebook, findEditor, ensureNotebookEditor, captureEditorFocus, restoreEditorFocus } from './notebook/resolver';
import { applyEdits, EditOp } from './notebook/operations';
import { getCellId, ensureIds, resolveCell, withCellId, newCellId } from './notebook/identity';
import { toJupyter, stripForAgent } from './notebook/outputs';
import { resetJupyterApiCache, getJupyterApi } from './execution/queue';
import { discoverDaemon, daemonPost, workspaceRootForPath } from './session';
import { isCanvasNotebookOpen, listOpenCanvasNotebookPaths } from './editor/provider';

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

interface AttachDiagnostic {
    method: string;
    detail: string;
}

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

function workspaceKernelLabel(workspace: string | null): string {
    const base = workspace ? path.basename(workspace) : '';
    return base ? `${base} (.venv)` : '.venv (workspace)';
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
        // Only fall back to cwd when there is no workspace open at all.
        if (folders.length === 0) {
            return cwd;
        }
        return null;
    }
    return folders[0]?.uri.fsPath ?? null;
}

function buildBrowserCanvasUrl(
    relativePath: string,
    browserUrlOverride?: string,
): string {
    const configured = vscode.workspace.getConfiguration('agent-repl').get<string>(
        'browserCanvasUrl',
        'http://127.0.0.1:4173/preview.html',
    );
    const base = (browserUrlOverride && browserUrlOverride.trim()) || configured;
    const url = new URL(base);
    url.searchParams.set('path', relativePath);
    return url.toString();
}

export function discoverKernels(workspaceDir?: string | null): KernelDiscovery {
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
        if (matchingSpec) {
            matchingSpec.recommended = true;
            preferredKernel = matchingSpec;
        } else {
            const label = workspaceKernelLabel(workspace);
            preferredKernel = {
                id: workspaceVenvPython,
                label,
                type: 'workspace-venv',
                python: workspaceVenvPython,
                kernelspec_name: null,
                kernelspec_display_name: label,
                source: workspaceVenvPython,
                recommended: true,
            };
            pushKernel(preferredKernel);
        }
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

function buildSelectKernelCommand(relPath: string, kernelId?: string | null): string {
    const command = `agent-repl select-kernel ${shellQuote(relPath)}`;
    return kernelId ? `${command} --kernel-id ${shellQuote(kernelId)}` : command;
}

function buildInteractiveSelectKernelCommand(relPath: string): string {
    return `agent-repl select-kernel ${shellQuote(relPath)} --interactive`;
}

export function kernelSelectionGuidance(relPath: string, discovery: KernelDiscovery, reason: 'missing' | 'failed') {
    const command = `agent-repl select-kernel ${shellQuote(relPath)}`;
    const interactiveCommand = buildInteractiveSelectKernelCommand(relPath);
    const preferredCommand = discovery.preferred_kernel
        ? buildSelectKernelCommand(relPath, discovery.preferred_kernel.id)
        : command;
    const kernelNames = discovery.kernels.map(kernel => kernel.label);
    const intro = reason === 'failed'
        ? 'agent-repl could not attach a kernel automatically. Select one to continue.'
        : 'No workspace .venv was detected. Select a kernel to continue.';

    return {
        message: intro,
        selection_required: true,
        available_kernel_names: kernelNames,
        available_kernel_count: kernelNames.length,
        list_kernels_command: 'agent-repl kernels',
        select_kernel_command: command,
        open_picker_command: interactiveCommand,
        recommended_kernel_id: discovery.preferred_kernel?.id ?? null,
        recommended_kernel_name: discovery.preferred_kernel?.label ?? null,
        recommended_kernel_command: preferredCommand,
        next_step: reason === 'failed'
            ? discovery.preferred_kernel
                ? `Run ${command} to retry with the workspace-preferred kernel. If you need the VS Code kernel picker, run ${interactiveCommand}.`
                : `Run ${interactiveCommand} to open the VS Code kernel picker.`
            : discovery.preferred_kernel
                ? `Run ${command} to use the workspace-preferred kernel, or run ${interactiveCommand} to open the VS Code kernel picker.`
                : `Run ${interactiveCommand} to open the VS Code kernel picker and choose one of the available kernels.`,
    };
}

function createKernelRequiredError(
    relPath: string,
    discovery: KernelDiscovery,
    reason: 'missing' | 'failed',
    diagnostics: AttachDiagnostic[] = [],
): Error {
    const guidance = kernelSelectionGuidance(relPath, discovery, reason);
    const detail = diagnostics.length
        ? ` Diagnostics: ${formatDiagnostics(diagnostics)}`
        : '';
    const message = reason === 'missing'
        ? `agent-repl new requires a ready kernel. No workspace .venv kernel was detected for '${relPath}'. Re-run with --kernel <kernel-id>.${detail}`
        : `agent-repl new could not attach the requested or preferred kernel for '${relPath}'. Re-run with --kernel <kernel-id>.${detail}`;
    const error = new Error(`${message} ${guidance.next_step}`.trim()) as Error & { statusCode?: number };
    error.statusCode = 400;
    return error;
}

function rawCellSource(source: unknown): string {
    if (Array.isArray(source)) {
        return source.map(part => `${part ?? ''}`).join('');
    }
    return typeof source === 'string' ? source : '';
}

async function readNotebookContents(relPath: string, cwd?: string): Promise<{ path: string; cells: any[] }> {
    resolveNotebookUri(relPath, cwd);
    const openDoc = findOpenNotebook(relPath, cwd);
    if (openDoc) {
        await ensureIds(openDoc);

        const cells = [];
        let codeIndex = 0;
        for (let i = 0; i < openDoc.cellCount; i++) {
            const cell = openDoc.cellAt(i);
            const cellId = getCellId(cell);
            if (!cellId) {
                throw new Error(`Missing agent-repl cell ID for notebook cell at index ${i}`);
            }
            const isCode = cell.kind === vscode.NotebookCellKind.Code;
            const outputs = toJupyter(cell);
            cells.push({
                index: i,
                display_number: isCode ? ++codeIndex : null,
                cell_id: cellId,
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
    const rawText = Buffer.from(data).toString('utf8');
    if (rawText.trim().length === 0) {
        notebook = { cells: [] };
    } else {
        try {
            notebook = JSON.parse(rawText);
        } catch {
            const err = new Error(`Notebook '${relPath}' is not valid JSON`) as any;
            err.statusCode = 400;
            throw err;
        }
    }

    const cells = [];
    let codeIndex = 0;
    for (const [index, cell] of (notebook.cells ?? []).entries()) {
        const cellType = cell.cell_type === 'markdown' ? 'markdown' : 'code';
        const isCode = cellType === 'code';
        const cellId = cell.metadata?.custom?.['agent-repl']?.cell_id;
        if (typeof cellId !== 'string' || !cellId) {
            throw new Error(`Missing agent-repl cell ID for notebook cell at index ${index}`);
        }
        cells.push({
            index,
            display_number: isCode ? ++codeIndex : null,
            cell_id: cellId,
            cell_type: cellType,
            source: rawCellSource(cell.source),
            outputs: stripForAgent(Array.isArray(cell.outputs) ? cell.outputs : []),
            execution_count: cell.execution_count ?? null,
            metadata: cell.metadata ?? {}
        });
    }
    return { path: relPath, cells };
}

export function findKernelRecord(discovery: KernelDiscovery, kernelId: string): KernelRecord | undefined {
    return discovery.kernels.find(kernel =>
        kernel.id === kernelId ||
        kernel.kernelspec_name === kernelId
    ) ?? discovery.kernels.find(kernel => samePath(kernel.python, kernelId));
}

function uniqueStrings(values: Array<string | null | undefined>): string[] {
    const seen = new Set<string>();
    const result: string[] = [];
    for (const value of values) {
        if (!value) { continue; }
        if (seen.has(value)) { continue; }
        seen.add(value);
        result.push(value);
    }
    return result;
}

async function getPythonExtensionApi(): Promise<any | undefined> {
    const pythonExt = vscode.extensions.getExtension('ms-python.python');
    if (!pythonExt) { return undefined; }

    try {
        return pythonExt.isActive ? pythonExt.exports : await pythonExt.activate();
    } catch {
        return undefined;
    }
}

function pythonEnvironmentExecutablePath(env: any): string | undefined {
    return env?.executable?.uri?.fsPath ?? env?.path;
}

async function resolvePythonEnvironment(
    pythonPath: string,
    diagnostics: AttachDiagnostic[]
): Promise<any | undefined> {
    const pythonApi = await getPythonExtensionApi();
    if (!pythonApi?.environments) {
        diagnostics.push({
            method: 'python.resolveEnvironment',
            detail: 'Python extension API unavailable, so agent-repl could not resolve the interpreter into a Jupyter-selectable environment',
        });
        return undefined;
    }

    const environments = Array.isArray(pythonApi.environments.known)
        ? pythonApi.environments.known
        : [];
    const match = environments.find((env: any) =>
        samePath(pythonEnvironmentExecutablePath(env), pythonPath) ||
        samePath(env?.id, pythonPath)
    );

    try {
        const resolved = await pythonApi.environments.resolveEnvironment(match ?? pythonPath);
        if (resolved) {
            return resolved;
        }
        diagnostics.push({
            method: 'python.resolveEnvironment',
            detail: `resolveEnvironment(${JSON.stringify(match?.id ?? pythonPath)}) returned no environment`,
        });
    } catch (err: any) {
        const msg = err?.message ?? String(err);
        diagnostics.push({
            method: 'python.resolveEnvironment',
            detail: `resolveEnvironment(${JSON.stringify(match?.id ?? pythonPath)}) threw: ${msg}`,
        });
    }

    return undefined;
}

function attachedKernelMatches(kernel: any, requested?: KernelRecord): boolean {
    if (!kernel) { return false; }
    if (!requested) { return true; }

    const metadata = kernel?.kernelConnectionMetadata ?? {};
    const controllerId = `${metadata?.id ?? ''}`.toLowerCase();
    const kernelspecName = `${metadata?.kernelSpec?.name ?? metadata?.kernelSpec?.specFile ?? ''}`.toLowerCase();
    const interpreterPath = metadata?.interpreter?.uri?.fsPath ?? metadata?.interpreter?.path;

    return (
        controllerId === requested.id.toLowerCase() ||
        (requested.kernelspec_name ? controllerId === requested.kernelspec_name.toLowerCase() : false) ||
        (requested.kernelspec_name ? kernelspecName.includes(requested.kernelspec_name.toLowerCase()) : false) ||
        samePath(interpreterPath, requested.python)
    );
}

function selectedPythonEnvironmentMatches(environment: any, requested?: KernelRecord): boolean {
    if (!environment) { return false; }
    if (!requested) { return true; }

    return samePath(pythonEnvironmentExecutablePath(environment), requested.python);
}

async function currentAttachedKernel(doc: vscode.NotebookDocument): Promise<any | undefined> {
    const api = await getJupyterApi();
    const getKernel = api?.kernels?.getKernel;
    if (typeof getKernel !== 'function') { return undefined; }

    try {
        return await getKernel(doc.uri);
    } catch (err) {
        console.warn('[agent-repl] currentAttachedKernel error:', err);
        return undefined;
    }
}

async function currentSelectedPythonEnvironment(doc: vscode.NotebookDocument): Promise<any | undefined> {
    const api = await getJupyterApi();
    const getPythonEnvironment = api?.getPythonEnvironment;
    if (typeof getPythonEnvironment !== 'function') { return undefined; }

    try {
        return await getPythonEnvironment(doc.uri);
    } catch (err) {
        console.warn('[agent-repl] currentSelectedPythonEnvironment error:', err);
        return undefined;
    }
}

async function waitForKernelSelection(
    doc: vscode.NotebookDocument,
    requested?: KernelRecord,
    timeoutMs: number = 4000
): Promise<any | undefined> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        const kernel = await currentAttachedKernel(doc);
        if (attachedKernelMatches(kernel, requested)) {
            return kernel;
        }

        const environment = await currentSelectedPythonEnvironment(doc);
        if (selectedPythonEnvironmentMatches(environment, requested)) {
            return environment;
        }

        await new Promise(resolve => setTimeout(resolve, 150));
    }
    return undefined;
}

async function attachKernelQuietly(
    doc: vscode.NotebookDocument,
    kernel: KernelRecord,
    diagnostics: AttachDiagnostic[]
): Promise<boolean> {
    const api = await getJupyterApi();
    if (typeof api?.openNotebook !== 'function') {
        diagnostics.push({ method: 'jupyter.openNotebook', detail: 'api.openNotebook is not a function (Jupyter extension API unavailable or not yet activated)' });
        return false;
    }

    if (!kernel.python) {
        diagnostics.push({
            method: 'jupyter.openNotebook',
            detail: `Kernel "${kernel.id}" does not expose a Python executable path, so agent-repl could not resolve it to a Python environment for programmatic selection`,
        });
        return false;
    }

    const environment = await resolvePythonEnvironment(kernel.python, diagnostics);
    if (!environment) {
        return false;
    }

    const request = {
        id: environment?.id ?? null,
        path: pythonEnvironmentExecutablePath(environment) ?? kernel.python,
    };

    try {
        await api.openNotebook(doc.uri, environment);
    } catch (err: any) {
        const msg = err?.message ?? String(err);
        diagnostics.push({ method: 'jupyter.openNotebook', detail: `openNotebook(${JSON.stringify(request)}) threw: ${msg}` });
        return false;
    }

    return true;
}

async function attachEnvironmentQuietly(
    doc: vscode.NotebookDocument,
    environment: any,
    diagnostics: AttachDiagnostic[]
): Promise<boolean> {
    const api = await getJupyterApi();
    if (typeof api?.openNotebook !== 'function') {
        diagnostics.push({ method: 'jupyter.openNotebook', detail: 'api.openNotebook is not a function (Jupyter extension API unavailable or not yet activated)' });
        return false;
    }

    const request = {
        id: environment?.id ?? null,
        path: pythonEnvironmentExecutablePath(environment) ?? null,
    };

    try {
        await api.openNotebook(doc.uri, environment);
        return true;
    } catch (err: any) {
        diagnostics.push({
            method: 'jupyter.openNotebook',
            detail: `openNotebook(${JSON.stringify(request)}) threw: ${err?.message ?? String(err)}`,
        });
        return false;
    }
}

async function selectKernelViaCommand(
    doc: vscode.NotebookDocument,
    notebookEditor: vscode.NotebookEditor,
    kernelIds: Array<string | null | undefined>,
    requested: KernelRecord | undefined,
    extensionId: string,
    diagnostics: AttachDiagnostic[]
): Promise<boolean> {
    for (const id of uniqueStrings(kernelIds)) {
        try {
            await vscode.commands.executeCommand('notebook.selectKernel', {
                id,
                extension: extensionId,
                editor: notebookEditor,
                notebookEditor,
            });
        } catch (err: any) {
            const msg = err?.message ?? String(err);
            diagnostics.push({ method: 'notebook.selectKernel', detail: `selectKernel(id=${id}, ext=${extensionId}) threw: ${msg}` });
            continue;
        }

        if (await waitForKernelSelection(doc, requested, 2000)) {
            return true;
        }
        diagnostics.push({ method: 'notebook.selectKernel', detail: `selectKernel(id=${id}, ext=${extensionId}) succeeded but the selected kernel was not observable within 2s` });
    }

    return false;
}

interface AttachResult {
    method?: 'already-selected' | 'jupyter.openNotebook' | 'notebook.selectKernel';
    diagnostics: AttachDiagnostic[];
}

async function attachKernelWithFallback(
    doc: vscode.NotebookDocument,
    kernel: KernelRecord,
    extensionId: string,
    extraKernelIds: Array<string | null | undefined> = []
): Promise<AttachResult> {
    const diagnostics: AttachDiagnostic[] = [];

    if (await waitForKernelSelection(doc, kernel, 300)) {
        return { method: 'already-selected', diagnostics };
    }

    if (await attachKernelQuietly(doc, kernel, diagnostics)) {
        return { method: 'jupyter.openNotebook', diagnostics };
    }

    const focus = captureEditorFocus();
    try {
        const editor = await ensureNotebookEditor(doc, {
            preserveFocus: true,
            preview: false,
        });

        if (await selectKernelViaCommand(
            doc,
            editor,
            [...extraKernelIds, kernel.id, kernel.kernelspec_name],
            kernel,
            extensionId,
            diagnostics,
        )) {
            return { method: 'notebook.selectKernel', diagnostics };
        }
    } finally {
        await restoreEditorFocus(focus);
    }

    return { diagnostics };
}

async function selectKernelById(
    doc: vscode.NotebookDocument,
    kernelId: string,
    extensionId: string,
    diagnostics: AttachDiagnostic[] = []
): Promise<boolean> {
    const focus = captureEditorFocus();
    try {
        const editor = await ensureNotebookEditor(doc, {
            preserveFocus: true,
            preview: false,
        });
        return await selectKernelViaCommand(doc, editor, [kernelId], undefined, extensionId, diagnostics);
    } finally {
        await restoreEditorFocus(focus);
    }
}

function requireDaemon(fsPath: string): { daemon: { url: string; token: string }; workspaceRoot: string } {
    const workspaceRoot = workspaceRootForPath(fsPath);
    if (!workspaceRoot) {
        throw new Error(`No workspace root found for ${fsPath}`);
    }
    const daemon = discoverDaemon(workspaceRoot);
    if (!daemon) {
        throw new Error('Daemon not found');
    }
    return { daemon, workspaceRoot };
}

export function buildRoutes(maxQueue: number): Routes {
    return {
        // --- Health ---
        'GET /api/health': async () => ({
            status: 'ok',
            version: '0.3.0',
            extension_root: path.resolve(__dirname, '..'),
            routes_module: __filename,
            open_notebooks: Array.from(new Set([
                ...vscode.workspace.notebookDocuments
                .filter(d => d.notebookType === 'jupyter-notebook')
                .map(d => d.uri.fsPath),
                ...listOpenCanvasNotebookPaths(),
            ])),
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
                        const cancellation = new vscode.CancellationTokenSource();
                        const outputs = [];
                        try {
                            for await (const output of kernel.executeCode(code, cancellation.token)) {
                                outputs.push({
                                    items: Array.isArray(output?.items) ? output.items.length : 0,
                                    metadata: output?.metadata ?? {},
                                });
                            }
                        } finally {
                            cancellation.dispose();
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
            resolveNotebookUri(relPath, cwd);
            const doc = findOpenNotebook(relPath, cwd);
            if (!doc) {
                const notebookUri = resolveNotebookUri(relPath, cwd);
                if (isCanvasNotebookOpen(notebookUri.fsPath)) {
                    return {
                        path: relPath,
                        open: true,
                        open_via: 'canvas',
                        kernel_state: 'canvas',
                        busy: false,
                        running: [],
                        queued: [],
                    };
                }
                return {
                    path: relPath,
                    open: false,
                    kernel_state: 'not_open',
                    busy: false,
                    running: [],
                    queued: [],
                };
            }
            const { daemon, workspaceRoot } = requireDaemon(doc.uri.fsPath);
            const notebookPath = path.relative(workspaceRoot, doc.uri.fsPath);
            const status = await daemonPost(daemon, '/api/notebooks/status', { path: notebookPath });
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

        // --- Execute (daemon pass-through) ---
        'POST /api/notebook/execute-cell': async (body) => {
            const { path: relPath, cwd, cell_id, cell_index } = body as {
                path: string; cwd?: string; cell_id?: string; cell_index?: number;
            };
            const doc = await resolveOrOpenNotebook(relPath, cwd);
            const { daemon, workspaceRoot } = requireDaemon(doc.uri.fsPath);
            const notebookPath = path.relative(workspaceRoot, doc.uri.fsPath);
            return daemonPost(daemon, '/api/notebooks/execute-cell', {
                path: notebookPath,
                cell_id,
                cell_index,
            });
        },

        'GET /api/notebook/execution': async (_body, q) => {
            const id = q.get('id');
            const notebookPath = q.get('path');
            if (!id) { throw new Error('Missing ?id='); }
            if (!notebookPath) { throw new Error('Missing ?path='); }
            const doc = resolveNotebook(notebookPath);
            const { daemon, workspaceRoot } = requireDaemon(doc.uri.fsPath);
            const relPath = path.relative(workspaceRoot, doc.uri.fsPath);
            return daemonPost(daemon, '/api/notebooks/execution', {
                path: relPath,
                execution_id: id,
            });
        },

        'POST /api/notebook/insert-and-execute': async (body) => {
            const { path: relPath, cwd, source, cell_type, at_index } = body as {
                path: string; cwd?: string; source: string; cell_type?: string; at_index?: number;
            };
            const doc = await resolveOrOpenNotebook(relPath, cwd);
            const { daemon, workspaceRoot } = requireDaemon(doc.uri.fsPath);
            const notebookPath = path.relative(workspaceRoot, doc.uri.fsPath);
            return daemonPost(daemon, '/api/notebooks/insert-and-execute', {
                path: notebookPath,
                source,
                cell_type: cell_type ?? 'code',
                at_index: at_index ?? -1,
            });
        },

        // --- Lifecycle ---
        'POST /api/notebook/execute-all': async (body) => {
            const { path: relPath, cwd } = body as { path: string; cwd?: string };
            const doc = await resolveOrOpenNotebook(relPath, cwd);
            const { daemon, workspaceRoot } = requireDaemon(doc.uri.fsPath);
            const notebookPath = path.relative(workspaceRoot, doc.uri.fsPath);
            return daemonPost(daemon, '/api/notebooks/execute-all', {
                path: notebookPath,
            });
        },

        'POST /api/notebook/restart-kernel': async (body) => {
            const { path, cwd } = body as { path: string; cwd?: string };
            const doc = await resolveOrOpenNotebook(path, cwd);
            const diagnostics: AttachDiagnostic[] = [];
            const method = await restartKernelQuietly(doc, resolveWorkspaceDir(cwd), diagnostics);
            return { status: 'ok', path, method, diagnostics };
        },

        'POST /api/notebook/restart-and-run-all': async (body) => {
            const { path: relPath, cwd } = body as { path: string; cwd?: string };
            const doc = await resolveOrOpenNotebook(relPath, cwd);
            const diagnostics: AttachDiagnostic[] = [];
            const method = await restartKernelQuietly(doc, resolveWorkspaceDir(cwd), diagnostics);
            const { daemon, workspaceRoot } = requireDaemon(doc.uri.fsPath);
            const notebookPath = path.relative(workspaceRoot, doc.uri.fsPath);
            const execResult = await daemonPost(daemon, '/api/notebooks/execute-all', {
                path: notebookPath,
            });
            return { status: 'started', path: relPath, method, diagnostics, ...execResult };
        },

        'POST /api/notebook/select-kernel': async (body) => {
            const { path: relPath, cwd, kernel_id, extension: kernelExt, interactive } = body as {
                path: string; cwd?: string; kernel_id?: string; extension?: string; interactive?: boolean;
            };
            const doc = await resolveOrOpenNotebook(relPath, cwd);
            const discovery = discoverKernels(resolveWorkspaceDir(cwd));
            const extensionId = kernelExt ?? 'ms-toolsai.jupyter';

            if (kernel_id) {
                const requested = findKernelRecord(discovery, kernel_id);
                if (requested) {
                    const result = await attachKernelWithFallback(doc, requested, extensionId, [kernel_id]);
                    if (result.method) {
                        return { status: 'ok', path: relPath, kernel_id, method: result.method };
                    }

                    return {
                        status: 'selection_failed',
                        path: relPath,
                        kernel_id,
                        diagnostics: result.diagnostics,
                        ...kernelSelectionGuidance(relPath, discovery, 'failed'),
                    };
                }

                const fallbackDiagnostics: AttachDiagnostic[] = [];
                if (await selectKernelById(doc, kernel_id, extensionId, fallbackDiagnostics)) {
                    return { status: 'ok', path: relPath, kernel_id, method: 'notebook.selectKernel' };
                }

                return {
                    status: 'selection_failed',
                    path: relPath,
                    kernel_id,
                    diagnostics: fallbackDiagnostics.length
                        ? fallbackDiagnostics
                        : [{ method: 'notebook.selectKernel', detail: `kernel_id "${kernel_id}" not found in discovery and selectKernel command failed` }],
                    ...kernelSelectionGuidance(relPath, discovery, 'failed'),
                };
            }

            if (!interactive && discovery.preferred_kernel) {
                const result = await attachKernelWithFallback(doc, discovery.preferred_kernel, extensionId);
                if (result.method) {
                    return {
                        status: 'ok',
                        path: relPath,
                        kernel_id: discovery.preferred_kernel.id,
                        method: result.method,
                        defaulted_to_preferred: true,
                    };
                }

                return {
                    status: 'selection_failed',
                    path: relPath,
                    kernel_id: discovery.preferred_kernel.id,
                    diagnostics: result.diagnostics,
                    ...kernelSelectionGuidance(relPath, discovery, 'failed'),
                };
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
            let selectedKernel: KernelRecord | null = null;
            let kernelDiagnostics: AttachDiagnostic[] = [];
            try {
                if (kernel_id) {
                    const requested = findKernelRecord(discovery, kernel_id);
                    if (requested) {
                        const result = await attachKernelWithFallback(doc, requested, 'ms-toolsai.jupyter', [kernel_id]);
                        kernelDiagnostics = result.diagnostics;
                        if (result.method) {
                            selectedKernel = requested;
                            kernelStatus = 'selected';
                        } else {
                            kernelStatus = 'selection_failed';
                        }
                    } else if (await selectKernelById(doc, kernel_id, 'ms-toolsai.jupyter', kernelDiagnostics)) {
                        kernelStatus = 'selected';
                    } else {
                        kernelStatus = 'selection_failed';
                    }
                } else if (hasPreferredKernel && discovery.preferred_kernel) {
                    const result = await attachKernelWithFallback(doc, discovery.preferred_kernel, 'ms-toolsai.jupyter');
                    kernelDiagnostics = result.diagnostics;
                    if (result.method) {
                        selectedKernel = discovery.preferred_kernel;
                        kernelStatus = 'selected';
                    } else {
                        kernelStatus = 'selection_failed';
                    }
                } else {
                    kernelStatus = 'needs_selection';
                }
            } finally {
                await restoreEditorFocus(focus);
            }

            if (kernelStatus === 'needs_selection') {
                throw createKernelRequiredError(relPath, discovery, 'missing');
            }
            if (kernelStatus === 'selection_failed') {
                throw createKernelRequiredError(relPath, discovery, 'failed', kernelDiagnostics);
            }

            const response: Record<string, unknown> = {
                status: 'ok',
                path: relPath,
                kernel_status: kernelStatus,
                ready: kernelStatus === 'selected',
            };

            if (kernelStatus === 'selected' && selectedKernel) {
                response.kernel = selectedKernel;
                response.message = `Selected kernel: ${selectedKernel.label}`;
            }

            return response;
        },

        'POST /api/notebook/open': async (body) => {
            const { path: relPath, cwd, editor, target, browser_url } = body as {
                path: string;
                cwd?: string;
                editor?: string;
                target?: string;
                browser_url?: string;
            };
            const uri = resolveNotebookUri(relPath, cwd);
            const resolvedTarget = target === 'browser' ? 'browser' : 'vscode';
            const resolvedEditor = editor === 'jupyter' ? 'jupyter' : 'canvas';
            if (resolvedTarget === 'browser') {
                const url = buildBrowserCanvasUrl(
                    relPath,
                    typeof browser_url === 'string' ? browser_url : undefined,
                );
                await vscode.env.openExternal(vscode.Uri.parse(url));
                return {
                    status: 'ok',
                    path: relPath,
                    target: 'browser',
                    editor: 'canvas',
                    url,
                };
            }
            if (resolvedEditor === 'canvas') {
                await vscode.commands.executeCommand('vscode.openWith', uri, 'agent-repl.canvasEditor');
                return {
                    status: 'ok',
                    path: relPath,
                    target: 'vscode',
                    editor: 'canvas',
                    view_type: 'agent-repl.canvasEditor',
                };
            }

            const doc = await resolveOrOpenNotebook(relPath, cwd);
            await vscode.commands.executeCommand('vscode.openWith', uri, 'jupyter-notebook');
            await ensureNotebookEditor(doc, { preserveFocus: false, preview: false });
            return {
                status: 'ok',
                path: relPath,
                target: 'vscode',
                editor: 'jupyter',
                view_type: 'jupyter-notebook',
            };
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
            pushActivityEvent(body);
            return { status: 'ok' };
        }
    };
}

function formatDiagnostics(diagnostics: AttachDiagnostic[]): string {
    return diagnostics.map(d => `${d.method}: ${d.detail}`).join(' | ');
}

async function restartKernelQuietly(
    doc: vscode.NotebookDocument,
    workspaceDir: string | null,
    diagnostics: AttachDiagnostic[]
): Promise<string> {
    const currentKernel = await currentAttachedKernel(doc);
    const currentEnvironment = await currentSelectedPythonEnvironment(doc);
    const workspaceKernel = discoverKernels(workspaceDir).preferred_kernel;

    if (!currentKernel || typeof currentKernel.shutdown !== 'function') {
        diagnostics.push({
            method: 'kernel.shutdown',
            detail: 'Current kernel does not expose a background shutdown method',
        });
        throw new Error(`Background kernel restart is unavailable. ${formatDiagnostics(diagnostics)}`);
    }

    try {
        await currentKernel.shutdown();
    } catch (err: any) {
        diagnostics.push({
            method: 'kernel.shutdown',
            detail: err?.message ?? String(err),
        });
        throw new Error(`Background kernel restart failed. ${formatDiagnostics(diagnostics)}`);
    } finally {
        resetJupyterApiCache();
    }

    if (currentEnvironment && await attachEnvironmentQuietly(doc, currentEnvironment, diagnostics)) {
        return 'jupyter.openNotebook(current-environment)';
    }

    if (workspaceKernel && await attachKernelQuietly(doc, workspaceKernel, diagnostics)) {
        return 'jupyter.openNotebook(workspace-preferred)';
    }

    throw new Error(`Background kernel restart could not reattach a kernel. ${formatDiagnostics(diagnostics)}`);
}

// Activity event bus
const activityEvents: any[] = [];
const activityListeners: Array<(e: any) => void> = [];

export function pushActivityEvent(event: any): void {
    activityEvents.push(event);
    if (activityEvents.length > 500) { activityEvents.splice(0, activityEvents.length - 500); }
    for (const listener of activityListeners) { listener(event); }
}

export function onActivity(listener: (e: any) => void): vscode.Disposable {
    activityListeners.push(listener);
    return new vscode.Disposable(() => {
        const i = activityListeners.indexOf(listener);
        if (i >= 0) { activityListeners.splice(i, 1); }
    });
}

export function getActivityEvents(): any[] { return [...activityEvents]; }

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');

const { chromium } = require('playwright');

const extensionRoot = path.resolve(__dirname, '..');
let previewPort = 4173;
let previewUrl = `http://127.0.0.1:${previewPort}/preview.html`;
let mockPreviewUrl = `${previewUrl}?mock=1`;

let previewServer;
let previewServerExitPromise;
let browser;

async function waitForPreviewReady(url, timeoutMs = 60_000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
        try {
            const response = await fetch(url, { cache: 'no-store' });
            if (response.ok) {
                return;
            }
        } catch {
            // Keep polling until the server is up.
        }
        await delay(250);
    }
    throw new Error(`Preview server did not become ready within ${timeoutMs}ms`);
}

async function findOpenPort() {
    return new Promise((resolve, reject) => {
        const server = net.createServer();
        server.on('error', reject);
        server.listen(0, '127.0.0.1', () => {
            const address = server.address();
            const port = typeof address === 'object' && address ? address.port : null;
            server.close((error) => {
                if (error) {
                    reject(error);
                    return;
                }
                if (typeof port !== 'number') {
                    reject(new Error('Failed to allocate a preview port'));
                    return;
                }
                resolve(port);
            });
        });
    });
}

async function runAgentReplCli(args, cwd = path.resolve(extensionRoot, '..')) {
    return await new Promise((resolve, reject) => {
        const child = spawn('uv', ['run', '--project', cwd, 'agent-repl', ...args], {
            cwd,
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (chunk) => {
            stdout += chunk.toString();
        });
        child.stderr.on('data', (chunk) => {
            stderr += chunk.toString();
        });
        child.on('error', reject);
        child.on('exit', (code) => {
            if (code === 0) {
                resolve({ stdout, stderr });
                return;
            }
            reject(new Error(stderr || stdout || `agent-repl exited with ${code}`));
        });
    });
}

async function stopWorkspaceDaemon(workspaceRoot) {
    try {
        await runAgentReplCli(['core', 'stop', '--workspace-root', workspaceRoot]);
    } catch {
        // Best-effort cleanup: there may not be a daemon yet.
    }
}

async function launchBrowser() {
    try {
        return await chromium.launch({ channel: 'chrome', headless: true });
    } catch (error) {
        return chromium.launch({ headless: true });
    }
}

async function openPreviewPage() {
    const context = await browser.newContext({
        viewport: { width: 1440, height: 960 },
    });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
        origin: new URL(previewUrl).origin,
    });
    const page = await context.newPage();
    await page.goto(mockPreviewUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-cell-id="preview-code-1"] .cm-editor');
    return { context, page };
}

async function openWorkspacePreviewPage() {
    const context = await browser.newContext({
        viewport: { width: 1440, height: 960 },
    });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
        origin: new URL(previewUrl).origin,
    });
    const page = await context.newPage();
    await page.goto(previewUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-browser-shell="true"]');
    return { context, page };
}

async function openWorkspaceNotebookPage(notebookPath) {
    const context = await browser.newContext({
        viewport: { width: 1440, height: 960 },
    });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
        origin: new URL(previewUrl).origin,
    });
    const page = await context.newPage();
    const targetUrl = `${previewUrl}?path=${encodeURIComponent(notebookPath)}`;
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-browser-shell="true"]');
    await page.waitForSelector('[data-cell-id] .cm-editor');
    return { context, page };
}

async function openJupyterLabWorkspaceNotebookPage(notebookPath) {
    const workspaceRoot = path.resolve(extensionRoot, '..');
    await runAgentReplCli(['core', 'start', '--workspace-root', workspaceRoot], workspaceRoot);
    const context = await browser.newContext({
        viewport: { width: 1440, height: 960 },
    });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], {
        origin: new URL(previewUrl).origin,
    });
    const page = await context.newPage();
    const targetUrl = `${previewUrl}?path=${encodeURIComponent(notebookPath)}`;
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
    await waitForJupyterLabReady(page);
    return { context, page };
}

async function waitForJupyterLabReady(page) {
    await page.waitForSelector('.agent-repl-jupyterlab-toolbar', { timeout: 60_000 });
    await page.waitForFunction(() => {
        const shell = document.querySelector('[data-jupyterlab-phase]');
        const phase = shell?.getAttribute('data-jupyterlab-phase');
        return phase === 'ready' || phase === 'error';
    }, { timeout: 60_000 });
    const errorText = await page.locator('.agent-repl-jupyterlab-preview-state--error').textContent().catch(() => null);
    if (errorText && errorText.trim()) {
        throw new Error(`JupyterLab preview failed to boot: ${errorText.trim()}`);
    }
    await page.waitForSelector('.agent-repl-jupyterlab-notebook', { timeout: 60_000 });
    await page.waitForFunction(() => {
        return document.querySelectorAll('.agent-repl-jupyterlab-notebook .jp-Cell').length > 0;
    }, { timeout: 60_000 });
}

async function waitForJupyterLabCommandMode(page) {
    await page.waitForFunction(() => {
        const notebook = document.querySelector('.agent-repl-jupyterlab-notebook');
        return notebook?.classList.contains('jp-mod-commandMode') ?? false;
    }, { timeout: 30_000 });
}

async function focusJupyterLabNotebook(page) {
    await page.evaluate(() => {
        const activeCell = document.querySelector('.agent-repl-jupyterlab-notebook .jp-Cell.jp-mod-active');
        if (activeCell instanceof HTMLElement) {
            activeCell.focus();
            return;
        }
        const notebook = document.querySelector('.agent-repl-jupyterlab-notebook');
        if (notebook instanceof HTMLElement) {
            notebook.focus();
        }
    });
    await page.waitForFunction(() => {
        const notebook = document.querySelector('.agent-repl-jupyterlab-notebook');
        return notebook instanceof HTMLElement
            && document.activeElement instanceof HTMLElement
            && notebook.contains(document.activeElement);
    }, { timeout: 10_000 });
}

async function closePageHandle(handle) {
    await handle.page.close().catch(() => {});
    await handle.context.close().catch(() => {});
}

function normalizeNotebookSource(source) {
    if (Array.isArray(source)) {
        return source.join('');
    }
    return typeof source === 'string' ? source : '';
}

async function writeNotebook(filePath, cellSources) {
    const notebook = {
        cells: cellSources.map((source, index) => ({
            cell_type: 'code',
            execution_count: null,
            id: `browser-test-${Date.now()}-${index}`,
            metadata: {},
            outputs: [],
            source: [`${source}${source.endsWith('\n') ? '' : '\n'}`],
        })),
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, JSON.stringify(notebook, null, 2));
}

function collectWidgetFixture(sourceNotebook) {
    const widgetState = sourceNotebook.metadata?.widgets?.['application/vnd.jupyter.widget-state+json'];
    if (!widgetState || typeof widgetState !== 'object') {
        throw new Error('Source notebook does not include saved widget state');
    }

    for (const cell of sourceNotebook.cells ?? []) {
        for (const output of cell.outputs ?? []) {
            const view = output?.data?.['application/vnd.jupyter.widget-view+json'];
            if (!view || typeof view !== 'object' || typeof view.model_id !== 'string') {
                continue;
            }
            if (!(view.model_id in widgetState)) {
                continue;
            }

            const neededIds = new Set([view.model_id]);
            const queue = [view.model_id];
            while (queue.length > 0) {
                const modelId = queue.pop();
                const model = widgetState[modelId];
                if (!model) {
                    continue;
                }
                const refs = JSON.stringify(model).match(/IPY_MODEL_[0-9a-f]+/g) ?? [];
                for (const ref of refs) {
                    const nextId = ref.replace('IPY_MODEL_', '');
                    if (!neededIds.has(nextId) && widgetState[nextId]) {
                        neededIds.add(nextId);
                        queue.push(nextId);
                    }
                }
            }

            const minimizedState = {};
            for (const modelId of neededIds) {
                minimizedState[modelId] = widgetState[modelId];
            }

            return {
                cell: {
                    ...cell,
                    outputs: [output],
                },
                metadata: {
                    ...(sourceNotebook.metadata ?? {}),
                    widgets: {
                        'application/vnd.jupyter.widget-state+json': minimizedState,
                    },
                },
            };
        }
    }

    throw new Error('Could not find a saved widget view with matching widget state');
}

async function waitForNotebookSource(filePath, expectedText, timeoutMs = 15_000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
        try {
            const raw = await fs.readFile(filePath, 'utf8');
            const notebook = JSON.parse(raw);
            const source = normalizeNotebookSource(notebook.cells?.[0]?.source);
            if (source.includes(expectedText)) {
                return source;
            }
        } catch {
            // Keep polling until the saved contents land.
        }
        await delay(150);
    }
    throw new Error(`Notebook source did not include ${expectedText} within ${timeoutMs}ms`);
}

async function waitForNotebook(filePath, predicate, timeoutMs = 15_000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
        try {
            const raw = await fs.readFile(filePath, 'utf8');
            const notebook = JSON.parse(raw);
            if (predicate(notebook)) {
                return notebook;
            }
        } catch {
            // Keep polling until the saved contents land.
        }
        await delay(150);
    }
    throw new Error(`Notebook contents did not satisfy the expected predicate within ${timeoutMs}ms`);
}

test.before(async () => {
    previewPort = await findOpenPort();
    previewUrl = `http://127.0.0.1:${previewPort}/preview.html`;
    mockPreviewUrl = `${previewUrl}?mock=1`;
    previewServer = spawn('node', ['./scripts/preview-webview.mjs'], {
        cwd: extensionRoot,
        env: {
            ...process.env,
            AGENT_REPL_PREVIEW_PORT: String(previewPort),
        },
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    previewServerExitPromise = new Promise((resolve) => {
        previewServer.once('exit', () => resolve());
    });

    let startupLogs = '';
    previewServer.stdout.on('data', (chunk) => {
        startupLogs += chunk.toString();
    });
    previewServer.stderr.on('data', (chunk) => {
        startupLogs += chunk.toString();
    });

    previewServer.on('exit', (code) => {
        if (code !== 0 && code !== null) {
            console.error(startupLogs);
        }
    });

    await waitForPreviewReady(previewUrl);
    browser = await launchBrowser();
});

test.after(async () => {
    await browser?.close().catch(() => {});
    if (previewServer && !previewServer.killed) {
        previewServer.kill('SIGTERM');
    }
    await Promise.race([
        previewServerExitPromise,
        delay(5_000),
    ]).catch(() => {});
    if (previewServer?.exitCode == null && !previewServer?.killed) {
        previewServer.kill('SIGKILL');
    }
    await stopWorkspaceDaemon(path.resolve(extensionRoot, '..'));
});

test('preview uses a monospaced font for code cells', async () => {
    const handle = await openPreviewPage();
    try {
        const fontFamily = await handle.page
            .locator('[data-cell-id="preview-code-1"] .cm-content')
            .evaluate((element) => getComputedStyle(element).fontFamily);

        assert.match(fontFamily, /IBM Plex Mono|SF Mono|Monaco|Menlo|Consolas|monospace/i);
    } finally {
        await closePageHandle(handle);
    }
});

test('kernel picker options remain visible below the toolbar when opened', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        await page.locator('.toolbar-kernel-button').click();
        await page.waitForSelector('.toolbar-kernel-menu');

        const toolbarOverflow = await page.evaluate(() => {
            const toolbar = document.querySelector('[data-toolbar="notebook"] > div');
            if (!(toolbar instanceof HTMLElement)) {
                return null;
            }
            const computed = getComputedStyle(toolbar);
            return {
                overflowX: computed.overflowX,
                overflowY: computed.overflowY,
            };
        });

        assert.deepEqual(toolbarOverflow, {
            overflowX: 'visible',
            overflowY: 'visible',
        });
    } finally {
        await closePageHandle(handle);
    }
});

test('plain preview roots itself to the launched workspace and selects a notebook', async () => {
    const handle = await openWorkspacePreviewPage();
    try {
        const { page } = handle;

        await page.waitForFunction(() => {
            const activeNotebook = document.querySelector('[data-explorer-item="notebook"][data-active="true"]');
            return Boolean(activeNotebook && new URL(window.location.href).searchParams.get('path'));
        });

        const selectedPath = await page.evaluate(() => new URL(window.location.href).searchParams.get('path'));
        const activeCount = await page.locator('[data-explorer-item="notebook"][data-active="true"]').count();
        const mockNotebookVisible = await page.locator('[data-cell-id="preview-code-1"]').count();

        assert.equal(activeCount, 1);
        assert.ok(selectedPath);
        assert.equal(mockNotebookVisible, 0);
    } finally {
        await closePageHandle(handle);
    }
});

test('browser save button and cmd/ctrl-s flush the active draft to disk without blur', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-save-shortcut-${Date.now()}.ipynb`);
    const notebook = {
        cells: [{
            cell_type: 'code',
            execution_count: null,
            id: `save-test-${Date.now()}`,
            metadata: {},
            outputs: [],
            source: ['seed = 1\n'],
        }],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;
        const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';

        await page.waitForSelector('[data-save-notebook="true"]');
        assert.equal(await page.locator('[data-save-notebook="true"]').count(), 1);

        await page.locator('[data-cell-id] .cm-content').click();
        await page.keyboard.press(`${commandKey}+A`);
        await page.keyboard.type('saved_value = 7');

        await page.waitForFunction(() => {
            const button = document.querySelector('[data-save-notebook="true"]');
            return button instanceof HTMLButtonElement && !button.disabled;
        });

        const prevented = await page.evaluate((useMeta) => {
            const event = new KeyboardEvent('keydown', {
                key: 's',
                bubbles: true,
                cancelable: true,
                metaKey: useMeta,
                ctrlKey: !useMeta,
            });
            document.dispatchEvent(event);
            return event.defaultPrevented;
        }, process.platform === 'darwin');
        assert.equal(prevented, true);

        await page.waitForFunction(() => {
            const button = document.querySelector('[data-save-notebook="true"]');
            return Boolean(
                button instanceof HTMLButtonElement
                && button.disabled
            );
        });

        const source = await waitForNotebookSource(notebookPath, 'saved_value = 7');
        assert.match(source, /saved_value = 7/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('workspace preview renders markdown, html tables, and json outputs from persisted notebooks', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-rich-output-${Date.now()}.ipynb`);
    const notebook = {
        cells: [{
            cell_type: 'code',
            execution_count: 1,
            id: `rich-output-${Date.now()}`,
            metadata: {},
            outputs: [
                {
                    output_type: 'display_data',
                    data: {
                        'text/plain': '<IPython.core.display.Markdown object>',
                        'text/markdown': '## Summary\n\n| key | value |\n| --- | --- |\n| alpha | 1 |',
                    },
                    metadata: {},
                },
                {
                    output_type: 'display_data',
                    data: {
                        'text/plain': 'alpha  1',
                        'text/html': '<table><thead><tr><th>key</th><th>value</th></tr></thead><tbody><tr><td>alpha</td><td>1</td></tr></tbody></table>',
                    },
                    metadata: {},
                },
                {
                    output_type: 'display_data',
                    data: {
                        'application/json': { alpha: 1, items: ['x', 'y'] },
                    },
                    metadata: {},
                },
            ],
            source: ['display("rich outputs")\n'],
        }],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.waitForSelector('[data-rich-output-kind="markdown"]');
        await page.waitForSelector('[data-rich-output-kind="html"] table');
        await page.waitForSelector('[data-rich-output-kind="json"]');

        assert.match(
            await page.locator('[data-rich-output-kind="markdown"] h2').textContent(),
            /Summary/,
        );
        assert.equal(
            await page.locator('[data-rich-output-kind="html"] table tbody td').first().textContent(),
            'alpha',
        );
        assert.match(
            await page.locator('[data-rich-output-kind="json"]').textContent(),
            /"items": \[/,
        );
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview renders a notebook-like surface and runs code through the standalone host', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-live-${Date.now()}.ipynb`);
    const notebook = {
        cells: [
            {
                cell_type: 'markdown',
                id: `jupyterlab-md-${Date.now()}`,
                metadata: {},
                source: ['# Notebook Demo\n', '\n', 'The code cell below should run live.\n'],
            },
            {
                cell_type: 'code',
                execution_count: null,
                id: `jupyterlab-code-${Date.now()}`,
                metadata: {},
                outputs: [],
                source: [
                    'from IPython.display import HTML, Markdown, display\n',
                    'display(Markdown("## Live output"))\n',
                    'display(HTML("<table><thead><tr><th>kind</th><th>value</th></tr></thead><tbody><tr><td>html</td><td>42</td></tr></tbody></table>"))\n',
                    'print("Notebook execution is live.")\n',
                ],
            },
        ],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.getByText('from IPython.display import HTML, Markdown, display').click();
        await page.getByRole('button', { name: 'Run', exact: true }).click();

        await page.waitForFunction(() => document.body.textContent?.includes('Notebook execution is live.'));
        await page.waitForFunction(() => {
            return Array.from(document.querySelectorAll('table td')).some((cell) => cell.textContent === '42');
        });

        const textContent = await page.locator('body').textContent();
        assert.match(textContent, /Notebook Demo/);
        assert.match(textContent, /\[1\]:/);
        assert.match(textContent, /Notebook execution is live\./);
        assert.match(textContent, /Live output/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview trusts iframe-backed html only after the notebook is trusted', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-trust-${Date.now()}.ipynb`);
    await stopWorkspaceDaemon(path.resolve(extensionRoot, '..'));
    const notebook = {
        cells: [
            {
                cell_type: 'code',
                execution_count: 1,
                id: `jupyterlab-trust-cell-${Date.now()}`,
                metadata: {},
                outputs: [
                    {
                        output_type: 'display_data',
                        data: {
                            'text/plain': '<iframe fallback>',
                            'text/html': '<iframe srcdoc="<p>trusted iframe payload</p>" sandbox="allow-same-origin"></iframe>',
                        },
                        metadata: {},
                    },
                ],
                source: ['"trusted iframe demo"\n'],
            },
        ],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        assert.equal(await page.locator('.jp-OutputArea-output iframe').count(), 0);

        await page.getByRole('button', { name: 'Trust' }).click();

        await page.waitForFunction(() => document.body.textContent?.includes('Trusted'));
        await page.waitForFunction(() => document.querySelectorAll('.jp-OutputArea-output iframe').length === 1);

        const iframeText = await page.locator('.jp-OutputArea-output iframe').evaluate(async (node) => {
            if (!(node instanceof HTMLIFrameElement)) {
                return '';
            }
            await new Promise((resolve) => {
                if (node.contentDocument?.readyState === 'complete') {
                    resolve();
                    return;
                }
                node.addEventListener('load', () => resolve(), { once: true });
            });
            return node.contentDocument?.body?.textContent ?? '';
        });

        assert.match(iframeText, /trusted iframe payload/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview renders saved ipywidget outputs from notebook metadata', async () => {
    const sourceNotebookPath = '/Users/giladrubin/python_workspace/mafat_hebrew_retrieval/notebooks/old/Finetune_rerank_sentense_BGE.ipynb';
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-widget-${Date.now()}.ipynb`);
    const sourceNotebook = JSON.parse(await fs.readFile(sourceNotebookPath, 'utf8'));
    const fixture = collectWidgetFixture(sourceNotebook);
    const notebook = {
        cells: [fixture.cell],
        metadata: fixture.metadata,
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.waitForFunction(() => {
            return document.body.textContent?.includes('config.json:')
                && document.body.textContent?.includes('0.00/799');
        }, { timeout: 60_000 });

        const textContent = await page.locator('body').textContent();
        assert.match(textContent, /config\.json:/);
        assert.match(textContent, /0\.00\/799/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview keeps the console clean and supports command-mode insert, arrow navigation, and Shift+Enter execution', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-shortcuts-${Date.now()}.ipynb`);
    const marker = `Inserted smoke ${Date.now()}`;
    const notebook = {
        cells: [
            {
                cell_type: 'code',
                execution_count: null,
                id: `jupyterlab-shortcuts-${Date.now()}`,
                metadata: {},
                outputs: [],
                source: ['print("seed cell")\n'],
            },
        ],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const consoleMessages = [];
    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    handle.page.on('console', (message) => {
        consoleMessages.push({ type: message.type(), text: message.text() });
    });
    handle.page.on('pageerror', (error) => {
        consoleMessages.push({ type: 'pageerror', text: String(error.stack || error) });
    });

    try {
        const { page } = handle;
        await page.locator('.jp-CodeCell .cm-content').first().click();
        await page.keyboard.press('Escape');
        await page.keyboard.press('b');
        await page.waitForFunction(() => document.querySelectorAll('.jp-Cell').length === 2);

        await page.keyboard.type(`print(${JSON.stringify(marker)})`);
        await page.keyboard.press('Escape');
        await page.keyboard.press('ArrowUp');
        await page.waitForFunction(() => {
            const activeCell = document.querySelector('.jp-Cell.jp-mod-active');
            return activeCell?.textContent?.includes('seed cell') ?? false;
        });
        await page.keyboard.press('ArrowDown');
        await page.waitForFunction((expectedMarker) => {
            const activeCell = document.querySelector('.jp-Cell.jp-mod-active');
            return activeCell?.textContent?.includes(expectedMarker) ?? false;
        }, marker);
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction((expectedMarker) => {
            return document.body.textContent?.includes(expectedMarker) ?? false;
        }, marker);

        const notebookText = await page.locator('body').textContent();
        assert.match(notebookText, /seed cell/);
        assert.match(notebookText, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
        assert.equal(await page.locator('.jp-Cell').count(), 2);
        assert.doesNotMatch(notebookText, /\bSaved\b/);

        const noisyMessages = consoleMessages.filter((message) => {
            return message.type === 'pageerror'
                || /Invalid access: Add Yjs type to a document before reading data\./.test(message.text)
                || /Failed to load resource/.test(message.text);
        });
        assert.deepEqual(noisyMessages, []);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview enables CodeMirror completion popups for python code', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-completion-${Date.now()}.ipynb`);
    await writeNotebook(notebookPath, ['']);

    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        const firstEditor = page.locator('.jp-CodeCell .cm-content').first();
        await firstEditor.click();
        await page.keyboard.type('imp');
        await page.keyboard.press('Control+Space');

        await page.waitForSelector('.cm-tooltip-autocomplete');
        const completionText = await page.locator('.cm-tooltip-autocomplete').textContent();
        assert.match(completionText, /import/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview selects the next code cell in command mode after Shift+Enter and preserves continued execution', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-shift-enter-${Date.now()}.ipynb`);
    const notebook = {
        cells: [
            {
                cell_type: 'markdown',
                id: `jupyterlab-shift-enter-md-${Date.now()}`,
                metadata: {},
                source: ['# Shift Enter Regression\n', '\n', 'Reproduces the repeated Shift+Enter workflow.\n'],
            },
            {
                cell_type: 'code',
                execution_count: null,
                id: `jupyterlab-shift-enter-code-${Date.now()}`,
                metadata: {},
                outputs: [],
                source: [],
            },
        ],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const consoleMessages = [];
    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    handle.page.on('console', (message) => {
        consoleMessages.push({ type: message.type(), text: message.text() });
    });
    handle.page.on('pageerror', (error) => {
        consoleMessages.push({ type: 'pageerror', text: String(error.stack || error) });
    });

    try {
        const { page } = handle;

        const firstEditor = page.locator('.jp-CodeCell .cm-content').first();
        await firstEditor.click();
        await page.keyboard.type('a=5');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            return document.querySelectorAll('.jp-CodeCell').length >= 2;
        });

        await page.waitForFunction(() => {
            const activeCell = document.querySelector('.jp-Cell.jp-mod-active');
            return activeCell instanceof HTMLElement
                && activeCell.classList.contains('jp-CodeCell')
                && (activeCell.textContent?.includes('[ ]:') ?? false);
        });
        await page.waitForFunction(() => {
            const notebook = document.querySelector('.agent-repl-jupyterlab-notebook');
            const active = document.activeElement;
            return notebook?.classList.contains('jp-mod-commandMode')
                && !notebook?.classList.contains('jp-mod-editMode')
                && active instanceof HTMLElement
                && !active.closest('[role="textbox"]');
        });

        const secondEditor = page.locator('.jp-CodeCell .cm-content').nth(1);
        await page.keyboard.press('Enter');
        await secondEditor.click();
        await page.keyboard.insertText('a');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            return Array.from(document.querySelectorAll('.jp-OutputArea-output')).some((node) => {
                return (node.textContent || '').trim() === '5';
            });
        });

        const savedNotebook = await waitForNotebook(notebookPath, (currentNotebook) => {
            const cells = currentNotebook.cells ?? [];
            return cells.length >= 4
                && normalizeNotebookSource(cells[1]?.source) === 'a=5'
                && normalizeNotebookSource(cells[2]?.source) === 'a'
                && JSON.stringify(cells[2]?.outputs ?? []).includes('"5"');
        });

        assert.equal(normalizeNotebookSource(savedNotebook.cells[1].source), 'a=5');
        assert.equal(normalizeNotebookSource(savedNotebook.cells[2].source), 'a');
        assert.equal(savedNotebook.cells[3].cell_type, 'code');
        assert.equal(normalizeNotebookSource(savedNotebook.cells[3].source), '');
        assert.match(await page.locator('body').textContent(), /\[.*\]:\s*5/);

        const noisyMessages = consoleMessages.filter((message) => {
            return message.type === 'warning'
                || message.type === 'error'
                || message.type === 'pageerror'
                || /Failed to load resource/.test(message.text);
        });
        assert.deepEqual(noisyMessages, []);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview selects all cells from command mode with cmd/ctrl-a', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-select-all-${Date.now()}.ipynb`);
    await writeNotebook(notebookPath, ['alpha = 1', 'beta = 2', 'gamma = 3']);

    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.locator('.jp-CodeCell .cm-content').first().click();
        await page.keyboard.press('Escape');
        await page.keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');

        await page.waitForFunction(() => {
            return document.querySelectorAll('.jp-Cell.jp-mod-selected').length === document.querySelectorAll('.jp-Cell').length;
        });

        const selectionState = await page.evaluate(() => {
            const notebook = document.querySelector('.agent-repl-jupyterlab-notebook');
            return {
                selectedCount: document.querySelectorAll('.jp-Cell.jp-mod-selected').length,
                totalCount: document.querySelectorAll('.jp-Cell').length,
                commandMode: notebook?.classList.contains('jp-mod-commandMode') ?? false,
            };
        });

        assert.equal(selectionState.selectedCount, 3);
        assert.equal(selectionState.totalCount, 3);
        assert.equal(selectionState.commandMode, true);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('jupyterlab preview undoes the last structural insert with z in command mode', async () => {
    const notebookPath = path.join(extensionRoot, '..', 'tmp', `jupyterlab-undo-${Date.now()}.ipynb`);
    await writeNotebook(notebookPath, ['alpha = 1', 'beta = 2']);

    const handle = await openJupyterLabWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.locator('.jp-CodeCell .cm-content').first().click();
        await page.keyboard.press('Escape');
        await waitForJupyterLabCommandMode(page);
        await focusJupyterLabNotebook(page);
        await page.keyboard.press('b');

        await page.waitForFunction(() => document.querySelectorAll('.jp-CodeCell').length === 3);
        await page.waitForFunction(() => {
            const debug = window.__agentReplJupyterLab;
            return Boolean(debug)
                && debug.getPendingCommandCount() === 0
                && debug.getUndoDepth() > 0;
        });
        await waitForNotebook(notebookPath, (currentNotebook) => (currentNotebook.cells ?? []).length === 3, 30_000);

        await page.keyboard.press('Escape');
        await waitForJupyterLabCommandMode(page);
        await focusJupyterLabNotebook(page);
        await page.evaluate(() => {
            const debug = window.__agentReplJupyterLab;
            if (!debug) {
                throw new Error('JupyterLab preview debug bridge was not available for undo verification');
            }
            void debug.executeCommand('agent-repl:notebook-undo');
        });

        await page.waitForFunction(() => {
            const debug = window.__agentReplJupyterLab;
            return document.querySelectorAll('.jp-CodeCell').length === 2
                && Boolean(debug)
                && debug.getPendingCommandCount() === 0;
        });
        await waitForJupyterLabCommandMode(page);

        const savedNotebook = await waitForNotebook(notebookPath, (currentNotebook) => (currentNotebook.cells ?? []).length === 2, 30_000);
        assert.equal(savedNotebook.cells.length, 2);
        assert.equal(normalizeNotebookSource(savedNotebook.cells[0].source).trim(), 'alpha = 1');
        assert.equal(normalizeNotebookSource(savedNotebook.cells[1].source).trim(), 'beta = 2');
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('copy prefers markdown source and rendered html text for rich persisted outputs', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-rich-copy-${Date.now()}.ipynb`);
    const notebook = {
        cells: [{
            cell_type: 'code',
            execution_count: 1,
            id: `rich-copy-${Date.now()}`,
            metadata: {},
            outputs: [
                {
                    output_type: 'display_data',
                    data: {
                        'text/plain': '<IPython.core.display.Markdown object>',
                        'text/markdown': '## Summary\n\n| key | value |\n| --- | --- |\n| alpha | 1 |',
                    },
                    metadata: {},
                },
                {
                    output_type: 'display_data',
                    data: {
                        'text/plain': 'alpha  1',
                        'text/html': '<table><thead><tr><th>category</th><th>total</th></tr></thead><tbody><tr><td>alpha</td><td>12</td></tr></tbody></table>',
                    },
                    metadata: {},
                },
            ],
            source: ['display("rich copy")\n'],
        }],
        metadata: {
            kernelspec: {
                display_name: 'Python 3',
                language: 'python',
                name: 'python3',
            },
            language_info: {
                name: 'python',
            },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };

    await fs.mkdir(path.dirname(notebookPath), { recursive: true });
    await fs.writeFile(notebookPath, JSON.stringify(notebook, null, 2));

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.evaluate(() => {
            window.__agentReplCopiedText = '';
            const originalClipboard = navigator.clipboard;
            Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {
                    ...originalClipboard,
                    readText: async () => window.__agentReplCopiedText ?? '',
                    writeText: async (text) => {
                        window.__agentReplCopiedText = String(text);
                    },
                },
            });
        });

        await page.waitForSelector('[data-cell-id] [data-output-copy="true"]');
        await page.locator('[data-cell-id] [data-output-copy="true"]').click();
        await page.waitForFunction(() => {
            const button = document.querySelector('[data-cell-id] [data-output-copy="true"]');
            return button?.getAttribute('data-copied') === 'true';
        });

        const copiedText = await page.evaluate(() => navigator.clipboard.readText());
        assert.match(copiedText, /^## Summary/);
        assert.match(copiedText, /\| key \| value \|/);
        assert.match(copiedText, /\| category \| total \|/);
        assert.doesNotMatch(copiedText, /<table>|<th>|<td>/);
        assert.doesNotMatch(copiedText, /<IPython\.core\.display\.Markdown object>/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('mock preview executes IPython display objects with jupyter-like rich outputs', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';
        const code = [
            'from IPython.display import Markdown, HTML, SVG, JSON, display',
            'display(Markdown("## Live Markdown"))',
            'display(HTML("<table><tr><th>kind</th><th>value</th></tr><tr><td>html</td><td>1</td></tr></table>"))',
            'display(JSON({"alpha": 1, "items": ["x", "y"]}))',
            'SVG("<svg xmlns=\\"http://www.w3.org/2000/svg\\" width=\\"120\\" height=\\"40\\"><rect width=\\"120\\" height=\\"40\\" rx=\\"8\\" fill=\\"#d97706\\"/></svg>")',
        ].join('\n');

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.press(`${commandKey}+A`);
        await page.keyboard.press('Backspace');
        await page.keyboard.type(code);
        await page.keyboard.press('Shift+Enter');

        await page.waitForSelector('[data-cell-id="preview-code-1"] [data-rich-output-kind="markdown"]');
        await page.waitForSelector('[data-cell-id="preview-code-1"] [data-rich-output-kind="html"] table');
        await page.waitForSelector('[data-cell-id="preview-code-1"] [data-rich-output-kind="json"]');
        await page.waitForSelector('[data-cell-id="preview-code-1"] [data-rich-output-kind="svg"] svg');

        assert.match(
            await page.locator('[data-cell-id="preview-code-1"] [data-rich-output-kind="markdown"] h2').textContent(),
            /Live Markdown/,
        );
        assert.equal(
            await page.locator('[data-cell-id="preview-code-1"] [data-rich-output-kind="html"] table tbody td').first().textContent(),
            'html',
        );
        assert.match(
            await page.locator('[data-cell-id="preview-code-1"] [data-rich-output-kind="json"]').textContent(),
            /"items": \[/,
        );
    } finally {
        await closePageHandle(handle);
    }
});

test('persisted outputs stay neutral until the preview runtime is actually active', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        assert.equal(await page.locator('[data-cell-id="preview-code-1"] [data-cell-status]').count(), 0);
        assert.equal(await page.locator('[data-cell-id="preview-code-2"] [data-cell-status]').count(), 0);
    } finally {
        await closePageHandle(handle);
    }
});

test('toolbar does not render persistent add-cell buttons', async () => {
    const handle = await openPreviewPage();
    try {
        const labels = await handle.page.locator('[data-toolbar="notebook"] button').evaluateAll((nodes) =>
            nodes
                .map((node) => node.textContent?.replace(/\s+/g, ' ').trim() ?? '')
                .filter(Boolean)
        );

        assert.equal(labels.some((label) => label === 'Code' || label === 'Markdown' || label === 'MD'), false);
    } finally {
        await closePageHandle(handle);
    }
});

test('browser preview renders a minimal explorer and cmd/ctrl-b toggles it without inserting cells', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';

        await page.waitForSelector('[data-explorer-panel="true"][data-collapsed="false"]');
        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.press(`${commandKey}+B`);

        await page.waitForSelector('[data-explorer-panel="true"][data-collapsed="true"]');
        assert.equal(await page.locator('[data-cell-id]').count(), 3);

        await page.keyboard.press(`${commandKey}+B`);
        await page.waitForSelector('[data-explorer-panel="true"][data-collapsed="false"]');
    } finally {
        await closePageHandle(handle);
    }
});

test('cmd/ctrl-b toggles the explorer without typing a literal b into the active editor', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.press(`${commandKey}+A`);
        await page.keyboard.type('locked_source = 1');
        await page.keyboard.press(`${commandKey}+B`);

        await page.waitForSelector('[data-explorer-panel="true"][data-collapsed="true"]');

        const firstLine = await page.locator('[data-cell-id="preview-code-1"] .cm-line').first().textContent();
        assert.equal(firstLine, 'locked_source = 1');
    } finally {
        await closePageHandle(handle);
    }
});

test('clicking a notebook in the preview explorer switches the visible notebook', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-explorer-item-path="preview/agents-demo.ipynb"]').click();
        await page.waitForSelector('[data-cell-id="agents-code-1"] .cm-editor');

        const activePath = await page.locator('[data-explorer-item-path="preview/agents-demo.ipynb"]').getAttribute('data-active');
        const notebookText = await page.locator('[data-cell-id="agents-md-1"]').textContent();
        const codeText = await page.locator('[data-cell-id="agents-code-1"]').textContent();

        assert.equal(activePath, 'true');
        assert.match(notebookText, /browser explorer switching between preview notebooks/i);
        assert.match(codeText, /reviewer/);
    } finally {
        await closePageHandle(handle);
    }
});

test('switching notebooks clears running preview state from the previously selected notebook', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.type('\nimport time\ntime.sleep(2)');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const stop = document.querySelector('button[title="Stop"]');
            return stop instanceof HTMLButtonElement && !stop.disabled;
        });

        await page.locator('[data-explorer-item-path="preview/agents-demo.ipynb"]').click();
        await page.waitForSelector('[data-cell-id="agents-code-1"] .cm-editor');

        await page.waitForFunction(() => {
            const stop = document.querySelector('button[title="Stop"]');
            const statuses = document.querySelectorAll('[data-cell-id^="agents-"] [data-cell-status]');
            return stop instanceof HTMLButtonElement && stop.disabled && statuses.length === 0;
        });

        const switchedState = await page.evaluate(() => {
            const stop = document.querySelector('button[title="Stop"]');
            const activePath = document.querySelector('[data-explorer-item-path="preview/agents-demo.ipynb"]')
                ?.getAttribute('data-active');
            const visibleCellIds = Array.from(document.querySelectorAll('[data-cell-id]'))
                .map((node) => node.getAttribute('data-cell-id'));
            const agentStatuses = document.querySelectorAll('[data-cell-id^="agents-"] [data-cell-status]').length;
            return {
                stopDisabled: stop instanceof HTMLButtonElement ? stop.disabled : null,
                activePath,
                visibleCellIds,
                agentStatuses,
            };
        });

        assert.equal(switchedState.stopDisabled, true);
        assert.equal(switchedState.activePath, 'true');
        assert.deepEqual(switchedState.visibleCellIds, ['agents-md-1', 'agents-code-1']);
        assert.equal(switchedState.agentStatuses, 0);
    } finally {
        await closePageHandle(handle);
    }
});

test('switching notebooks from the explorer clears runtime-derived state from the previous notebook', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const executionCount = document.querySelector('[data-cell-id="preview-code-1"] code')?.textContent ?? null;
            return executionCount === '[1]';
        });

        await page.locator('[data-explorer-item-path="preview/agents-demo.ipynb"]').click();
        await page.waitForSelector('[data-cell-id="agents-code-1"] .cm-editor');

        await page.waitForFunction(() => {
            const oldNotebookCell = document.querySelector('[data-cell-id="preview-code-1"]');
            const activeStatus = document.querySelector('[data-cell-status]');
            const newNotebookExecutionCount =
                document.querySelector('[data-cell-id="agents-code-1"] code')?.textContent ?? null;
            return !oldNotebookCell && !activeStatus && newNotebookExecutionCount === '[1]';
        });

        const switchedState = await page.evaluate(() => ({
            previousNotebookStillVisible: Boolean(document.querySelector('[data-cell-id="preview-code-1"]')),
            visibleStatusCount: document.querySelectorAll('[data-cell-status]').length,
            newNotebookExecutionCount: document.querySelector('[data-cell-id="agents-code-1"] code')?.textContent ?? null,
            activePath: document.querySelector('[data-explorer-item-path="preview/agents-demo.ipynb"]')?.getAttribute('data-active'),
        }));

        assert.equal(switchedState.previousNotebookStillVisible, false);
        assert.equal(switchedState.visibleStatusCount, 0);
        assert.equal(switchedState.newNotebookExecutionCount, '[1]');
        assert.equal(switchedState.activePath, 'true');
    } finally {
        await closePageHandle(handle);
    }
});

test('cmd/ctrl-b toggles the browser explorer without inserting a cell or mutating the active draft', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.type('\nshortcut_probe = 1');

        const before = await page.evaluate(() => ({
            cellCount: document.querySelectorAll('[data-cell-id]').length,
            collapsed: document.querySelector('[data-explorer-panel="true"]')?.getAttribute('data-collapsed'),
            source: document.querySelector('[data-cell-id="preview-code-1"] .cm-content')?.textContent ?? '',
        }));

        await page.keyboard.press(`${commandKey}+B`);

        await page.waitForFunction(() => {
            const panel = document.querySelector('[data-explorer-panel="true"]');
            return panel?.getAttribute('data-collapsed') === 'true';
        });

        const afterCollapse = await page.evaluate(() => ({
            cellCount: document.querySelectorAll('[data-cell-id]').length,
            collapsed: document.querySelector('[data-explorer-panel="true"]')?.getAttribute('data-collapsed'),
            source: document.querySelector('[data-cell-id="preview-code-1"] .cm-content')?.textContent ?? '',
        }));

        assert.equal(afterCollapse.cellCount, before.cellCount);
        assert.equal(afterCollapse.collapsed, 'true');
        assert.equal(afterCollapse.source, before.source);

        await page.keyboard.press(`${commandKey}+B`);

        await page.waitForFunction(() => {
            const panel = document.querySelector('[data-explorer-panel="true"]');
            return panel?.getAttribute('data-collapsed') === 'false';
        });

        const afterExpand = await page.locator('[data-explorer-panel="true"]').getAttribute('data-collapsed');
        assert.equal(afterExpand, 'false');
    } finally {
        await closePageHandle(handle);
    }
});

test('command-mode a then enter inserts a code cell above and opens it in edit mode', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        assert.equal(await page.locator('[data-cell-id]').count(), 3);

        await page.locator('[data-cell-id="preview-code-1"]').dispatchEvent('click');
        await page.waitForTimeout(75);
        await page.keyboard.press('KeyA');
        await page.keyboard.press('Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);

        const insertedCell = page.locator('[data-cell-id]').nth(1);
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const insertedEditor = cells[1]?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return Boolean(insertedEditor && active && insertedEditor.contains(active));
        });
        await page.keyboard.type('value_above = 1');

        const insertedText = await insertedCell.locator('.cm-line').first().textContent();
        assert.equal(insertedText, 'value_above = 1');

        const editorFocused = await insertedCell.locator('.cm-editor').evaluate((element) => {
            const active = document.activeElement;
            return Boolean(active && element.contains(active));
        });
        assert.equal(editorFocused, true);
    } finally {
        await closePageHandle(handle);
    }
});

test('command-mode b then enter inserts a code cell below and opens it in edit mode', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        assert.equal(await page.locator('[data-cell-id]').count(), 3);

        await page.locator('[data-cell-id="preview-code-1"]').dispatchEvent('click');
        await page.waitForTimeout(75);
        await page.keyboard.press('KeyB');
        await page.keyboard.press('Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);

        const insertedCell = page.locator('[data-cell-id]').nth(2);
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const insertedEditor = cells[2]?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return Boolean(insertedEditor && active && insertedEditor.contains(active));
        });
        await page.keyboard.type('value = 1');

        const insertedText = await insertedCell.locator('.cm-line').first().textContent();
        assert.equal(insertedText, 'value = 1');

        const editorFocused = await insertedCell.locator('.cm-editor').evaluate((element) => {
            const active = document.activeElement;
            return Boolean(active && element.contains(active));
        });
        assert.equal(editorFocused, true);
    } finally {
        await closePageHandle(handle);
    }
});

test('escape leaves edit mode on the focused cell and enter restores edit mode on that same cell', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        const targetCell = page.locator('[data-cell-id="preview-code-1"]');
        await targetCell.locator('.cm-content').click();

        await page.waitForFunction(() => {
            const editor = document.querySelector('[data-cell-id="preview-code-1"] .cm-editor');
            const active = document.activeElement;
            return Boolean(editor && active && editor.contains(active));
        });

        await page.keyboard.press('Escape');

        await page.waitForFunction(() => {
            const article = document.querySelector('[data-cell-id="preview-code-1"] article');
            const editor = document.querySelector('[data-cell-id="preview-code-1"] .cm-editor');
            const active = document.activeElement;
            return article instanceof HTMLElement
                && Boolean(article.style.boxShadow && article.style.boxShadow !== 'none')
                && Boolean(editor)
                && !(active && editor.contains(active));
        });

        await page.keyboard.press('Enter');

        await page.waitForFunction(() => {
            const editor = document.querySelector('[data-cell-id="preview-code-1"] .cm-editor');
            const active = document.activeElement;
            return Boolean(editor && active && editor.contains(active));
        });

        const restored = await targetCell.locator('.cm-editor').evaluate((element) => {
            const active = document.activeElement;
            return Boolean(active && element.contains(active));
        });
        assert.equal(restored, true);
    } finally {
        await closePageHandle(handle);
    }
});

test('escape leaves edit mode so notebook command keys take over again', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.type('\nvalue = 1');
        await page.keyboard.press('Escape');
        await page.waitForTimeout(75);
        await page.keyboard.press('KeyB');
        await page.keyboard.press('Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);

        const cellCount = await page.locator('[data-cell-id]').count();
        const firstCellText = await page.locator('[data-cell-id="preview-code-1"] .cm-content').textContent();

        assert.equal(cellCount, 4);
        assert.match(firstCellText ?? '', /value = 1/);
    } finally {
        await closePageHandle(handle);
    }
});

test('command-mode m and y switch the selected cell between markdown and code', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const firstCell = page.locator('[data-cell-id="preview-code-1"]');

        await firstCell.dispatchEvent('click');
        await page.waitForTimeout(75);
        await page.keyboard.press('KeyM');

        await page.waitForFunction(() => {
            const cell = document.querySelector('[data-cell-id="preview-code-1"]');
            return Boolean(cell && cell.textContent?.includes('Markdown') && !cell.querySelector('.cm-editor'));
        });

        await page.keyboard.press('KeyY');

        await page.waitForFunction(() => {
            const cell = document.querySelector('[data-cell-id="preview-code-1"]');
            return Boolean(cell && cell.textContent?.includes('Python') && cell.querySelector('.cm-editor'));
        });

        assert.equal(await firstCell.locator('.cm-editor').count(), 1);
        assert.equal(await firstCell.locator('.markdown-content').count(), 0);
    } finally {
        await closePageHandle(handle);
    }
});

test('shift-enter from the editor selects the next cell immediately before execution finishes', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.type('\nimport time\ntime.sleep(2)');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const nextArticle = document.querySelector('[data-cell-id="preview-code-2"] article');
            const firstExecutionCount = document.querySelector('[data-cell-id="preview-code-1"] code')?.textContent ?? null;
            const nextBoxShadow = nextArticle instanceof HTMLElement ? nextArticle.style.boxShadow : '';
            return Boolean(nextBoxShadow && nextBoxShadow !== 'none' && firstExecutionCount === '[1]');
        });

        const immediateState = await page.evaluate(() => ({
            nextCellSelected: (() => {
                const nextArticle = document.querySelector('[data-cell-id="preview-code-2"] article');
                if (!(nextArticle instanceof HTMLElement)) {
                    return false;
                }
                return Boolean(nextArticle.style.boxShadow && nextArticle.style.boxShadow !== 'none');
            })(),
            firstExecutionCount: document.querySelector('[data-cell-id="preview-code-1"] code')?.textContent ?? null,
        }));

        assert.equal(immediateState.nextCellSelected, true);
        assert.equal(immediateState.firstExecutionCount, '[1]');

        await page.waitForFunction(() => {
            const first = document.querySelector('[data-cell-id="preview-code-1"] code');
            return first?.textContent === '[3]';
        });

        const firstExecutionCount = await page.locator('[data-cell-id="preview-code-1"] code').textContent();
        const secondExecutionCount = await page.locator('[data-cell-id="preview-code-2"] code').textContent();

        assert.equal(firstExecutionCount, '[3]');
        assert.equal(secondExecutionCount, '[2]');
    } finally {
        await closePageHandle(handle);
    }
});

test('text outputs expose a copy control that flips into a copied state', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.waitForSelector('[data-cell-id="preview-code-1"] [data-output-copy="true"]');
        assert.equal(await page.locator('[data-cell-id="preview-code-1"] [data-output-copy="true"]').count(), 1);
        await page.locator('[data-cell-id="preview-code-1"] [data-output-copy="true"]').click();

        await page.waitForFunction(() => {
            const button = document.querySelector('[data-cell-id="preview-code-1"] [data-output-copy="true"]');
            return button?.getAttribute('data-copied') === 'true';
        });

        const copiedText = await page.evaluate(() => navigator.clipboard.readText());
        assert.equal(copiedText, '"hello from preview"');
    } finally {
        await closePageHandle(handle);
    }
});

test('multiple textual output chunks still render a single copy button per cell', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-2"] .cm-content').click();
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const cell = document.querySelector('[data-cell-id="preview-code-2"]');
            return cell?.textContent?.includes('row 2');
        });

        assert.equal(await page.locator('[data-cell-id="preview-code-2"] [data-output-copy="true"]').count(), 1);
    } finally {
        await closePageHandle(handle);
    }
});

test('copy preserves stream line breaks without adding extra blank spacing', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-2"] [data-output-copy="true"]').click();
        await page.waitForFunction(() => {
            const button = document.querySelector('[data-cell-id="preview-code-2"] [data-output-copy="true"]');
            return button?.getAttribute('data-copied') === 'true';
        });

        const copiedText = await page.evaluate(() => navigator.clipboard.readText());
        assert.equal(copiedText, 'row 0\nrow 1\nrow 2\n');
    } finally {
        await closePageHandle(handle);
    }
});

test('plain text and stream outputs use the same vertical padding treatment', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const paddings = await page.evaluate(() => {
            const first = document.querySelector('[data-cell-id="preview-code-1"]')
                ?.parentElement?.parentElement?.querySelector('[data-output-text-block="default"]');
            const second = document.querySelector('[data-cell-id="preview-code-2"]')
                ?.parentElement?.parentElement?.querySelector('[data-output-text-block="default"]');
            if (!(first instanceof HTMLElement) || !(second instanceof HTMLElement)) {
                return null;
            }
            const firstStyle = getComputedStyle(first);
            const secondStyle = getComputedStyle(second);
            return {
                firstTop: firstStyle.paddingTop,
                firstBottom: firstStyle.paddingBottom,
                secondTop: secondStyle.paddingTop,
                secondBottom: secondStyle.paddingBottom,
            };
        });

        assert.deepEqual(paddings, {
            firstTop: '0px',
            firstBottom: '0px',
            secondTop: '0px',
            secondBottom: '0px',
        });
    } finally {
        await closePageHandle(handle);
    }
});

test('error output strips ANSI tokens and uses the tighter Error status', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';

        await page.locator('[data-cell-id="preview-code-1"] .cm-content').click();
        await page.keyboard.press(`${commandKey}+A`);
        await page.keyboard.press('Backspace');
        await page.keyboard.type('test');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const cell = document.querySelector('[data-cell-id="preview-code-1"]');
            const status = cell?.querySelector('[data-cell-status="failed"]');
            return status?.textContent?.includes('Error');
        });

        const outputText = await page.locator('[data-cell-id="preview-code-1"]').textContent();
        const statusText = await page.locator('[data-cell-id="preview-code-1"] [data-cell-status="failed"]').textContent();
        assert.match(statusText, /\bError\b/);
        assert.match(outputText, /NameError: name 'test' is not defined/);
        assert.match(outputText, /Traceback \(most recent call last\):/);
        assert.match(outputText, /File "<exec>", line 1, in <module>/);
        assert.doesNotMatch(outputText, /_pyodide\/_base\.py/);
        assert.doesNotMatch(outputText, /\[(?:\d{1,3}(?:;\d{1,3})*)m/);
        assert.doesNotMatch(outputText, /cdn\.jsdelivr\.net\/pyodide|wasm-function|callPyObjectMaybePromising/);
    } finally {
        await closePageHandle(handle);
    }
});

test('arrow-down at the end of a code cell keeps edit mode and moves into the next cell', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"] .cm-line').nth(1).click();
        await page.keyboard.press('End');
        await page.keyboard.press('ArrowDown');

        await page.waitForFunction(() => {
            const nextEditor = document.querySelector('[data-cell-id="preview-code-2"] .cm-editor');
            const active = document.activeElement;
            return Boolean(nextEditor && active && nextEditor.contains(active));
        });

        await page.keyboard.type('next_');
        await page.waitForFunction(() => {
            const firstLine = document.querySelector('[data-cell-id="preview-code-2"] .cm-line');
            return firstLine?.textContent?.startsWith('next_for idx in range(3):');
        });

        const firstLine = await page.locator('[data-cell-id="preview-code-2"] .cm-line').first().textContent();
        assert.equal(firstLine, 'next_for idx in range(3):');
    } finally {
        await closePageHandle(handle);
    }
});

test('arrow-up at the start of a code cell keeps edit mode and moves into the previous cell', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"] .cm-line').first().click();
        await page.keyboard.press('Home');
        await page.keyboard.press('ArrowUp');

        await page.waitForFunction(() => {
            const textarea = document.querySelector('[data-cell-id="preview-md-1"] textarea');
            return Boolean(textarea && document.activeElement === textarea);
        });

        await page.keyboard.type('\nAppended');
        await page.waitForFunction(() => {
            const textarea = document.querySelector('[data-cell-id="preview-md-1"] textarea');
            return textarea?.value?.endsWith('\nAppended');
        });

        const markdownValue = await page.locator('[data-cell-id="preview-md-1"] textarea').inputValue();
        assert.match(markdownValue, /\nAppended$/);
    } finally {
        await closePageHandle(handle);
    }
});

test('dd deletes the focused cell and prefers the cell below for the next focus target', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"]').click();
        await page.keyboard.press('d');
        await page.keyboard.press('d');

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            if (cells.length !== 2) {
                return false;
            }
            const selectedArticle = document.querySelector('[data-cell-id="preview-code-2"] article');
            return selectedArticle instanceof HTMLElement
                && Boolean(selectedArticle.style.boxShadow && selectedArticle.style.boxShadow !== 'none');
        });

        const remainingCellIds = await page.locator('[data-cell-id]').evaluateAll((elements) =>
            elements.map((element) => element.getAttribute('data-cell-id')),
        );
        assert.deepEqual(remainingCellIds, ['preview-md-1', 'preview-code-2']);
    } finally {
        await closePageHandle(handle);
    }
});

test('dd falls back to the cell above when deleting the last cell', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-2"]').click();
        await page.keyboard.press('d');
        await page.keyboard.press('d');

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            if (cells.length !== 2) {
                return false;
            }
            const selectedArticle = document.querySelector('[data-cell-id="preview-code-1"] article');
            return selectedArticle instanceof HTMLElement
                && Boolean(selectedArticle.style.boxShadow && selectedArticle.style.boxShadow !== 'none');
        });

        const remainingCellIds = await page.locator('[data-cell-id]').evaluateAll((elements) =>
            elements.map((element) => element.getAttribute('data-cell-id')),
        );
        assert.deepEqual(remainingCellIds, ['preview-md-1', 'preview-code-1']);
    } finally {
        await closePageHandle(handle);
    }
});

test('command-mode z restores the last deleted cell at the notebook level', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"]').click();
        await page.keyboard.press('d');
        await page.keyboard.press('d');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 2);

        await page.keyboard.press('z');

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            if (cells.length !== 3) {
                return false;
            }
            const ids = cells.map((element) => element.getAttribute('data-cell-id'));
            if (ids.join(',') !== 'preview-md-1,preview-code-1,preview-code-2') {
                return false;
            }
            const selectedArticle = document.querySelector('[data-cell-id="preview-code-1"] article');
            return selectedArticle instanceof HTMLElement
                && Boolean(selectedArticle.style.boxShadow && selectedArticle.style.boxShadow !== 'none');
        });
    } finally {
        await closePageHandle(handle);
    }
});

test('shift-enter executes the latest in-editor source without waiting for a separate draft flush', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-1"]').dispatchEvent('click');
        await page.waitForTimeout(75);
        await page.keyboard.press('KeyB');
        await page.keyboard.press('Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);
        const insertedCell = page.locator('[data-cell-id]').nth(2);
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const insertedEditor = cells[2]?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return Boolean(insertedEditor && active && insertedEditor.contains(active));
        });

        await page.keyboard.type('1 + 1');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const outputText = cells[2]?.nextElementSibling?.textContent ?? '';
            return outputText.includes('2');
        });

        const outputText = await insertedCell.evaluate((element) => {
            const outputContainer = element.nextElementSibling;
            return outputContainer?.textContent ?? '';
        });
        assert.match(outputText, /\b2\b/);
    } finally {
        await closePageHandle(handle);
    }
});

test('shift-enter on the last code cell inserts the next cell inline before execution finishes', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;
        assert.equal(await page.locator('[data-cell-id]').count(), 3);

        await page.locator('[data-cell-id="preview-code-2"] .cm-content').click();
        await page.keyboard.type('\nimport time\ntime.sleep(2)');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const lastCellEditor = cells.at(-1)?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            const priorExecutionCount = document.querySelector('[data-cell-id="preview-code-2"] code')?.textContent ?? null;
            return Boolean(lastCellEditor && active && lastCellEditor.contains(active) && priorExecutionCount === '[2]');
        });

        const immediateState = await page.evaluate(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const lastCellEditor = cells.at(-1)?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            const priorExecutionCount = document.querySelector('[data-cell-id="preview-code-2"] code')?.textContent ?? null;
            return {
                insertedEditorFocused: Boolean(lastCellEditor && active && lastCellEditor.contains(active)),
                priorExecutionCount,
            };
        });

        assert.equal(immediateState.insertedEditorFocused, true);
        assert.equal(immediateState.priorExecutionCount, '[2]');

        const lastCell = page.locator('[data-cell-id]').last();
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const lastCellEditor = cells.at(-1)?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return Boolean(lastCellEditor && active && lastCellEditor.contains(active));
        });
        await page.keyboard.type('next_value = 2');

        const lastCellText = await lastCell.locator('.cm-line').first().textContent();
        assert.equal(lastCellText, 'next_value = 2');
    } finally {
        await closePageHandle(handle);
    }
});

test('shift-enter after restart reopens an existing blank trailing code cell inline', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-2"] .cm-content').click();
        await page.keyboard.type('\nseed = 1');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);
        await page.keyboard.type('value = 1\nvalue');
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 5);

        await page.locator('button[title="Restart"]').click();
        await page.waitForTimeout(250);

        const cellIds = await page.locator('[data-cell-id]').evaluateAll((nodes) =>
            nodes.map((node) => node.getAttribute('data-cell-id')),
        );
        const targetId = cellIds.at(-2);
        assert.ok(targetId);

        await page.locator(`[data-cell-id="${targetId}"] .cm-content`).click({ force: true });
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const lastCellEditor = cells.at(-1)?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return cells.length === 5 && Boolean(lastCellEditor && active && lastCellEditor.contains(active));
        });

        const inlineState = await page.evaluate(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const lastCellEditor = cells.at(-1)?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return {
                cellCount: cells.length,
                trailingCellFocusedInline: Boolean(lastCellEditor && active && lastCellEditor.contains(active)),
            };
        });

        assert.equal(inlineState.cellCount, 5);
        assert.equal(inlineState.trailingCellFocusedInline, true);
    } finally {
        await closePageHandle(handle);
    }
});

test('running that reused trailing cell creates the next trailing cell too', async () => {
    const handle = await openPreviewPage();
    try {
        const { page } = handle;

        await page.locator('[data-cell-id="preview-code-2"] .cm-content').click();
        await page.keyboard.type('\nseed = 1');
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 4);

        await page.keyboard.type('value = 1\nvalue');
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 5);

        await page.locator('button[title="Restart"]').click({ force: true });
        await page.waitForTimeout(250);

        const cellIds = await page.locator('[data-cell-id]').evaluateAll((nodes) =>
            nodes.map((node) => node.getAttribute('data-cell-id')),
        );
        const targetId = cellIds.at(-2);
        assert.ok(targetId);

        await page.locator(`[data-cell-id="${targetId}"] .cm-content`).click({ force: true });
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const secondToLastExecutionCount = cells.at(-2)?.querySelector('code')?.textContent ?? null;
            const lastCellExecutionCount = cells.at(-1)?.querySelector('code')?.textContent ?? null;
            return cells.length === 5
                && secondToLastExecutionCount === '[1]'
                && lastCellExecutionCount === '[ ]';
        });

        const trailingCellId = await page.locator('[data-cell-id]').evaluateAll((nodes) =>
            nodes.at(-1)?.getAttribute('data-cell-id') ?? null,
        );
        assert.ok(trailingCellId);

        await page.locator(`[data-cell-id="${trailingCellId}"] .cm-content`).click({ force: true });
        await page.waitForFunction((cellId) => {
            const editor = document.querySelector(`[data-cell-id="${cellId}"] .cm-editor`);
            const active = document.activeElement;
            return Boolean(editor && active && editor.contains(active));
        }, trailingCellId);
        await page.keyboard.type('next_value = 2\nnext_value');
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 6);

        const inlineState = await page.evaluate(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            return {
                cellCount: cells.length,
                trailingCellId: cells.at(-1)?.getAttribute('data-cell-id') ?? null,
            };
        });

        assert.equal(inlineState.cellCount, 6);
        assert.ok(inlineState.trailingCellId);
    } finally {
        await closePageHandle(handle);
    }
});

test('cmd/ctrl-enter runs the active code cell and advances immediately in workspace preview', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-cmd-enter-${Date.now()}.ipynb`);
    const commandKey = process.platform === 'darwin' ? 'Meta' : 'Control';

    await writeNotebook(notebookPath, [
        'import time\ntime.sleep(2)\nprint("done", flush=True)',
    ]);

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        await page.locator('[data-cell-id] .cm-content').click();
        await page.keyboard.press(`${commandKey}+Enter`);

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const lastCellEditor = cells.at(-1)?.querySelector('.cm-editor') ?? null;
            const active = document.activeElement;
            return cells.length === 2 && Boolean(lastCellEditor && active && lastCellEditor.contains(active));
        });

        await page.keyboard.type('next_value = 2');

        const nextCellText = await page.locator('[data-cell-id]').last().locator('.cm-line').first().textContent();
        assert.equal(nextCellText, 'next_value = 2');
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('workspace preview shows queued status for a second submitted cell while the first is running', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-queued-status-${Date.now()}.ipynb`);

    await writeNotebook(notebookPath, [
        'import time\nfor i in range(6):\n    print(f"tick {i}", flush=True)\n    time.sleep(0.5)',
        'queued_value = 2\nqueued_value',
    ]);

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        const firstCell = page.locator('[data-cell-id]').nth(0);
        const secondCell = page.locator('[data-cell-id]').nth(1);

        await firstCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => {
            const status = document.querySelector('[data-cell-id] [data-cell-status]');
            return status?.getAttribute('data-cell-status') === 'running';
        });

        await secondCell.hover();
        await secondCell.getByTitle('Run cell (Shift+Enter)').click();

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const secondStatus = cells[1]?.querySelector('[data-cell-status]')?.getAttribute('data-cell-status') ?? null;
            return secondStatus === 'queued';
        }, { timeout: 10_000 });

        const statuses = await page.evaluate(() => Array.from(document.querySelectorAll('[data-cell-id]')).map((cell) => ({
            id: cell.getAttribute('data-cell-id'),
            status: cell.querySelector('[data-cell-status]')?.getAttribute('data-cell-status') ?? null,
        })));
        assert.equal(statuses[0]?.status, 'running');
        assert.equal(statuses[1]?.status, 'queued');
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('rerunning a completed cell does not flash completed before returning to running in workspace preview', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-rerun-status-${Date.now()}.ipynb`);

    await writeNotebook(notebookPath, [
        'from time import sleep\nsleep(1)',
    ]);

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;
        const firstCell = page.locator('[data-cell-id]').first();

        await firstCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const status = document.querySelector('[data-cell-id] [data-cell-status]');
            return status?.getAttribute('data-cell-status') === 'completed';
        }, { timeout: 15_000 });

        await firstCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const status = document.querySelector('[data-cell-id] [data-cell-status]');
            return status?.getAttribute('data-cell-status') !== 'completed';
        }, { timeout: 250 });

        await page.waitForFunction(() => {
            const status = document.querySelector('[data-cell-id] [data-cell-status]');
            return status?.getAttribute('data-cell-status') === 'running';
        }, { timeout: 10_000 });
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('rerunning a completed second cell while another cell is active stays visibly queued in workspace preview', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-rerun-queued-${Date.now()}.ipynb`);

    await writeNotebook(notebookPath, [
        'from time import sleep\nfor i in range(4):\n    print(i, flush=True)\n    sleep(0.5)',
        'queued_value = 2\nqueued_value',
    ]);

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;
        const firstCell = page.locator('[data-cell-id]').nth(0);
        const secondCell = page.locator('[data-cell-id]').nth(1);

        await secondCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const secondStatus = cells[1]?.querySelector('[data-cell-status]');
            return secondStatus?.getAttribute('data-cell-status') === 'completed';
        }, { timeout: 15_000 });

        await firstCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => {
            const firstStatus = document.querySelector('[data-cell-id] [data-cell-status]');
            return firstStatus?.getAttribute('data-cell-status') === 'running';
        }, { timeout: 10_000 });

        await secondCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');

        await page.waitForFunction(() => {
            const cells = Array.from(document.querySelectorAll('[data-cell-id]'));
            const secondStatus = cells[1]?.querySelector('[data-cell-status]');
            return secondStatus?.getAttribute('data-cell-status') === 'queued';
        }, { timeout: 250 });
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

test('dd deletes a running cell after returning it to command mode in workspace preview', async () => {
    const notebookPath = path.join(extensionRoot, 'tmp', `browser-delete-running-${Date.now()}.ipynb`);

    await writeNotebook(notebookPath, [
        'import time\nfor i in range(6):\n    print(f"tick {i}", flush=True)\n    time.sleep(0.5)',
        'print("still here")',
    ]);

    const handle = await openWorkspaceNotebookPage(notebookPath);
    try {
        const { page } = handle;

        const firstCell = page.locator('[data-cell-id]').nth(0);

        await firstCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('Shift+Enter');
        await page.waitForFunction(() => {
            const status = document.querySelector('[data-cell-id] [data-cell-status]');
            return status?.getAttribute('data-cell-status') === 'running';
        });

        await firstCell.locator('article').click({ position: { x: 48, y: 10 } });
        await page.keyboard.press('d');
        await page.keyboard.press('d');

        await page.waitForFunction(() => document.querySelectorAll('[data-cell-id]').length === 1, { timeout: 10_000 });

        const remainingText = await page.locator('[data-cell-id]').first().textContent();
        assert.match(remainingText, /still here/);
    } finally {
        await closePageHandle(handle);
        await fs.unlink(notebookPath).catch(() => {});
    }
});

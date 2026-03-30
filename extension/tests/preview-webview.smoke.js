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
let browser;

async function waitForPreviewReady(url, timeoutMs = 30_000) {
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
    const page = await context.newPage();
    await page.goto(previewUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-browser-shell="true"]');
    return { context, page };
}

async function openWorkspaceNotebookPage(notebookPath) {
    const context = await browser.newContext({
        viewport: { width: 1440, height: 960 },
    });
    const page = await context.newPage();
    const targetUrl = `${previewUrl}?path=${encodeURIComponent(notebookPath)}`;
    await page.goto(targetUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-browser-shell="true"]');
    await page.waitForSelector('[data-cell-id] .cm-editor');
    return { context, page };
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

/**
 * V1 Architecture E2E Tests
 *
 * Tests the core v1 architecture commitments end-to-end using the browser
 * preview and CLI:
 *
 * 1. WebSocket live sync — execute via CLI, verify output appears in browser
 * 2. Checkpoint round-trip — create, edit, restore, verify
 * 3. Execution via daemon — click execute in browser, verify output
 * 4. Multi-notebook — two notebooks, independent execution
 * 5. Screenshots at key points
 *
 * Reuses helpers from preview-webview.smoke.js.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');

const { chromium } = require('playwright');

const extensionRoot = path.resolve(__dirname, '..');
const workspaceRoot = path.resolve(extensionRoot, '..');
const screenshotDir = path.join(workspaceRoot, 'tmp', 'e2e-screenshots');

let previewPort = 4173;
let previewUrl = `http://127.0.0.1:${previewPort}/preview.html`;
let previewServer;
let previewServerExitPromise;
let browser;

// ---------------------------------------------------------------------------
// Shared helpers (same patterns as preview-webview.smoke.js)
// ---------------------------------------------------------------------------

async function waitForPreviewReady(url, timeoutMs = 60_000) {
    const startedAt = Date.now();
    while (Date.now() - startedAt < timeoutMs) {
        try {
            const response = await fetch(url, { cache: 'no-store' });
            if (response.ok) return;
        } catch { /* keep polling */ }
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
                if (error) { reject(error); return; }
                if (typeof port !== 'number') { reject(new Error('Failed to allocate port')); return; }
                resolve(port);
            });
        });
    });
}

async function runAgentReplCli(args, cwd = workspaceRoot) {
    return await new Promise((resolve, reject) => {
        const child = spawn('uv', ['run', '--project', cwd, 'agent-repl', ...args], {
            cwd,
            stdio: ['ignore', 'pipe', 'pipe'],
        });
        let stdout = '';
        let stderr = '';
        child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
        child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
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

async function stopWorkspaceDaemon() {
    try {
        await runAgentReplCli(['core', 'stop', '--workspace-root', workspaceRoot]);
    } catch { /* best-effort */ }
}

async function launchBrowser() {
    try {
        return await chromium.launch({ channel: 'chrome', headless: true });
    } catch {
        return chromium.launch({ headless: true });
    }
}

async function writeNotebook(filePath, cellSources) {
    const notebook = {
        cells: cellSources.map((source, index) => ({
            cell_type: 'code',
            execution_count: null,
            id: `e2e-${Date.now()}-${index}`,
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
            language_info: { name: 'python' },
        },
        nbformat: 4,
        nbformat_minor: 5,
    };
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, JSON.stringify(notebook, null, 2));
}

async function openNotebookPage(notebookPath) {
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

async function closePageHandle(handle) {
    await handle.page.close().catch(() => {});
    await handle.context.close().catch(() => {});
}

async function screenshot(page, name) {
    await fs.mkdir(screenshotDir, { recursive: true });
    await page.screenshot({ path: path.join(screenshotDir, `${name}.png`), fullPage: true });
}

async function waitForOutputText(page, cellIndex, expectedSubstring, timeoutMs = 30_000) {
    await page.waitForFunction(
        ({ idx, text }) => {
            const cells = document.querySelectorAll('.agent-repl-jupyterlab-notebook .jp-Cell');
            const cell = cells[idx];
            if (!cell) return false;
            const outputArea = cell.querySelector('.jp-OutputArea');
            if (!outputArea) return false;
            return outputArea.textContent?.includes(text) ?? false;
        },
        { idx: cellIndex, text: expectedSubstring },
        { timeout: timeoutMs },
    );
}

async function getCellCount(page) {
    return page.evaluate(() => {
        return document.querySelectorAll('.agent-repl-jupyterlab-notebook .jp-Cell').length;
    });
}

async function getCellSource(page, cellIndex) {
    return page.evaluate((idx) => {
        const cells = document.querySelectorAll('.agent-repl-jupyterlab-notebook .jp-Cell');
        const cell = cells[idx];
        if (!cell) return null;
        const editor = cell.querySelector('.cm-content');
        return editor?.textContent ?? null;
    }, cellIndex);
}

// ---------------------------------------------------------------------------
// Test lifecycle
// ---------------------------------------------------------------------------

const testNotebookDir = path.join(workspaceRoot, 'tmp', 'e2e-notebooks');

test.before(async () => {
    await fs.mkdir(screenshotDir, { recursive: true });
    await fs.mkdir(testNotebookDir, { recursive: true });

    previewPort = await findOpenPort();
    previewUrl = `http://127.0.0.1:${previewPort}/preview.html`;

    previewServer = spawn('node', ['./scripts/preview-webview.mjs'], {
        cwd: extensionRoot,
        env: { ...process.env, AGENT_REPL_PREVIEW_PORT: String(previewPort) },
        stdio: ['ignore', 'pipe', 'pipe'],
    });
    previewServerExitPromise = new Promise((resolve) => {
        previewServer.once('exit', () => resolve());
    });

    let startupLogs = '';
    previewServer.stdout.on('data', (chunk) => { startupLogs += chunk.toString(); });
    previewServer.stderr.on('data', (chunk) => { startupLogs += chunk.toString(); });
    previewServer.on('exit', (code) => {
        if (code !== 0 && code !== null) console.error(startupLogs);
    });

    await waitForPreviewReady(previewUrl);

    // Start workspace daemon
    await runAgentReplCli(['core', 'start', '--workspace-root', workspaceRoot]);

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
    await stopWorkspaceDaemon();

    // Clean up test notebooks
    await fs.rm(testNotebookDir, { recursive: true, force: true }).catch(() => {});
});

// ---------------------------------------------------------------------------
// Test 1: WebSocket live sync
// ---------------------------------------------------------------------------

test('WebSocket live sync — CLI execution output appears in browser without refresh', async () => {
    const notebookPath = path.join(testNotebookDir, 'ws-sync.ipynb');
    const relativePath = path.relative(workspaceRoot, notebookPath);
    await writeNotebook(notebookPath, ['print("ws-sync-marker-42")']);

    const handle = await openNotebookPage(relativePath);
    try {
        await screenshot(handle.page, '01-ws-sync-before-execute');

        // Execute cell 0 via CLI
        await runAgentReplCli([
            'exec', '--path', relativePath,
            '--cell-index', '0',
            '--workspace-root', workspaceRoot,
        ]);

        // Output should appear in browser via WebSocket push (no page refresh)
        await waitForOutputText(handle.page, 0, 'ws-sync-marker-42');
        await screenshot(handle.page, '02-ws-sync-after-execute');
    } finally {
        await closePageHandle(handle);
    }
});

// ---------------------------------------------------------------------------
// Test 2: Checkpoint round-trip
// ---------------------------------------------------------------------------

test('Checkpoint round-trip — create, edit, restore, verify browser shows restored state', async () => {
    const notebookPath = path.join(testNotebookDir, 'checkpoint.ipynb');
    const relativePath = path.relative(workspaceRoot, notebookPath);
    await writeNotebook(notebookPath, ['x = 1\nprint(x)']);

    // Execute to establish baseline outputs
    await runAgentReplCli([
        'exec', '--path', relativePath,
        '--cell-index', '0',
        '--workspace-root', workspaceRoot,
    ]);

    // Create checkpoint
    const createResult = await runAgentReplCli([
        'core', 'checkpoint-create',
        '--workspace-root', workspaceRoot,
        relativePath,
        '--label', 'before-edit',
    ]);
    const checkpointData = JSON.parse(createResult.stdout);
    assert.ok(checkpointData.checkpoint_id, 'checkpoint should have an id');
    const checkpointId = checkpointData.checkpoint_id;

    // Edit notebook via CLI (change cell source)
    await runAgentReplCli([
        'edit', '--path', relativePath,
        '--cell-index', '0',
        '--source', 'x = 999\nprint(x)',
        '--workspace-root', workspaceRoot,
    ]);

    // Open browser and verify edited state
    const handle = await openNotebookPage(relativePath);
    try {
        await screenshot(handle.page, '03-checkpoint-after-edit');
        const editedSource = await getCellSource(handle.page, 0);
        assert.ok(editedSource?.includes('999'), 'cell should show edited source');

        // Restore checkpoint
        await runAgentReplCli([
            'core', 'checkpoint-restore',
            '--workspace-root', workspaceRoot,
            relativePath,
            '--checkpoint-id', checkpointId,
        ]);

        // Wait for browser to reflect restored state via WebSocket
        await handle.page.waitForFunction(() => {
            const cells = document.querySelectorAll('.agent-repl-jupyterlab-notebook .jp-Cell');
            const cell = cells[0];
            if (!cell) return false;
            const editor = cell.querySelector('.cm-content');
            return editor?.textContent?.includes('x = 1') ?? false;
        }, { timeout: 15_000 });

        await screenshot(handle.page, '04-checkpoint-after-restore');
        const restoredSource = await getCellSource(handle.page, 0);
        assert.ok(restoredSource?.includes('x = 1'), 'cell should show restored source');
    } finally {
        await closePageHandle(handle);
    }
});

// ---------------------------------------------------------------------------
// Test 3: Execution via daemon (browser execute button)
// ---------------------------------------------------------------------------

test('Execution via daemon — browser execute triggers daemon execution and output appears', async () => {
    const notebookPath = path.join(testNotebookDir, 'browser-exec.ipynb');
    const relativePath = path.relative(workspaceRoot, notebookPath);
    await writeNotebook(notebookPath, ['print("browser-exec-marker-77")']);

    const handle = await openNotebookPage(relativePath);
    try {
        await screenshot(handle.page, '05-browser-exec-before');

        // Click the run button in the JupyterLab toolbar
        // The toolbar has a play/run button — find it and click
        const runButton = handle.page.locator('.agent-repl-jupyterlab-toolbar button[title*="Run"], .agent-repl-jupyterlab-toolbar button[aria-label*="Run"]').first();
        const runButtonExists = await runButton.count() > 0;

        if (runButtonExists) {
            await runButton.click();
        } else {
            // Fallback: use Shift+Enter keyboard shortcut
            const cell = handle.page.locator('.agent-repl-jupyterlab-notebook .jp-Cell').first();
            await cell.click();
            await handle.page.keyboard.press('Shift+Enter');
        }

        // Output should appear
        await waitForOutputText(handle.page, 0, 'browser-exec-marker-77');
        await screenshot(handle.page, '06-browser-exec-after');
    } finally {
        await closePageHandle(handle);
    }
});

// ---------------------------------------------------------------------------
// Test 4: Multi-notebook independence
// ---------------------------------------------------------------------------

test('Multi-notebook — execute in one notebook does not affect the other', async () => {
    const notebookA = path.join(testNotebookDir, 'multi-a.ipynb');
    const notebookB = path.join(testNotebookDir, 'multi-b.ipynb');
    const relA = path.relative(workspaceRoot, notebookA);
    const relB = path.relative(workspaceRoot, notebookB);

    await writeNotebook(notebookA, ['print("notebook-a-output")']);
    await writeNotebook(notebookB, ['print("notebook-b-output")']);

    const handleA = await openNotebookPage(relA);
    const handleB = await openNotebookPage(relB);
    try {
        await screenshot(handleA.page, '07-multi-a-before');
        await screenshot(handleB.page, '08-multi-b-before');

        // Execute only notebook A via CLI
        await runAgentReplCli([
            'exec', '--path', relA,
            '--cell-index', '0',
            '--workspace-root', workspaceRoot,
        ]);

        // A should show output
        await waitForOutputText(handleA.page, 0, 'notebook-a-output');
        await screenshot(handleA.page, '09-multi-a-after-exec');

        // B should still have no output
        const bHasOutput = await handleB.page.evaluate(() => {
            const cells = document.querySelectorAll('.agent-repl-jupyterlab-notebook .jp-Cell');
            const cell = cells[0];
            if (!cell) return false;
            const outputArea = cell.querySelector('.jp-OutputArea');
            return outputArea?.textContent?.includes('notebook-b-output') ?? false;
        });
        assert.equal(bHasOutput, false, 'notebook B should not have output from A execution');
        await screenshot(handleB.page, '10-multi-b-unaffected');

        // Now execute B
        await runAgentReplCli([
            'exec', '--path', relB,
            '--cell-index', '0',
            '--workspace-root', workspaceRoot,
        ]);
        await waitForOutputText(handleB.page, 0, 'notebook-b-output');
        await screenshot(handleB.page, '11-multi-b-after-exec');
    } finally {
        await closePageHandle(handleA);
        await closePageHandle(handleB);
    }
});

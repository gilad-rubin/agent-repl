import { execFile } from 'node:child_process';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import fs from 'node:fs';
import { dirname, extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { createStandaloneServices } from './standalone-server.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const extensionRoot = join(__dirname, '..');
const repoRoot = resolve(extensionRoot, '..');
const sourceRoot = join(extensionRoot, 'webview-src');
const host = '127.0.0.1';
const port = Number(process.env.AGENT_REPL_PREVIEW_PORT || 4173);
const initCwd = process.env.INIT_CWD ? resolve(process.env.INIT_CWD) : null;
const inferredWorkspaceRoot = initCwd && initCwd !== extensionRoot ? initCwd : repoRoot;
const workspaceRoot = resolve(process.env.AGENT_REPL_STANDALONE_WORKSPACE || inferredWorkspaceRoot);
const standaloneServices = createStandaloneServices({
  repoRoot,
  workspaceRoot,
  extensionRoot,
});
const standaloneApiRoutes = [
  '/api/standalone/health',
  '/api/standalone/attach',
  '/api/standalone/session-touch',
  '/api/standalone/session-end',
  '/api/standalone/kernels',
  '/api/standalone/workspace-tree',
  '/api/standalone/lsp/sync',
  '/api/standalone/notebook/contents',
  '/api/standalone/notebook/shared-model',
  '/api/standalone/notebook/edit',
  '/api/standalone/notebook/execute-cell',
  '/api/standalone/notebook/interrupt',
  '/api/standalone/notebook/execute-all',
  '/api/standalone/notebook/select-kernel',
  '/api/standalone/notebook/restart',
  '/api/standalone/notebook/restart-and-run-all',
  '/api/standalone/notebook/runtime',
  '/api/standalone/notebook/status',
  '/api/standalone/notebook/trust',
  '/api/standalone/notebook/activity',
  '/api/standalone/notebook/execute-cell-async',
  '/api/standalone/notebook/execute-all-async',
  '/api/standalone/notebook/restart-and-run-all-async',
];
const standaloneProtocolVersion = 'standalone-preview-v1';

const contentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.woff2', 'font/woff2'],
]);

let buildInFlight = false;
let buildQueued = false;

function run(command, args, cwd) {
  return new Promise((resolvePromise, rejectPromise) => {
    execFile(command, args, { cwd }, (error) => {
      if (error) {
        rejectPromise(error);
        return;
      }
      resolvePromise(undefined);
    });
  });
}

async function buildWebview() {
  if (buildInFlight) {
    buildQueued = true;
    return;
  }

  buildInFlight = true;
  try {
    console.log('[preview] rebuilding canvas bundle...');
    await run('node', ['./scripts/build-webview.mjs'], extensionRoot);
    console.log('[preview] bundle ready');
  } catch (error) {
    console.error('[preview] build failed:', error);
  } finally {
    buildInFlight = false;
    if (buildQueued) {
      buildQueued = false;
      void buildWebview();
    }
  }
}

function scheduleBuild() {
  void buildWebview();
}

function safePathname(urlPath) {
  const decoded = decodeURIComponent(urlPath.split('?')[0] || '/');
  const requested = decoded === '/' ? '/preview.html' : decoded;
  const resolvedPath = resolve(extensionRoot, `.${requested}`);
  if (!resolvedPath.startsWith(extensionRoot)) {
    return undefined;
  }
  return resolvedPath;
}

const server = createServer(async (request, response) => {
  if ((request.url || '').startsWith('/favicon.ico')) {
    response.writeHead(204, {
      'Cache-Control': 'no-store',
    });
    response.end();
    return;
  }

  if ((request.url || '').startsWith('/api/standalone/health')) {
    response.writeHead(200, {
      'Content-Type': 'application/json; charset=utf-8',
      'Cache-Control': 'no-store',
    });
    response.end(JSON.stringify({
      status: 'ok',
      protocol_version: standaloneProtocolVersion,
      workspace_root: workspaceRoot,
      extension_root: extensionRoot,
      pid: process.pid,
      api_routes: standaloneApiRoutes,
    }));
    return;
  }

  if ((request.url || '').startsWith('/api/standalone/')) {
    const handled = await standaloneServices.handleApiRequest(request, response);
    if (handled) {
      return;
    }
  }

  const filePath = safePathname(request.url || '/');
  if (!filePath) {
    response.writeHead(403);
    response.end('Forbidden');
    return;
  }

  try {
    const stat = await fs.promises.stat(filePath);
    const targetFile = stat.isDirectory() ? join(filePath, 'index.html') : filePath;
    const data = await readFile(targetFile);
    const contentType = contentTypes.get(extname(targetFile)) || 'application/octet-stream';
    response.writeHead(200, {
      'Content-Type': contentType,
      'Cache-Control': 'no-store',
    });
    response.end(data);
  } catch {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end(`Not found: ${normalize(filePath)}`);
  }
});

await buildWebview();

try {
  fs.watch(sourceRoot, { recursive: true }, (_eventType, filename) => {
    if (!filename || filename.startsWith('.')) {
      return;
    }
    scheduleBuild();
  });
  console.log(`[preview] watching ${sourceRoot}`);
} catch (error) {
  console.warn('[preview] recursive watch unavailable; rebuild manually with npm run build:webview');
  console.warn(error);
}

server.listen(port, host, () => {
  console.log(`[preview] workspace root ${workspaceRoot}`);
  console.log(`[preview] open http://${host}:${port}/preview.html`);
  console.log(`[preview] standalone example http://${host}:${port}/preview.html?path=README.ipynb`);
  console.log(`[preview] mock example http://${host}:${port}/preview.html?mock=1`);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    void standaloneServices.dispose().finally(() => {
      server.close(() => process.exit(0));
    });
  });
}

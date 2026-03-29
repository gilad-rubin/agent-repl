const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadWebviewModule() {
    const modulePath = path.resolve(__dirname, '../out/editor/webview.js');
    const originalLoad = Module._load;

    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                Uri: {
                    joinPath(base, ...segments) {
                        const basePath = base.path ?? base.fsPath ?? '';
                        const joined = path.posix.join(basePath, ...segments);
                        return {
                            path: joined,
                            fsPath: joined,
                            toString() {
                                return joined;
                            },
                        };
                    },
                },
            };
        }
        return originalLoad.call(this, request, parent, isMain);
    };

    delete require.cache[modulePath];
    try {
        return require(modulePath);
    } finally {
        Module._load = originalLoad;
    }
}

test('derivePreviewAssetUrls maps the preview page to shared media assets', () => {
    const { derivePreviewAssetUrls } = loadWebviewModule();

    assert.deepEqual(
        derivePreviewAssetUrls('http://127.0.0.1:4173/preview.html?path=demo.ipynb'),
        {
            origin: 'http://127.0.0.1:4173',
            canvasCss: 'http://127.0.0.1:4173/media/canvas.css',
            canvasJs: 'http://127.0.0.1:4173/media/canvas.js',
        },
    );
});

test('derivePreviewAssetUrls rejects non-loopback origins', () => {
    const { derivePreviewAssetUrls } = loadWebviewModule();

    assert.equal(derivePreviewAssetUrls('https://example.com/preview.html'), null);
});

test('buildWebviewHtml bootstraps preview assets first and keeps local fallbacks', () => {
    const { buildWebviewHtml } = loadWebviewModule();
    const html = buildWebviewHtml(
        {
            cspSource: 'vscode-webview:',
            asWebviewUri(uri) {
                return {
                    toString() {
                        return `vscode-resource:${uri.path}`;
                    },
                };
            },
        },
        { path: '/extension' },
        { browserCanvasUrl: 'http://localhost:4173/preview.html' },
    );

    assert.match(html, /http:\/\/localhost:4173\/media\/canvas\.css/);
    assert.match(html, /http:\/\/localhost:4173\/media\/canvas\.js/);
    assert.match(html, /vscode-resource:\/extension\/media\/canvas\.css/);
    assert.match(html, /vscode-resource:\/extension\/media\/canvas\.js/);
    assert.match(html, /script-src 'nonce-[^']+' vscode-webview: http:\/\/localhost:4173/);
    assert.match(html, /style-src 'nonce-[^']+' vscode-webview: http:\/\/localhost:4173/);
});

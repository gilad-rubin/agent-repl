import * as crypto from 'crypto';
import * as vscode from 'vscode';

type PreviewAssetUrls = {
  origin: string;
  canvasCss: string;
  canvasJs: string;
};

function isLoopbackHost(hostname: string): boolean {
  return hostname === '127.0.0.1' || hostname === 'localhost' || hostname === '::1' || hostname === '[::1]';
}

export function derivePreviewAssetUrls(browserCanvasUrl?: string): PreviewAssetUrls | null {
  if (!browserCanvasUrl?.trim()) {
    return null;
  }

  try {
    const previewUrl = new URL(browserCanvasUrl);
    if (!['http:', 'https:'].includes(previewUrl.protocol) || !isLoopbackHost(previewUrl.hostname)) {
      return null;
    }
    const baseUrl = new URL('./', previewUrl);
    return {
      origin: previewUrl.origin,
      canvasCss: new URL('media/canvas.css', baseUrl).toString(),
      canvasJs: new URL('media/canvas.js', baseUrl).toString(),
    };
  } catch {
    return null;
  }
}

export function buildWebviewHtml(
  webview: vscode.Webview,
  extensionUri: vscode.Uri,
  options?: {
    browserCanvasUrl?: string;
  },
): string {
  const nonce = crypto.randomBytes(16).toString('hex');
  const mediaUri = vscode.Uri.joinPath(extensionUri, 'media');

  const fontMonoRegular = webview.asWebviewUri(
    vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexMono-Regular.woff2'),
  );
  const fontMonoBold = webview.asWebviewUri(
    vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexMono-Bold.woff2'),
  );
  const fontSansRegular = webview.asWebviewUri(
    vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexSans-Regular.woff2'),
  );
  const fontSansSemiBold = webview.asWebviewUri(
    vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexSans-SemiBold.woff2'),
  );
  const canvasCss = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'canvas.css'));
  const canvasJs = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'canvas.js'));
  const previewAssets = derivePreviewAssetUrls(options?.browserCanvasUrl);
  const localAssets = {
    canvasCss: canvasCss.toString(),
    canvasJs: canvasJs.toString(),
  };
  const previewOrigin = previewAssets ? ` ${previewAssets.origin}` : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none';
    font-src ${webview.cspSource};
    style-src 'nonce-${nonce}' ${webview.cspSource}${previewOrigin};
    script-src 'nonce-${nonce}' ${webview.cspSource}${previewOrigin};
    img-src ${webview.cspSource} data:;">
<style nonce="${nonce}">
@font-face { font-family: 'IBM Plex Mono'; font-weight: 400; src: url('${fontMonoRegular}') format('woff2'); }
@font-face { font-family: 'IBM Plex Mono'; font-weight: 700; src: url('${fontMonoBold}') format('woff2'); }
@font-face { font-family: 'IBM Plex Sans'; font-weight: 400; src: url('${fontSansRegular}') format('woff2'); }
@font-face { font-family: 'IBM Plex Sans'; font-weight: 600; src: url('${fontSansSemiBold}') format('woff2'); }
</style>
<link id="agent-repl-canvas-css" rel="stylesheet" href="${canvasCss}">
</head>
<body data-nonce="${nonce}">
  <div id="root"></div>
  <script nonce="${nonce}">
  (() => {
    const localAssets = ${JSON.stringify(localAssets)};
    const previewAssets = ${JSON.stringify(previewAssets)};
    const cssLink = document.getElementById('agent-repl-canvas-css');
    const applyCss = (href) => {
      if (cssLink instanceof HTMLLinkElement) {
        cssLink.href = href;
      }
    };
    const loadScript = (src, fallbackSrc) => {
      const script = document.createElement('script');
      script.setAttribute('nonce', '${nonce}');
      script.src = src;
      script.addEventListener('error', () => {
        script.remove();
        if (fallbackSrc && fallbackSrc !== src) {
          loadScript(fallbackSrc);
        }
      }, { once: true });
      document.body.appendChild(script);
    };

    if (previewAssets) {
      if (cssLink instanceof HTMLLinkElement) {
        cssLink.addEventListener('error', () => applyCss(localAssets.canvasCss), { once: true });
      }
      applyCss(previewAssets.canvasCss);
      loadScript(previewAssets.canvasJs, localAssets.canvasJs);
      return;
    }

    loadScript(localAssets.canvasJs);
  })();
  </script>
</body>
</html>`;
}

import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import * as esbuild from 'esbuild';

const __dirname = dirname(fileURLToPath(import.meta.url));
const extensionRoot = join(__dirname, '..');
const sourceRoot = join(extensionRoot, 'webview-src');
const mediaRoot = join(extensionRoot, 'media');
const mediaFontsRoot = join(mediaRoot, 'fonts');

function syncWebviewFonts() {
  mkdirSync(mediaFontsRoot, { recursive: true });

  const fontSources = [
    [
      join(extensionRoot, 'node_modules', '@ibm', 'plex-mono', 'fonts', 'complete', 'woff2', 'IBMPlexMono-Regular.woff2'),
      join(mediaFontsRoot, 'IBMPlexMono-Regular.woff2'),
    ],
    [
      join(extensionRoot, 'node_modules', '@ibm', 'plex-mono', 'fonts', 'complete', 'woff2', 'IBMPlexMono-Bold.woff2'),
      join(mediaFontsRoot, 'IBMPlexMono-Bold.woff2'),
    ],
    [
      join(extensionRoot, 'node_modules', '@ibm', 'plex-sans', 'fonts', 'complete', 'woff2', 'IBMPlexSans-Regular.woff2'),
      join(mediaFontsRoot, 'IBMPlexSans-Regular.woff2'),
    ],
    [
      join(extensionRoot, 'node_modules', '@ibm', 'plex-sans', 'fonts', 'complete', 'woff2', 'IBMPlexSans-SemiBold.woff2'),
      join(mediaFontsRoot, 'IBMPlexSans-SemiBold.woff2'),
    ],
  ];

  for (const [source, target] of fontSources) {
    cpSync(source, target);
  }
}

syncWebviewFonts();

execFileSync('npx', ['tsc', '-p', join(sourceRoot, 'tsconfig.json'), '--noEmit'], {
  cwd: extensionRoot,
  stdio: 'inherit',
});

// Strip @font-face blocks from Carbon's CSS — we self-host the fonts via
// inline declarations in webview.ts, and the CDN URLs would be blocked by CSP.
const stripCarbonFonts = {
  name: 'strip-carbon-fonts',
  setup(build) {
    build.onLoad({ filter: /styles\.css$/, namespace: 'file' }, async (args) => {
      if (!args.path.includes('@carbon')) return undefined;
      const { readFile } = await import('node:fs/promises');
      const css = await readFile(args.path, 'utf8');
      const stripped = css.replace(/@font-face\s*\{[^}]*\}/g, '');
      return { contents: stripped, loader: 'css' };
    });
  },
};

await esbuild.build({
  entryPoints: [join(sourceRoot, 'main.tsx')],
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: 'es2020',
  jsx: 'automatic',
  outfile: join(mediaRoot, 'canvas.js'),
  minify: true,
  logLevel: 'info',
  plugins: [stripCarbonFonts],
});
// esbuild extracts CSS imports (Carbon + styles.css) into canvas.css automatically

# Browser Verification Guide

Use this guide when you need to test, troubleshoot, or QA the browser canvas properly, especially for execution state, queue behavior, and streaming output.

## What This Covers

- Fast manual QA in the browser preview
- Repro steps for streaming output and run-all behavior
- How to avoid stale-build and stale-runtime false alarms
- How to script browser checks with Playwright when you need stronger verification

## When To Use Browser Preview

Use `cd extension && npm run preview:webview` when the change is primarily in:

- `extension/webview-src/`
- shared canvas reducers/helpers under `extension/src/shared/`
- standalone browser host behavior
- renderer-visible execution state, output rendering, and notebook chrome

Do not treat browser preview as sufficient for:

- VS Code custom-editor lifecycle
- extension-host activation or `extension.ts`
- installed-extension reload behavior
- notebook focus/selection behavior that depends on VS Code shell integration

For those, verify in VS Code too.

## Start Clean

Before debugging browser behavior, make sure the preview is testing the build you think it is.

1. Rebuild the canvas:

```bash
cd extension && npm run compile
```

2. Make sure the shared runtime is fresh for this workspace:

```bash
uv run agent-repl core status --workspace-root /Users/giladrubin/python_workspace/agent-repl --pretty
```

3. If the daemon is stale or missing, restart it:

```bash
uv run agent-repl core stop --workspace-root /Users/giladrubin/python_workspace/agent-repl
uv run agent-repl core start --workspace-root /Users/giladrubin/python_workspace/agent-repl --pretty
```

4. Start preview on a known port:

```bash
cd extension && AGENT_REPL_PREVIEW_PORT=4176 npm run preview:webview
```

5. If the port is already taken, inspect listeners before assuming the new server started:

```bash
lsof -nP -iTCP -sTCP:LISTEN | rg '417[0-9]'
```

## Use Fresh Notebooks For QA

Prefer a fresh notebook for each verification pass. Reusing an old notebook can blur the difference between persisted outputs and a new live run.

Example streaming repro notebook:

```bash
uv run agent-repl new tmp-browser-streaming-verify.ipynb --cells-json '[{"type":"code","source":"import time\nfor i in range(6):\n    print(f\"tick {i + 1}/6\", flush=True)\n    time.sleep(1)\nprint(\"done\", flush=True)"}]'
```

Example restart-and-run-all repro notebook:

```bash
uv run agent-repl new tmp-browser-runall-verify.ipynb --cells-json '[{"type":"code","source":"import time\nfor i in range(3):\n    print(f\"tick {i + 1}/3\", flush=True)\n    time.sleep(1)\nprint(\"done\", flush=True)"},{"type":"code","source":"print(\"second\")"},{"type":"code","source":"print(\"third\")"}]'
```

## Manual QA Checklist

Open preview:

```text
http://127.0.0.1:4176/preview.html?path=tmp-browser-streaming-verify.ipynb
```

For a single long-running cell, verify:

- the cell shows `Running` before it shows `Completed`
- streamed lines appear one by one while the cell is still running
- `done` appears only at the end
- the status timer advances while the cell runs
- `Cmd+Enter`/`Ctrl+Enter` submits the run and advances immediately to the next cell without waiting for completion
- rerunning a previously completed cell clears `Completed` immediately instead of lingering until `Running` arrives
- after reselecting a running cell in command mode, notebook commands like `dd` still work instead of waiting for execution to finish

For `Run All` or `Restart and Run All`, verify:

- the first code cell becomes `Running`
- later code cells stay `Queued` until their turn
- cells do not all sit in `Queued` until the entire batch finishes
- completed cells flip individually as the batch advances

## What “Intermediate Output” Should Look Like

For the sample cell:

```python
import time
for i in range(6):
    print(f"tick {i + 1}/6", flush=True)
    time.sleep(1)
print("done", flush=True)
```

Expected browser behavior:

- after about 1 second: `tick 1/6`
- after about 2 seconds: `tick 1/6`, `tick 2/6`
- after about 3 seconds: `tick 1/6`, `tick 2/6`, `tick 3/6`
- and so on until `done`

If all output appears only once the cell completes, the browser path is still batching or the activity path is blocked.

## Scripted Browser Verification

For repeatable browser checks, run Playwright from `extension/`, where the dependency is installed.

Important:

- use `waitUntil: 'domcontentloaded'`, not `networkidle`
- the preview page polls continuously, so `networkidle` can hang or mislead
- wait for explicit selectors such as `[data-cell-id]`

Minimal pattern:

```js
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const url = 'http://127.0.0.1:4176/preview.html?path='
    + encodeURIComponent('tmp-browser-streaming-verify.ipynb')
    + '&v=' + Date.now();

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForSelector('[data-cell-id]', { timeout: 30000 });
  await page.getByTitle('Run cell (Shift+Enter)').click();

  for (let i = 0; i < 8; i++) {
    await page.waitForTimeout(1000);
    const sample = await page.evaluate(() => ({
      status: document.querySelector('[data-cell-status]')?.textContent?.trim() ?? '',
      outputs: Array.from(document.querySelectorAll('[data-output-text-block]')).map((node) => node.textContent?.trim() ?? ''),
    }));
    console.log(sample);
  }

  await browser.close();
})();
```

Run it from:

```bash
cd extension && node ./tmp/your-script.cjs
```

## Common False Alarms

### Preview build mismatch

Symptom:

- code looks correct in the repo, but preview behaves like an older build

Checks:

- rerun `cd extension && npm run compile`
- make sure you are hitting the expected preview port
- add a cache-busting query param such as `&v=$(date +%s)`

### Installed extension drift

Symptom:

- browser preview works, but VS Code still behaves like old code

Checks:

- `npm run compile` updates the repo build only
- sync the full `extension/out/` and `extension/media/` trees into the installed extension if you are testing the installed copy
- after syncing installed `out/`, reload the VS Code window once

### Wrong server on the same port

Symptom:

- preview loads, but it is not the preview server you just started

Checks:

- inspect listeners with `lsof -nP -iTCP -sTCP:LISTEN | rg '417[0-9]'`
- probe `http://127.0.0.1:<port>/api/standalone/health` to confirm the preview workspace root and API contract
- if needed, pick a new explicit preview port

Recovery:

- `uv run agent-repl browse` now prefers a healthy preview server and will fall back to a fresh port if the existing one is stale or incompatible
- when the browser banner reports a stale preview server, restart `npm run preview:webview` for the workspace and then refresh the page

### Stale notebook contents

Symptom:

- the browser shows a notebook, but the run you are observing looks too fast or already completed

Checks:

- use a fresh notebook for the repro
- confirm the source shown in the browser matches the intended repro cell

### Runtime not actually fresh

Symptom:

- execution state or outputs do not match the source you just changed

Checks:

- restart the workspace daemon
- if testing `Restart and Run All`, confirm the browser is talking to the current daemon instance

## Troubleshooting Order

When browser behavior looks wrong, check these in order:

1. Is the repro notebook fresh?
2. Did `npm run compile` succeed?
3. Are you hitting the preview port you intended?
4. Is the daemon fresh for this workspace?
5. Is the browser showing the source you expect?
6. Are streamed outputs appearing incrementally in the DOM?
7. If preview is correct but VS Code is wrong, is the installed extension stale?

## Related Docs

- [dev/extension-guide.md](/Users/giladrubin/python_workspace/agent-repl/dev/extension-guide.md)
- [dev/current-architecture.md](/Users/giladrubin/python_workspace/agent-repl/dev/current-architecture.md)
- [AGENTS.md](/Users/giladrubin/python_workspace/agent-repl/AGENTS.md)

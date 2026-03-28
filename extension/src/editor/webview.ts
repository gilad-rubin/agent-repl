import * as vscode from 'vscode';
import * as crypto from 'crypto';

export function buildWebviewHtml(webview: vscode.Webview, extensionUri: vscode.Uri): string {
    const nonce = crypto.randomBytes(16).toString('hex');
    const mediaUri = vscode.Uri.joinPath(extensionUri, 'media');

    const fontMonoRegular = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexMono-Regular.woff2'));
    const fontMonoBold = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexMono-Bold.woff2'));
    const fontSansRegular = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexSans-Regular.woff2'));
    const fontSansSemiBold = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'fonts', 'IBMPlexSans-SemiBold.woff2'));
    const markedJs = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'marked.min.js'));
    const purifyJs = webview.asWebviewUri(vscode.Uri.joinPath(mediaUri, 'purify.min.js'));

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy"
  content="default-src 'none';
    font-src ${webview.cspSource};
    style-src 'nonce-${nonce}';
    script-src 'nonce-${nonce}' ${webview.cspSource};
    img-src ${webview.cspSource} data:;">
<style nonce="${nonce}">
@font-face { font-family: 'IBM Plex Mono'; font-weight: 400; src: url('${fontMonoRegular}') format('woff2'); }
@font-face { font-family: 'IBM Plex Mono'; font-weight: 700; src: url('${fontMonoBold}') format('woff2'); }
@font-face { font-family: 'IBM Plex Sans'; font-weight: 400; src: url('${fontSansRegular}') format('woff2'); }
@font-face { font-family: 'IBM Plex Sans'; font-weight: 600; src: url('${fontSansSemiBold}') format('woff2'); }

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: 'IBM Plex Sans', var(--vscode-font-family), sans-serif;
    font-size: 14px;
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
    padding: 0;
    overflow-y: auto;
}

/* --- Toolbar --- */
.toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--vscode-widget-border, rgba(128,128,128,0.2));
    background: var(--vscode-editorGroupHeader-tabsBackground, var(--vscode-editor-background));
    position: sticky;
    top: 0;
    z-index: 100;
}
.toolbar select, .toolbar button {
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 12px;
    color: var(--vscode-foreground);
    background: var(--vscode-input-background);
    border: 1px solid var(--vscode-input-border, rgba(128,128,128,0.3));
    border-radius: 3px;
    padding: 3px 8px;
    cursor: pointer;
}
.toolbar button:hover { background: var(--vscode-toolbar-hoverBackground, rgba(128,128,128,0.15)); }
.toolbar .spacer { flex: 1; }
.toolbar .kernel-status {
    font-size: 11px;
    color: var(--vscode-descriptionForeground);
}
.toolbar .kernel-status.busy { color: var(--vscode-charts-orange, #e8a317); }

/* --- Notebook container --- */
#notebook { padding: 16px; padding-bottom: 200px; }

/* --- Cell --- */
.cell {
    margin-bottom: 4px;
    border: 1px solid var(--vscode-widget-border, rgba(128,128,128,0.2));
    border-radius: 4px;
    position: relative;
    transition: border-color 0.1s, box-shadow 0.1s;
    padding-bottom: 4px;
}
.cell.selected {
    border-color: var(--vscode-focusBorder, #007acc);
    box-shadow: inset 3px 0 0 var(--vscode-focusBorder, #007acc);
}
.cell.executing {
    border-color: var(--vscode-charts-orange, #e8a317);
    box-shadow: inset 3px 0 0 var(--vscode-charts-orange, #e8a317);
}

/* --- Cell gutter --- */
.cell-gutter {
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 48px;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: 10px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--vscode-descriptionForeground);
    user-select: none;
}
.cell-gutter .exec-indicator {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--vscode-charts-orange, #e8a317);
    animation: pulse 1s infinite;
    margin-right: 4px;
}
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

/* --- Code cell --- */
.cell-source {
    margin-left: 48px;
    padding: 8px 12px;
    background: var(--vscode-input-background, rgba(0,0,0,0.1));
    border-radius: 3px;
}
.cell-source textarea {
    display: block;
    width: 100%;
    min-height: 40px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    line-height: 1.5;
    color: var(--vscode-editor-foreground);
    background: transparent;
    border: none;
    outline: none;
    resize: vertical;
    tab-size: 4;
    padding: 0;
}

/* --- Markdown cell --- */
.cell-markdown {
    margin-left: 48px;
    padding: 8px 12px;
    cursor: pointer;
}
.cell-markdown.rendered { line-height: 1.6; }
.cell-markdown.rendered h1 { font-size: 1.6em; font-weight: 600; margin: 0.4em 0; }
.cell-markdown.rendered h2 { font-size: 1.3em; font-weight: 600; margin: 0.3em 0; }
.cell-markdown.rendered h3 { font-size: 1.1em; font-weight: 600; margin: 0.2em 0; }
.cell-markdown.rendered code {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.9em;
    background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.1));
    padding: 1px 4px;
    border-radius: 3px;
}
.cell-markdown.rendered pre {
    background: var(--vscode-textCodeBlock-background, rgba(0,0,0,0.1));
    padding: 8px 12px;
    border-radius: 3px;
    overflow-x: auto;
    margin: 8px 0;
}
.cell-markdown.rendered pre code { background: none; padding: 0; }
.cell-markdown.rendered a { color: var(--vscode-textLink-foreground, #3794ff); }

/* --- Raw cell --- */
.cell-raw {
    margin-left: 48px;
    padding: 8px 12px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    color: var(--vscode-descriptionForeground);
    background: var(--vscode-input-background, rgba(0,0,0,0.05));
    border-radius: 3px;
    white-space: pre-wrap;
}
.cell-raw::before {
    content: 'raw';
    display: inline-block;
    font-size: 10px;
    background: var(--vscode-badge-background, #444);
    color: var(--vscode-badge-foreground, #fff);
    padding: 1px 4px;
    border-radius: 2px;
    margin-bottom: 4px;
}

/* --- Output area --- */
.cell-outputs {
    margin-left: 48px;
    padding: 4px 12px;
}
.cell-output {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 2px 0;
}
.cell-output.stderr { color: var(--vscode-errorForeground, #f44747); }
.cell-output.error {
    color: var(--vscode-errorForeground, #f44747);
    background: var(--vscode-inputValidation-errorBackground, rgba(255,0,0,0.05));
    padding: 4px 8px;
    border-radius: 3px;
}

/* --- Presence indicators --- */
.cell-presence {
    position: absolute;
    right: 8px;
    top: 4px;
    font-size: 10px;
    color: var(--vscode-descriptionForeground);
    background: var(--vscode-badge-background, #444);
    color: var(--vscode-badge-foreground, #fff);
    padding: 1px 6px;
    border-radius: 8px;
}

/* --- Loading --- */
.loading {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 200px;
    color: var(--vscode-descriptionForeground);
    font-size: 14px;
}
</style>
</head>
<body>

<div class="toolbar">
    <select id="kernel-select" title="Select kernel"><option value="">No kernel</option></select>
    <button id="btn-restart" title="Restart kernel">Restart</button>
    <button id="btn-run-all" title="Run all cells">Run All</button>
    <button id="btn-restart-run" title="Restart kernel and run all cells">Restart &amp; Run</button>
    <div class="spacer"></div>
    <span id="kernel-status" class="kernel-status">idle</span>
    <span style="font-size:11px; color: var(--vscode-descriptionForeground); margin-left: 8px;">Canvas Editor v0.1</span>
</div>

<div id="notebook">
    <div class="loading" id="loading-indicator">Loading notebook...</div>
</div>

<script nonce="${nonce}" src="${markedJs}"></script>
<script nonce="${nonce}" src="${purifyJs}"></script>
<script nonce="${nonce}">
(function() {
    const vscode = acquireVsCodeApi();
    let reqCounter = 0;
    function nextReqId() { return 'req-' + (++reqCounter); }

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------
    const state = {
        cells: [],
        focusedIndex: 0,
        selectedIndices: new Set([0]),
        mode: 'command', // 'command' | 'edit'
        path: '',
        executing: new Set(), // cell_ids currently executing
        activityCursor: 0,
        pollTimer: null,
        pollInterval: 1000,
    };

    // -----------------------------------------------------------------------
    // Messaging helpers
    // -----------------------------------------------------------------------
    function send(msg) { vscode.postMessage(msg); }

    function sendRequest(msg) {
        msg.requestId = msg.requestId || nextReqId();
        msg.path = state.path;
        send(msg);
        return msg.requestId;
    }

    // -----------------------------------------------------------------------
    // Rendering
    // -----------------------------------------------------------------------
    const notebook = document.getElementById('notebook');
    const loadingIndicator = document.getElementById('loading-indicator');

    function renderAllCells() {
        loadingIndicator.style.display = 'none';
        // Clear existing cells
        const existing = notebook.querySelectorAll('.cell');
        existing.forEach(el => el.remove());

        state.cells.forEach((cell, i) => {
            notebook.appendChild(renderCell(cell, i));
        });
        updateSelection();
    }

    function renderCell(cell, index) {
        const div = document.createElement('div');
        div.className = 'cell';
        div.dataset.cellId = cell.cell_id;
        div.dataset.index = String(index);

        // Gutter
        const gutter = document.createElement('div');
        gutter.className = 'cell-gutter';
        if (state.executing.has(cell.cell_id)) {
            gutter.innerHTML = '<span class="exec-indicator"></span>';
        } else if (cell.cell_type === 'code' && cell.execution_count != null) {
            gutter.textContent = '[' + cell.execution_count + ']';
        } else if (cell.cell_type === 'code') {
            gutter.textContent = '[ ]';
        }
        div.appendChild(gutter);

        // Cell body
        if (cell.cell_type === 'code') {
            const src = document.createElement('div');
            src.className = 'cell-source';
            const ta = document.createElement('textarea');
            ta.value = cell.source;
            ta.spellcheck = false;
            ta.rows = Math.max(1, cell.source.split('\\n').length);
            ta.addEventListener('focus', () => onCellFocus(index));
            ta.addEventListener('blur', () => onCellBlur(index, ta.value));
            ta.addEventListener('input', () => autoResize(ta));
            ta.addEventListener('keydown', handleEditKeydown);
            src.appendChild(ta);
            div.appendChild(src);
        } else if (cell.cell_type === 'markdown') {
            const md = document.createElement('div');
            md.className = 'cell-markdown rendered';
            md.innerHTML = renderMarkdown(cell.source);
            md.addEventListener('dblclick', () => enterMarkdownEdit(index));
            div.appendChild(md);
        } else {
            // raw cell
            const raw = document.createElement('div');
            raw.className = 'cell-raw';
            raw.textContent = cell.source;
            div.appendChild(raw);
        }

        // Outputs
        if (cell.outputs && cell.outputs.length > 0) {
            const outputsDiv = document.createElement('div');
            outputsDiv.className = 'cell-outputs';
            cell.outputs.forEach(out => {
                outputsDiv.appendChild(renderOutput(out));
            });
            div.appendChild(outputsDiv);
        }

        div.addEventListener('click', (e) => {
            if (e.target.tagName !== 'TEXTAREA') {
                focusCell(index);
            }
        });

        return div;
    }

    function renderOutput(out) {
        const el = document.createElement('div');
        el.className = 'cell-output';
        if (out.output_type === 'stream') {
            if (out.name === 'stderr') el.className += ' stderr';
            el.textContent = out.text || '';
        } else if (out.output_type === 'error') {
            el.className += ' error';
            const tb = (out.traceback || []).join('\\n');
            el.textContent = tb || (out.ename + ': ' + out.evalue);
        } else if (out.output_type === 'execute_result' || out.output_type === 'display_data') {
            const data = out.data || {};
            if (data['text/plain']) {
                el.textContent = data['text/plain'];
            } else if (data['text/html']) {
                el.innerHTML = DOMPurify.sanitize(data['text/html']);
            }
        } else {
            el.textContent = JSON.stringify(out);
        }
        return el;
    }

    function renderMarkdown(source) {
        if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
            return DOMPurify.sanitize(marked.parse(source || ''));
        }
        return escapeHtml(source || '');
    }

    function escapeHtml(s) {
        return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function autoResize(ta) {
        ta.rows = Math.max(1, ta.value.split('\\n').length);
    }

    // -----------------------------------------------------------------------
    // Selection & focus
    // -----------------------------------------------------------------------
    function focusCell(index) {
        if (index < 0 || index >= state.cells.length) return;
        state.focusedIndex = index;
        state.selectedIndices = new Set([index]);
        state.mode = 'command';
        updateSelection();
    }

    function updateSelection() {
        document.querySelectorAll('.cell').forEach((el, i) => {
            el.classList.toggle('selected', state.selectedIndices.has(i));
            el.classList.toggle('executing', state.executing.has(el.dataset.cellId));
        });
    }

    function scrollToCell(index) {
        const cells = document.querySelectorAll('.cell');
        if (cells[index]) cells[index].scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    // -----------------------------------------------------------------------
    // Edit mode transitions
    // -----------------------------------------------------------------------
    function enterEditMode() {
        state.mode = 'edit';
        const cell = document.querySelectorAll('.cell')[state.focusedIndex];
        if (!cell) return;
        const ta = cell.querySelector('textarea');
        if (ta) { ta.focus(); return; }
        // Markdown: switch to edit
        const md = cell.querySelector('.cell-markdown');
        if (md && state.cells[state.focusedIndex].cell_type === 'markdown') {
            enterMarkdownEdit(state.focusedIndex);
        }
    }

    function enterMarkdownEdit(index) {
        state.mode = 'edit';
        state.focusedIndex = index;
        const cell = state.cells[index];
        const el = document.querySelectorAll('.cell')[index];
        const md = el.querySelector('.cell-markdown');
        if (!md) return;
        md.className = 'cell-source';
        md.innerHTML = '';
        const ta = document.createElement('textarea');
        ta.value = cell.source;
        ta.rows = Math.max(1, cell.source.split('\\n').length);
        ta.spellcheck = false;
        ta.addEventListener('blur', () => {
            const newSource = ta.value;
            cell.source = newSource;
            md.className = 'cell-markdown rendered';
            md.innerHTML = renderMarkdown(newSource);
            md.addEventListener('dblclick', () => enterMarkdownEdit(index));
            flushDraft(cell.cell_id, newSource);
            state.mode = 'command';
        });
        ta.addEventListener('input', () => autoResize(ta));
        ta.addEventListener('keydown', handleEditKeydown);
        md.appendChild(ta);
        ta.focus();
    }

    function onCellFocus(index) {
        state.mode = 'edit';
        state.focusedIndex = index;
        state.selectedIndices = new Set([index]);
        updateSelection();
    }

    function onCellBlur(index, newSource) {
        const cell = state.cells[index];
        if (cell && cell.source !== newSource) {
            cell.source = newSource;
            flushDraft(cell.cell_id, newSource);
        }
        state.mode = 'command';
    }

    function flushDraft(cellId, source) {
        sendRequest({ type: 'flush-draft', cell_id: cellId, source: source });
    }

    function flushActiveDraft() {
        const focused = state.cells[state.focusedIndex];
        if (!focused) return;
        const el = document.querySelectorAll('.cell')[state.focusedIndex];
        if (!el) return;
        const ta = el.querySelector('textarea');
        if (ta && focused.source !== ta.value) {
            focused.source = ta.value;
            flushDraft(focused.cell_id, ta.value);
        }
    }

    // -----------------------------------------------------------------------
    // Keyboard handling (edit mode)
    // -----------------------------------------------------------------------
    function handleEditKeydown(e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            e.target.blur();
            state.mode = 'command';
            return;
        }
        if (e.key === 'Enter' && e.shiftKey) {
            e.preventDefault();
            e.target.blur();
            runCellAndAdvance();
            return;
        }
        if (e.key === 'Tab') {
            e.preventDefault();
            const ta = e.target;
            const start = ta.selectionStart;
            const end = ta.selectionEnd;
            if (e.shiftKey) {
                // Dedent: remove up to 4 leading spaces on the current line
                const before = ta.value.substring(0, start);
                const lineStart = before.lastIndexOf('\\n') + 1;
                const line = ta.value.substring(lineStart);
                const match = line.match(/^ {1,4}/);
                if (match) {
                    ta.value = ta.value.substring(0, lineStart) + line.substring(match[0].length);
                    ta.selectionStart = ta.selectionEnd = start - match[0].length;
                }
            } else {
                ta.value = ta.value.substring(0, start) + '    ' + ta.value.substring(end);
                ta.selectionStart = ta.selectionEnd = start + 4;
            }
            ta.dispatchEvent(new Event('input'));
        }
    }

    // -----------------------------------------------------------------------
    // Keyboard handling (command mode)
    // -----------------------------------------------------------------------
    let lastDTime = 0;

    document.addEventListener('keydown', (e) => {
        if (state.mode === 'edit') return;
        // Ignore if user is in a select or button
        if (e.target.tagName === 'SELECT' || e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT') return;

        switch(e.key) {
            case 'a':
                if (e.metaKey || e.ctrlKey) {
                    e.preventDefault();
                    selectAllCells();
                } else {
                    e.preventDefault();
                    insertCell('above');
                }
                break;
            case 'b':
                e.preventDefault();
                insertCell('below');
                break;
            case 'd': {
                e.preventDefault();
                const now = Date.now();
                if (now - lastDTime < 500) {
                    deleteSelectedCells();
                    lastDTime = 0;
                } else {
                    lastDTime = now;
                }
                break;
            }
            case 'Enter':
                if (e.shiftKey) {
                    e.preventDefault();
                    runCellAndAdvance();
                } else {
                    e.preventDefault();
                    enterEditMode();
                }
                break;
            case 'ArrowUp':
                e.preventDefault();
                if (e.shiftKey) {
                    extendSelection(-1);
                } else {
                    moveFocus(-1);
                }
                break;
            case 'ArrowDown':
                e.preventDefault();
                if (e.shiftKey) {
                    extendSelection(1);
                } else {
                    moveFocus(1);
                }
                break;
            case 'Escape':
                state.mode = 'command';
                document.activeElement?.blur?.();
                break;
        }
    });

    function moveFocus(delta) {
        const next = Math.max(0, Math.min(state.cells.length - 1, state.focusedIndex + delta));
        focusCell(next);
        scrollToCell(next);
    }

    function extendSelection(delta) {
        const next = Math.max(0, Math.min(state.cells.length - 1, state.focusedIndex + delta));
        state.focusedIndex = next;
        state.selectedIndices.add(next);
        updateSelection();
        scrollToCell(next);
    }

    function selectAllCells() {
        state.selectedIndices = new Set(state.cells.map((_, i) => i));
        updateSelection();
    }

    // -----------------------------------------------------------------------
    // Cell operations
    // -----------------------------------------------------------------------
    function insertCell(where) {
        const index = where === 'above' ? state.focusedIndex : state.focusedIndex + 1;
        sendRequest({
            type: 'edit',
            operations: [{ op: 'insert', source: '', cell_type: 'code', at_index: index }]
        });
    }

    function deleteSelectedCells() {
        const indices = [...state.selectedIndices].sort((a, b) => b - a);
        const ops = indices.map(i => ({ op: 'delete', cell_id: state.cells[i]?.cell_id })).filter(o => o.cell_id);
        if (ops.length === 0) return;
        sendRequest({ type: 'edit', operations: ops });
    }

    function runCellAndAdvance() {
        flushActiveDraft();
        const cell = state.cells[state.focusedIndex];
        if (!cell || cell.cell_type !== 'code') {
            moveFocus(1);
            return;
        }
        state.executing.add(cell.cell_id);
        updateSelection();
        sendRequest({ type: 'execute-cell', cell_id: cell.cell_id });
        // Move focus down
        if (state.focusedIndex < state.cells.length - 1) {
            moveFocus(1);
        }
    }

    // -----------------------------------------------------------------------
    // Toolbar
    // -----------------------------------------------------------------------
    document.getElementById('btn-restart').addEventListener('click', () => {
        flushActiveDraft();
        sendRequest({ type: 'restart-kernel' });
    });

    document.getElementById('btn-run-all').addEventListener('click', () => {
        flushActiveDraft();
        state.cells.forEach(c => { if (c.cell_type === 'code') state.executing.add(c.cell_id); });
        updateSelection();
        sendRequest({ type: 'execute-all' });
    });

    document.getElementById('btn-restart-run').addEventListener('click', () => {
        flushActiveDraft();
        state.cells.forEach(c => { if (c.cell_type === 'code') state.executing.add(c.cell_id); });
        updateSelection();
        sendRequest({ type: 'restart-and-run-all' });
    });

    document.getElementById('kernel-select').addEventListener('change', (e) => {
        const kernelId = e.target.value;
        if (kernelId) {
            sendRequest({ type: 'select-kernel', kernel_id: kernelId });
        }
    });

    // -----------------------------------------------------------------------
    // Message handler (from extension host)
    // -----------------------------------------------------------------------
    window.addEventListener('message', (event) => {
        const msg = event.data;
        switch(msg.type) {
            case 'contents':
                state.cells = msg.cells || [];
                renderAllCells();
                break;

            case 'edit-result':
                // Reload after edits
                sendRequest({ type: 'load-contents' });
                break;

            case 'execute-started':
                if (msg.cell_id) {
                    state.executing.add(msg.cell_id);
                    updateSelection();
                }
                break;

            case 'activity-update':
                handleActivityUpdate(msg);
                break;

            case 'kernels':
                populateKernelSelect(msg.kernels, msg.preferred_kernel);
                break;

            case 'runtime':
                updateKernelStatus(msg);
                break;

            case 'error':
                console.error('Canvas error:', msg.message);
                break;
        }
    });

    // -----------------------------------------------------------------------
    // Activity updates (live collaboration)
    // -----------------------------------------------------------------------
    function handleActivityUpdate(msg) {
        let needsFullReload = false;

        for (const event of (msg.events || [])) {
            switch(event.event_type || event.type) {
                case 'cell-source-updated': {
                    const cell = findCell(event.cell_id);
                    if (cell && event.data?.cell) {
                        cell.source = event.data.cell.source;
                        updateCellInDom(cell);
                    }
                    break;
                }
                case 'cell-output-appended':
                case 'cell-outputs-updated': {
                    const cell = findCell(event.cell_id);
                    if (cell && event.data?.cell) {
                        cell.outputs = event.data.cell.outputs || [];
                        if (event.data.cell.execution_count != null) {
                            cell.execution_count = event.data.cell.execution_count;
                        }
                        updateCellInDom(cell);
                    }
                    break;
                }
                case 'execution-started':
                    if (event.cell_id) state.executing.add(event.cell_id);
                    break;
                case 'execution-finished':
                case 'cell-execution-updated':
                    if (event.cell_id) {
                        state.executing.delete(event.cell_id);
                        const cell = findCell(event.cell_id);
                        if (cell && event.data?.cell) {
                            cell.outputs = event.data.cell.outputs || cell.outputs;
                            if (event.data.cell.execution_count != null) {
                                cell.execution_count = event.data.cell.execution_count;
                            }
                            updateCellInDom(cell);
                        }
                    }
                    break;
                case 'cell-inserted':
                case 'cell-removed':
                case 'notebook-reset-needed':
                    needsFullReload = true;
                    break;
            }
        }

        // Update kernel status from runtime info
        if (msg.runtime) {
            updateKernelStatus({ busy: msg.runtime.busy });
        }

        if (msg.cursor) {
            state.activityCursor = msg.cursor;
        }

        if (needsFullReload) {
            sendRequest({ type: 'load-contents' });
        } else {
            updateSelection();
        }
    }

    function findCell(cellId) {
        return state.cells.find(c => c.cell_id === cellId);
    }

    function updateCellInDom(cell) {
        const index = state.cells.indexOf(cell);
        if (index < 0) return;
        const els = document.querySelectorAll('.cell');
        const el = els[index];
        if (!el) return;

        // Update gutter
        const gutter = el.querySelector('.cell-gutter');
        if (gutter) {
            if (state.executing.has(cell.cell_id)) {
                gutter.innerHTML = '<span class="exec-indicator"></span>';
            } else if (cell.cell_type === 'code' && cell.execution_count != null) {
                gutter.textContent = '[' + cell.execution_count + ']';
            } else if (cell.cell_type === 'code') {
                gutter.textContent = '[ ]';
            }
        }

        // Update source (only if not being edited)
        if (state.mode !== 'edit' || state.focusedIndex !== index) {
            const ta = el.querySelector('textarea');
            if (ta && cell.cell_type === 'code') {
                ta.value = cell.source;
                autoResize(ta);
            }
            const md = el.querySelector('.cell-markdown.rendered');
            if (md) {
                md.innerHTML = renderMarkdown(cell.source);
            }
        }

        // Update outputs
        let outputsDiv = el.querySelector('.cell-outputs');
        if (cell.outputs && cell.outputs.length > 0) {
            if (!outputsDiv) {
                outputsDiv = document.createElement('div');
                outputsDiv.className = 'cell-outputs';
                el.appendChild(outputsDiv);
            }
            outputsDiv.innerHTML = '';
            cell.outputs.forEach(out => outputsDiv.appendChild(renderOutput(out)));
        } else if (outputsDiv) {
            outputsDiv.remove();
        }
    }

    // -----------------------------------------------------------------------
    // Kernel UI
    // -----------------------------------------------------------------------
    function populateKernelSelect(kernels, preferred) {
        const select = document.getElementById('kernel-select');
        select.innerHTML = '<option value="">Select kernel...</option>';
        (kernels || []).forEach(k => {
            const opt = document.createElement('option');
            opt.value = k.id;
            opt.textContent = k.label + (k.recommended ? ' (recommended)' : '');
            if (preferred && preferred.id === k.id) opt.selected = true;
            select.appendChild(opt);
        });
    }

    function updateKernelStatus(info) {
        const el = document.getElementById('kernel-status');
        if (info.busy) {
            el.textContent = 'busy';
            el.className = 'kernel-status busy';
        } else {
            el.textContent = info.kernel_label || 'idle';
            el.className = 'kernel-status';
        }
    }

    // -----------------------------------------------------------------------
    // Link handling (open external)
    // -----------------------------------------------------------------------
    document.addEventListener('click', (e) => {
        const a = e.target.closest('a[href]');
        if (a) {
            e.preventDefault();
            send({ type: 'open-external-link', requestId: nextReqId(), url: a.href });
        }
    });

    // -----------------------------------------------------------------------
    // Init: request path from extension host, then load
    // -----------------------------------------------------------------------
    send({ type: 'webview-ready', requestId: nextReqId() });
})();
</script>
</body>
</html>`;
}

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { autocompletion } from '@codemirror/autocomplete';
import { globalCompletion, localCompletionSource } from '@codemirror/lang-python';
import { search as searchExtension } from '@codemirror/search';
import { CommandRegistry } from '@lumino/commands';
import { Widget } from '@lumino/widgets';
import { Theme } from '@carbon/react';
import {
  ChevronDown,
  ChevronRight,
  Erase,
  Folder,
  FolderOpen,
  Notebook as NotebookIcon,
  PlayFilled,
  Restart,
  Save,
} from '@carbon/icons-react';
import { nullTranslator } from '@jupyterlab/translation';
import { Notebook, NotebookActions, NotebookModel, StaticNotebook } from '@jupyterlab/notebook';
import * as nbformat from '@jupyterlab/nbformat';
import type { CodeEditor } from '@jupyterlab/codeeditor';
import { CodeMirrorEditorFactory, CodeMirrorMimeTypeService, EditorExtensionRegistry, EditorLanguageRegistry } from '@jupyterlab/codemirror';
import { HTMLManager } from '@jupyter-widgets/html-manager/lib/htmlmanager';
import type { IManagerState } from '@jupyter-widgets/base-manager';

import { buildReplaceSourceOperations } from '../src/shared/notebookEditPayload';
import { DaemonWebSocket } from '../src/shared/wsClient';
import { shouldReloadStandaloneNotebookContents } from '../src/shared/notebookActivity';

import '@lumino/widgets/style/index.css';
import '@jupyterlab/rendermime/style/base.css';
import '@jupyterlab/outputarea/style/base.css';
import '@jupyterlab/codeeditor/style/base.css';
import '@jupyterlab/codemirror/style/base.css';
import '@jupyterlab/cells/style/base.css';
import '@jupyterlab/notebook/style/base.css';
import '@jupyterlab/theme-light-extension/style/theme.css';

type NotebookOutput = {
  output_type: string;
  name?: string;
  text?: string | string[];
  ename?: string;
  evalue?: string;
  traceback?: string[];
  data?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
  transient?: Record<string, unknown>;
};

type NotebookCell = {
  cell_id: string;
  cell_type: 'code' | 'markdown' | 'raw' | string;
  source: string;
  outputs?: NotebookOutput[];
  execution_count?: number | null;
  metadata?: Record<string, unknown>;
  trusted?: boolean | null;
};

type NotebookTrustSnapshot = {
  notebook_trusted: boolean;
  trusted_code_cells: number;
  total_code_cells: number;
};

type NotebookMetadata = Record<string, unknown>;

type NotebookSharedSnapshot = {
  cells?: NotebookCell[];
  document_version?: number;
  notebook_metadata?: NotebookMetadata;
  notebook_trusted?: boolean;
  trusted_code_cells?: number;
  total_code_cells?: number;
};

type NotebookEditOperation = Record<string, unknown>;

type StructureUndoEntry = {
  inverseOperations: NotebookEditOperation[];
  forwardOperations?: NotebookEditOperation[];
  successLabel: string;
};

type RuntimeSnapshot = {
  busy: boolean;
  kernel_label: string;
  kernel_generation: number;
  current_execution: Record<string, unknown> | null;
  running_cell_ids: string[];
  queued_cell_ids: string[];
};

type JupyterLabPreviewAppProps = {
  notebookPath: string | null;
};

type WorkspaceTreeNode = {
  kind: 'directory' | 'notebook';
  name: string;
  path: string;
  children?: WorkspaceTreeNode[];
};

type Kernel = {
  id: string;
  label: string;
  recommended?: boolean;
};

type ThemeMode = 'system' | 'light' | 'dark';

type JupyterLabPreviewDebugWindow = Window & {
  __agentReplJupyterLab?: {
    executeCommand: (commandId: string) => Promise<unknown>;
    focusNotebook: () => void;
    getPendingCommandCount: () => number;
    getUndoDepth: () => number;
    getRedoDepth: () => number;
  };
};

const PREVIEW_CLIENT_ID = globalThis.crypto?.randomUUID?.() ?? `jupyterlab-preview-${Date.now()}`;
const AUTOSAVE_DELAY_MS = 350;
const BENIGN_YJS_PREMATURE_ACCESS_WARNING = 'Invalid access: Add Yjs type to a document before reading data.';
const BENIGN_YJS_MULTIDOC_WARNING = '[yjs#509] Not same Y.Doc';
const NOTEBOOK_COMMAND_SELECTOR = '.agent-repl-jupyterlab-notebook';
const BOOTSTRAP_RETRY_DELAY_MS = 500;
const BOOTSTRAP_MAX_ATTEMPTS = 8;
const UI_FONT = 'IBM Plex Sans';
const CELL_GUTTER_WIDTH = 40;
const EXPLORER_COLLAPSED_STORAGE_KEY = 'agent-repl.explorer-collapsed';
const THEME_MODE_STORAGE_KEY = 'agent-repl.theme-mode';

const COMMAND_IDS = {
  enterEditMode: 'agent-repl:notebook-enter-edit-mode',
  enterCommandMode: 'agent-repl:notebook-enter-command-mode',
  selectAll: 'agent-repl:notebook-select-all',
  selectAbove: 'agent-repl:notebook-select-above',
  selectBelow: 'agent-repl:notebook-select-below',
  extendSelectionAbove: 'agent-repl:notebook-extend-selection-above',
  extendSelectionBelow: 'agent-repl:notebook-extend-selection-below',
  insertAbove: 'agent-repl:notebook-insert-above',
  insertBelow: 'agent-repl:notebook-insert-below',
  changeToCode: 'agent-repl:notebook-change-to-code',
  changeToMarkdown: 'agent-repl:notebook-change-to-markdown',
  deleteCells: 'agent-repl:notebook-delete-cells',
  moveUp: 'agent-repl:notebook-move-up',
  moveDown: 'agent-repl:notebook-move-down',
  runCell: 'agent-repl:notebook-run-cell',
  runAndAdvance: 'agent-repl:notebook-run-and-advance',
  runAndInsertBelow: 'agent-repl:notebook-run-and-insert-below',
  runAll: 'agent-repl:notebook-run-all',
  save: 'agent-repl:notebook-save',
  undoNotebook: 'agent-repl:notebook-undo',
  redoNotebook: 'agent-repl:notebook-redo',
  clearAllOutputs: 'agent-repl:notebook-clear-all-outputs',
  splitCell: 'agent-repl:notebook-split-cell',
  mergeCells: 'agent-repl:notebook-merge-cells',
  copyCells: 'agent-repl:notebook-copy-cells',
  pasteCells: 'agent-repl:notebook-paste-cells',
} as const;

function createToolbarButtonBaseStyle(): React.CSSProperties {
  return {
    fontFamily: `"${UI_FONT}", sans-serif`,
    fontSize: 13,
    fontWeight: 500,
    height: 32,
    borderRadius: 6,
    cursor: 'pointer',
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    lineHeight: 1.25,
    whiteSpace: 'nowrap',
    flexShrink: 1,
    minWidth: 0,
  };
}

function createToolbarOutlineButtonStyle(compact: boolean): React.CSSProperties {
  return {
    ...createToolbarButtonBaseStyle(),
    padding: compact ? '0 10px' : '0 12px',
    border: '1px solid var(--cds-border-subtle)',
    background: 'transparent',
    color: 'var(--cds-text-secondary)',
  };
}

function normalizeThemeMode(value: unknown): ThemeMode {
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'system';
}

function normalizeCollapsedExplorer(value: unknown): boolean {
  return value === 'true';
}

function collectExplorerAncestorPaths(notebookPath: string | null | undefined): Set<string> {
  if (!notebookPath) {
    return new Set();
  }
  const normalized = notebookPath.replace(/\\/g, '/');
  const segments = normalized.split('/').filter(Boolean);
  const expanded = new Set<string>();
  let current = '';
  for (let index = 0; index < Math.max(0, segments.length - 1); index += 1) {
    current = current ? `${current}/${segments[index]}` : segments[index];
    expanded.add(current);
  }
  return expanded;
}

function useDarkMode(): boolean {
  const [dark, setDark] = useState(() => {
    if (document.body.classList.contains('vscode-dark') || document.body.classList.contains('vscode-high-contrast')) {
      return true;
    }
    if (document.body.classList.contains('vscode-light')) {
      return false;
    }
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });

  useEffect(() => {
    const observer = new MutationObserver(() => {
      if (document.body.classList.contains('vscode-dark') || document.body.classList.contains('vscode-high-contrast')) {
        setDark(true);
        return;
      }
      if (document.body.classList.contains('vscode-light')) {
        setDark(false);
      }
    });
    observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    const handleMq = (event: MediaQueryListEvent) => {
      if (
        !document.body.classList.contains('vscode-dark')
        && !document.body.classList.contains('vscode-light')
        && !document.body.classList.contains('vscode-high-contrast')
      ) {
        setDark(event.matches);
      }
    };
    mq?.addEventListener('change', handleMq);
    return () => {
      observer.disconnect();
      mq?.removeEventListener('change', handleMq);
    };
  }, []);

  return dark;
}

function shouldRetryStandaloneBootstrapError(message: string): boolean {
  return /fetch failed/i.test(message) || /No running agent-repl core daemon matched/i.test(message);
}

async function postJson<T>(url: string, body: Record<string, unknown>): Promise<T> {
  const maxAttempts = 3;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const text = await response.text();
      let payload: Record<string, unknown> = {};
      try {
        payload = text ? JSON.parse(text) : {};
      } catch {
        payload = {};
      }
      if (!response.ok) {
        const errorMessage = typeof payload.error === 'string' && payload.error.trim()
          ? payload.error
          : `Request failed with status ${response.status}`;
        if (attempt < maxAttempts && shouldRetryStandaloneBootstrapError(errorMessage)) {
          await new Promise((resolve) => window.setTimeout(resolve, 250));
          continue;
        }
        throw new Error(errorMessage);
      }
      return payload as T;
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      if (attempt < maxAttempts && shouldRetryStandaloneBootstrapError(errorMessage)) {
        await new Promise((resolve) => window.setTimeout(resolve, 250));
        continue;
      }
      throw error;
    }
  }
  throw new Error(`Request failed after ${maxAttempts} attempts: ${url}`);
}

function isTransientBootstrapError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /fetch failed|Failed to fetch|NetworkError|Load failed/i.test(message);
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function toNotebookJson(cells: NotebookCell[], notebookMetadata?: NotebookMetadata): nbformat.INotebookContent {
  return {
    cells: cells.map((cell) => {
      if (cell.cell_type === 'code') {
        const metadata = { ...(cell.metadata ?? {}) } as nbformat.ICodeCellMetadata & { trusted?: boolean };
        if (typeof cell.trusted === 'boolean') {
          metadata.trusted = cell.trusted;
        }
        return {
          cell_type: 'code',
          id: cell.cell_id,
          metadata,
          execution_count: cell.execution_count ?? null,
          outputs: (cell.outputs ?? []) as nbformat.IOutput[],
          source: cell.source,
        } satisfies nbformat.ICodeCell;
      }
      if (cell.cell_type === 'raw') {
        return {
          cell_type: 'raw',
          id: cell.cell_id,
          metadata: (cell.metadata ?? {}) as nbformat.IRawCellMetadata,
          source: cell.source,
        } satisfies nbformat.IRawCell;
      }
      return {
        cell_type: 'markdown',
        id: cell.cell_id,
        metadata: (cell.metadata ?? {}) as nbformat.IBaseCellMetadata,
        source: cell.source,
      } satisfies nbformat.IMarkdownCell;
    }),
    metadata: normalizeNotebookMetadata(notebookMetadata) as nbformat.INotebookMetadata,
    nbformat: 4,
    nbformat_minor: 5,
  };
}

function cloneCells(cells: NotebookCell[]): NotebookCell[] {
  return JSON.parse(JSON.stringify(cells)) as NotebookCell[];
}

function cloneNotebookMetadata(metadata: NotebookMetadata | null | undefined): NotebookMetadata {
  return JSON.parse(JSON.stringify(metadata ?? {})) as NotebookMetadata;
}

function applyNotebookOperationsToCells(cells: NotebookCell[], operations: NotebookEditOperation[]): NotebookCell[] {
  const nextCells = cloneCells(cells);

  for (const operation of operations) {
    const command = operation.op;
    if (command === 'insert') {
      const atIndex = typeof operation.at_index === 'number' ? operation.at_index : nextCells.length;
      const boundedIndex = Math.max(0, Math.min(atIndex, nextCells.length));
      nextCells.splice(boundedIndex, 0, {
        cell_id: typeof operation.cell_id === 'string' && operation.cell_id
          ? operation.cell_id
          : `cell-${Math.random().toString(36).slice(2, 10)}`,
        cell_type: operation.cell_type === 'markdown' ? 'markdown' : 'code',
        source: typeof operation.source === 'string' ? operation.source : '',
        metadata: typeof operation.metadata === 'object' && operation.metadata
          ? { ...(operation.metadata as Record<string, unknown>) }
          : {},
        outputs: Array.isArray(operation.outputs) ? operation.outputs as NotebookOutput[] : [],
        execution_count: typeof operation.execution_count === 'number' || operation.execution_count === null
          ? operation.execution_count as number | null
          : null,
      });
      continue;
    }

    const matchIndex = typeof operation.cell_id === 'string'
      ? nextCells.findIndex((cell) => cell.cell_id === operation.cell_id)
      : (typeof operation.cell_index === 'number' ? operation.cell_index : -1);
    if (matchIndex < 0 || matchIndex >= nextCells.length) {
      continue;
    }

    if (command === 'delete') {
      nextCells.splice(matchIndex, 1);
      continue;
    }

    if (command === 'move') {
      const toIndex = typeof operation.to_index === 'number' ? operation.to_index : matchIndex;
      const boundedIndex = Math.max(0, Math.min(toIndex, nextCells.length - 1));
      if (boundedIndex === matchIndex) {
        continue;
      }
      const [movedCell] = nextCells.splice(matchIndex, 1);
      nextCells.splice(boundedIndex, 0, movedCell);
      continue;
    }

    if (command === 'change-cell-type') {
      const currentCell = nextCells[matchIndex];
      nextCells[matchIndex] = {
        ...currentCell,
        cell_type: operation.cell_type === 'markdown' ? 'markdown' : 'code',
        source: typeof operation.source === 'string' ? operation.source : currentCell.source,
        outputs: operation.cell_type === 'code' ? [] : [],
        execution_count: null,
      };
    }
  }

  return nextCells;
}

function normalizeNotebookMetadata(raw: unknown): NotebookMetadata {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {
      kernelspec: {
        display_name: 'Python 3',
        language: 'python',
        name: 'python3',
      },
      language_info: {
        name: 'python',
      },
    };
  }
  const metadata = cloneNotebookMetadata(raw as NotebookMetadata);
  const kernelspec = metadata.kernelspec ?? {
    display_name: 'Python 3',
    language: 'python',
    name: 'python3',
  };
  const languageInfo = metadata.language_info ?? { name: 'python' };
  return {
    ...metadata,
    kernelspec,
    language_info: languageInfo,
  };
}

function notebookMetadataSignature(metadata: NotebookMetadata): string {
  return JSON.stringify(metadata);
}

function extractWidgetState(metadata: NotebookMetadata): IManagerState | null {
  const widgets = metadata.widgets as Record<string, unknown> | undefined;
  if (!widgets || typeof widgets !== 'object' || Array.isArray(widgets)) {
    return null;
  }
  const widgetState = widgets['application/vnd.jupyter.widget-state+json'];
  if (!widgetState || typeof widgetState !== 'object' || Array.isArray(widgetState)) {
    return null;
  }
  const normalized = cloneNotebookMetadata(widgetState as NotebookMetadata) as Record<string, unknown>;
  if (typeof normalized.version_major === 'number' && normalized.state && typeof normalized.state === 'object' && !Array.isArray(normalized.state)) {
    return normalized as IManagerState;
  }
  return {
    version_major: 2,
    version_minor: 0,
    state: normalized,
  } as IManagerState;
}

function trustSnapshotFromPayload(payload: Partial<NotebookTrustSnapshot> | null | undefined, cells: NotebookCell[]): NotebookTrustSnapshot {
  const codeCells = cells.filter((cell) => cell.cell_type === 'code');
  const trustedCodeCells = codeCells.filter((cell) => cell.trusted === true).length;
  return {
    notebook_trusted: Boolean(payload?.notebook_trusted),
    trusted_code_cells: typeof payload?.trusted_code_cells === 'number' ? payload.trusted_code_cells : trustedCodeCells,
    total_code_cells: typeof payload?.total_code_cells === 'number' ? payload.total_code_cells : codeCells.length,
  };
}

function trustSignature(trust: NotebookTrustSnapshot, cells: NotebookCell[]): string {
  const perCell = cells
    .filter((cell) => cell.cell_type === 'code')
    .map((cell) => (cell.trusted ? '1' : '0'))
    .join('');
  return `${trust.notebook_trusted ? '1' : '0'}:${trust.trusted_code_cells}:${trust.total_code_cells}:${perCell}`;
}

function normalizeCellSource(source: unknown): string {
  if (Array.isArray(source)) {
    return source.join('');
  }
  return typeof source === 'string' ? source : '';
}

function readWidgetEditorSource(widget: Notebook['widgets'][number]): string | null {
  const editorHost = widget.editor?.host;
  if (!(editorHost instanceof HTMLElement)) {
    return null;
  }
  const contentRoot = editorHost.querySelector('.cm-content');
  if (!(contentRoot instanceof HTMLElement)) {
    return null;
  }
  const lines = Array.from(contentRoot.querySelectorAll('.cm-line'));
  if (lines.length > 0) {
    return lines.map((line) => line.textContent ?? '').join('\n');
  }
  return contentRoot.textContent ?? '';
}

function notebookCellsFromWidgets(notebook: Notebook, model: NotebookModel): NotebookCell[] {
  const notebookJson = model.toJSON();
  const jsonCells = Array.isArray(notebookJson.cells) ? notebookJson.cells : [];

  return notebook.widgets.map((widget, index) => {
    const cellModel = widget.model as {
      id?: string;
      type?: string;
      toJSON?: () => {
        id?: string;
        cell_type?: string;
        source?: string | string[];
        outputs?: NotebookOutput[];
        execution_count?: number | null;
        metadata?: Record<string, unknown>;
      };
      sharedModel?: {
        getId?: () => string;
        getSource?: () => string;
      };
      executionCount?: number | null;
    };
    const fallbackJson = jsonCells[index] ?? {};
    const cellJson = typeof cellModel.toJSON === 'function' ? cellModel.toJSON() : fallbackJson;
    const editorSource = readWidgetEditorSource(widget);
    return {
      cell_id: typeof cellJson.id === 'string' && cellJson.id
        ? cellJson.id
        : typeof cellModel.sharedModel?.getId === 'function'
          ? cellModel.sharedModel.getId()
          : typeof cellModel.id === 'string' && cellModel.id
            ? cellModel.id
            : `cell-${Math.random().toString(36).slice(2, 10)}`,
      cell_type: cellModel.type ?? cellJson.cell_type ?? 'code',
      source: editorSource ?? (typeof cellModel.sharedModel?.getSource === 'function'
        ? cellModel.sharedModel.getSource()
        : normalizeCellSource(cellJson.source)),
      outputs: Array.isArray(cellJson.outputs) ? cellJson.outputs as NotebookOutput[] : [],
      execution_count: typeof cellModel.executionCount === 'number'
        ? cellModel.executionCount
        : (typeof cellJson.execution_count === 'number' ? cellJson.execution_count : null),
      metadata: (cellJson.metadata ?? {}) as Record<string, unknown>,
    };
  });
}

function formatRuntimeLabel(runtime: RuntimeSnapshot): string {
  if (runtime.busy) {
    return runtime.kernel_label ? `${runtime.kernel_label} • Running` : 'Running';
  }
  return runtime.kernel_label ? `${runtime.kernel_label} • Idle` : 'Idle';
}

function getSelectedCellIndices(notebook: Notebook): number[] {
  const indices = notebook.selectedCells
    .map((cell) => notebook.widgets.indexOf(cell))
    .filter((index): index is number => index >= 0);

  if (indices.length === 0 && notebook.activeCellIndex >= 0) {
    return [notebook.activeCellIndex];
  }

  return [...new Set(indices)].sort((left, right) => left - right);
}

function focusActiveCellEditor(notebook: Notebook): void {
  const activeCell = notebook.activeCell;
  if (!activeCell) {
    return;
  }
  const editor = activeCell.editor;
  if (!editor) {
    return;
  }
  editor.focus();
  // Move cursor to end of last line
  const lastLine = editor.lineCount - 1;
  const lastLineLength = editor.getLine(lastLine)?.length ?? 0;
  editor.setCursorPosition({ line: lastLine, column: lastLineLength });
}

function focusNotebookCommandSelection(notebook: Notebook): void {
  notebook.mode = 'command';
  const cellCount = notebook.widgets.length;
  if (cellCount > 0) {
    const nextIndex = notebook.activeCellIndex >= 0
      ? Math.min(notebook.activeCellIndex, cellCount - 1)
      : 0;
    notebook.activeCellIndex = nextIndex;
  }
  notebook.activate();
  notebook.activeCell?.node.focus();
  notebook.node.focus();
}

function configureEditor(editor: CodeEditor.IEditor): void {
  editor.setOptions({
    codeFolding: true,
    highlightActiveLine: true,
    matchBrackets: true,
  });
}

function installBenignYjsWarningFilter(): () => void {
  const originalWarn = console.warn;
  const filteredWarn = (...args: unknown[]) => {
    const firstArg = args[0];
    if (typeof firstArg === 'string' && (
      firstArg.includes(BENIGN_YJS_PREMATURE_ACCESS_WARNING)
      || firstArg.includes(BENIGN_YJS_MULTIDOC_WARNING)
    )) {
      return;
    }
    originalWarn(...args);
  };

  console.warn = filteredWarn;
  return () => {
    if (console.warn === filteredWarn) {
      console.warn = originalWarn;
    }
  };
}

function shouldHandleNotebookShortcut(event: KeyboardEvent, notebook: Notebook): boolean {
  if (event.isComposing) {
    return false;
  }

  const normalizedKey = event.key.length === 1 ? event.key.toLowerCase() : event.key;
  const accel = event.metaKey || event.ctrlKey;

  if (notebook.mode === 'edit') {
    if (normalizedKey === 'Escape') {
      return true;
    }
    if (normalizedKey === 'Enter' && event.shiftKey) {
      return true;
    }
    if (normalizedKey === 'Enter' && (accel || event.altKey)) {
      return true;
    }
    if (normalizedKey.toLowerCase() === 's' && accel) {
      return true;
    }
    if (accel && event.shiftKey && (normalizedKey === '-' || normalizedKey === 'minus')) {
      return true;
    }
    return false;
  }

  if (accel && normalizedKey === 'a') {
    return true;
  }
  if (normalizedKey === 'Enter' || normalizedKey === 'Escape') {
    return true;
  }
  if (normalizedKey === 'ArrowUp' || normalizedKey === 'ArrowDown') {
    return !event.altKey && !accel;
  }
  return ['a', 'b', 'c', 'd', 'j', 'k', 'm', 'v', 'y', 'z'].includes(normalizedKey);
}

function shouldRouteNotebookEvent(
  target: EventTarget | null,
  mountNode: HTMLElement,
  notebook: Notebook,
): boolean {
  if (target instanceof Node && mountNode.contains(target)) {
    return true;
  }
  return notebook.mode === 'command'
    && (target === document.body || target === document.documentElement);
}

function findCellIndexById(model: NotebookModel, cellId: string): number {
  for (let i = 0; i < model.cells.length; i++) {
    const cellModel = model.cells.get(i);
    const id = typeof (cellModel.sharedModel as any).getId === 'function'
      ? (cellModel.sharedModel as any).getId()
      : (cellModel as any).id ?? '';
    if (id === cellId) return i;
  }
  return -1;
}

export function JupyterLabPreviewApp({ notebookPath }: JupyterLabPreviewAppProps) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const kernelMenuRef = useRef<HTMLDivElement | null>(null);
  const notebookRef = useRef<Notebook | null>(null);
  const modelRef = useRef<NotebookModel | null>(null);
  const lastSyncedCellsRef = useRef<NotebookCell[]>([]);
  const autoSaveTimerRef = useRef<number | null>(null);
  const displayIdMapRef = useRef<Map<string, Map<string, number[]>>>(new Map());
  const [cellModelVersion, setCellModelVersion] = useState(0);
  const executedCellIdsRef = useRef<Set<string>>(new Set());
  const toolbarStatusTimerRef = useRef<number | null>(null);
  const applyingRemoteRef = useRef(false);
  const applyingNotebookActionRef = useRef(false);
  const pendingExecutionRef = useRef(false);
  const runtimeBusyRef = useRef(false);
  const structureUndoStackRef = useRef<StructureUndoEntry[]>([]);
  const structureRedoStackRef = useRef<StructureUndoEntry[]>([]);
  const contentsRequestSeqRef = useRef(0);
  const appliedDocumentVersionRef = useRef(0);
  const appliedTrustSignatureRef = useRef('');
  const appliedNotebookMetadataSignatureRef = useRef('');
  const fallbackDocumentVersionRef = useRef(0);
  const pendingNotebookCommandCountRef = useRef(0);

  const [currentNotebookPath, setCurrentNotebookPath] = useState<string | null>(notebookPath);
  const [workspaceTree, setWorkspaceTree] = useState<WorkspaceTreeNode | null>(null);
  const [workspaceName, setWorkspaceName] = useState('');
  const [explorerExpandedPaths, setExplorerExpandedPaths] = useState<Set<string>>(
    () => collectExplorerAncestorPaths(notebookPath),
  );
  const [explorerCollapsed, setExplorerCollapsed] = useState<boolean>(() => {
    try {
      return normalizeCollapsedExplorer(window.localStorage.getItem(EXPLORER_COLLAPSED_STORAGE_KEY));
    } catch {
      return false;
    }
  });
  const [themeMode, setThemeMode] = useState<ThemeMode>(() => {
    try {
      return normalizeThemeMode(window.localStorage.getItem(THEME_MODE_STORAGE_KEY));
    } catch {
      return 'system';
    }
  });
  const [kernels, setKernels] = useState<Kernel[]>([]);
  const [selectedKernelId, setSelectedKernelId] = useState('');
  const [kernelMenuOpen, setKernelMenuOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [surfaceReady, setSurfaceReady] = useState(false);
  const [initialCells, setInitialCells] = useState<NotebookCell[] | null>(null);
  const [initialDocumentVersion, setInitialDocumentVersion] = useState(0);
  const [initialNotebookMetadata, setInitialNotebookMetadata] = useState<NotebookMetadata>(() => normalizeNotebookMetadata(null));
  const [initialTrustSnapshot, setInitialTrustSnapshot] = useState<NotebookTrustSnapshot>({
    notebook_trusted: false,
    trusted_code_cells: 0,
    total_code_cells: 0,
  });
  const systemDark = useDarkMode();
  const dark = themeMode === 'system' ? systemDark : themeMode === 'dark';
  const carbonTheme = dark ? 'g100' : 'g10';

  useEffect(() => {
    setCurrentNotebookPath(notebookPath);
    setExplorerExpandedPaths(collectExplorerAncestorPaths(notebookPath));
  }, [notebookPath]);

  useEffect(() => {
    try {
      window.localStorage.setItem(EXPLORER_COLLAPSED_STORAGE_KEY, explorerCollapsed ? 'true' : 'false');
    } catch {
      // noop
    }
  }, [explorerCollapsed]);

  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_MODE_STORAGE_KEY, themeMode);
    } catch {
      // noop
    }
  }, [themeMode]);
  const [runtime, setRuntime] = useState<RuntimeSnapshot>({
    busy: false,
    kernel_label: '',
    kernel_generation: 0,
    current_execution: null,
    running_cell_ids: [],
    queued_cell_ids: [],
  });
  const lastKernelGenerationRef = useRef(0);
  const [toolbarStatus, setToolbarStatus] = useState<string | null>(null);
  const [trustSnapshot, setTrustSnapshot] = useState<NotebookTrustSnapshot>({
    notebook_trusted: false,
    trusted_code_cells: 0,
    total_code_cells: 0,
  });

  useEffect(() => {
    const endSession = () => {
      void fetch('/api/standalone/session-end', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client_id: PREVIEW_CLIENT_ID, path: currentNotebookPath }),
        keepalive: true,
      }).catch(() => {});
    };

    window.addEventListener('beforeunload', endSession);
    return () => {
      window.removeEventListener('beforeunload', endSession);
      endSession();
    };
  }, [currentNotebookPath]);

  const showToolbarStatus = useCallback((message: string | null, durationMs = 1600) => {
    if (toolbarStatusTimerRef.current != null) {
      window.clearTimeout(toolbarStatusTimerRef.current);
      toolbarStatusTimerRef.current = null;
    }
    setToolbarStatus(message);
    if (!message || durationMs <= 0) {
      return;
    }
    toolbarStatusTimerRef.current = window.setTimeout(() => {
      setToolbarStatus((current) => (current === message ? null : current));
      toolbarStatusTimerRef.current = null;
    }, durationMs);
  }, []);

  const ensureAttached = useCallback(async () => {
    if (!currentNotebookPath) {
      throw new Error('No notebook path provided.');
    }
    await postJson('/api/standalone/attach', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
  }, [currentNotebookPath]);

  const fetchSharedModelSnapshot = useCallback(async () => {
    if (!currentNotebookPath) {
      return {
        cells: [],
        document_version: 0,
        notebook_metadata: normalizeNotebookMetadata(null),
        notebook_trusted: false,
        trusted_code_cells: 0,
        total_code_cells: 0,
      };
    }
    try {
      return await postJson<NotebookSharedSnapshot>('/api/standalone/notebook/shared-model', {
        client_id: PREVIEW_CLIENT_ID,
        path: currentNotebookPath,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      if (!/Not Found/i.test(message)) {
        throw error;
      }
      const legacyContents = await postJson<{ cells?: NotebookCell[] }>('/api/standalone/notebook/contents', {
        client_id: PREVIEW_CLIENT_ID,
        path: currentNotebookPath,
      });
      fallbackDocumentVersionRef.current += 1;
      return {
        cells: legacyContents.cells,
        document_version: fallbackDocumentVersionRef.current,
        notebook_metadata: normalizeNotebookMetadata(null),
        notebook_trusted: false,
        trusted_code_cells: 0,
        total_code_cells: 0,
      };
    }
  }, [currentNotebookPath]);

  const applyRemoteCells = useCallback((cells: NotebookCell[]) => {
    const model = modelRef.current;
    const notebook = notebookRef.current;
    if (!model || !notebook) {
      return;
    }

    const previousActiveIndex = notebook.activeCellIndex;
    const previousMode = notebook.mode;
    const currentCells = notebookCellsFromWidgets(notebook, model);
    const sameStructure = currentCells.length === cells.length
      && currentCells.every((cell, index) => cell.cell_id === cells[index]?.cell_id && cell.cell_type === cells[index]?.cell_type);

    applyingRemoteRef.current = true;
    try {
      if (sameStructure) {
        for (let index = 0; index < cells.length; index += 1) {
          const nextCell = cells[index];
          const cellModel = model.cells.get(index);
          if (!cellModel) {
            continue;
          }

          if (cellModel.sharedModel.getSource() !== nextCell.source) {
            cellModel.sharedModel.setSource(nextCell.source);
          }

          if (cellModel.type === 'code' && nextCell.cell_type === 'code') {
            const codeCellModel = cellModel as unknown as {
              executionCount: number | null;
              sharedModel: {
                setOutputs: (outputs: NotebookOutput[]) => void;
              };
              outputs: {
                clear: (wait?: boolean) => void;
                fromJSON: (outputs: NotebookOutput[]) => void;
              };
              trusted: boolean;
            };
            codeCellModel.executionCount = nextCell.execution_count ?? null;
            // Update both shared model and output area model for rendering
            // Use fromJSON directly (replaces all outputs atomically) to avoid flicker from clear+set
            codeCellModel.sharedModel.setOutputs(nextCell.outputs ?? []);
            codeCellModel.outputs.fromJSON(nextCell.outputs ?? []);
            codeCellModel.trusted = nextCell.trusted === true;
          }
        }
      } else {
        const sharedModel = model.sharedModel as unknown as {
          transact?: (fn: () => void) => void;
          nbformat?: number;
          nbformat_minor?: number;
          metadata?: nbformat.INotebookMetadata;
          deleteCellRange: (from: number, to: number) => void;
          insertCells: (index: number, values: Array<nbformat.ICell>) => void;
        };
        const nextNotebook = toNotebookJson(cells, initialNotebookMetadata);
        const replaceCells = () => {
          sharedModel.nbformat = nextNotebook.nbformat;
          sharedModel.nbformat_minor = nextNotebook.nbformat_minor;
          sharedModel.metadata = nextNotebook.metadata;
          sharedModel.deleteCellRange(0, model.cells.length);
          sharedModel.insertCells(0, nextNotebook.cells as Array<nbformat.ICell>);
        };
        if (typeof sharedModel.transact === 'function') {
          sharedModel.transact(replaceCells);
        } else {
          replaceCells();
        }
        for (let index = 0; index < cells.length; index += 1) {
          const nextCell = cells[index];
          const cellModel = model.cells.get(index);
          if (!cellModel || cellModel.type !== 'code' || nextCell.cell_type !== 'code') {
            continue;
          }
          (cellModel as unknown as { trusted: boolean }).trusted = nextCell.trusted === true;
        }
      }
      model.dirty = false;
      lastSyncedCellsRef.current = cloneCells(cells);
      if (model.cells.length > 0) {
        notebook.activeCellIndex = Math.max(0, Math.min(previousActiveIndex, model.cells.length - 1));
      }
      notebook.mode = previousMode;
      notebook.update();
      // Trigger status bar re-evaluation after model update
      setCellModelVersion((v) => v + 1);
    } finally {
      applyingRemoteRef.current = false;
    }
  }, [initialNotebookMetadata]);

  const loadContents = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    const requestSeq = contentsRequestSeqRef.current + 1;
    contentsRequestSeqRef.current = requestSeq;
    const snapshot = await fetchSharedModelSnapshot();
    if (requestSeq !== contentsRequestSeqRef.current) {
      return;
    }
    const nextVersion = typeof snapshot.document_version === 'number' ? snapshot.document_version : 0;
    const nextCells = Array.isArray(snapshot.cells) ? snapshot.cells : [];
    const nextNotebookMetadata = normalizeNotebookMetadata(snapshot.notebook_metadata);
    const nextTrust = trustSnapshotFromPayload(snapshot, nextCells);
    const nextTrustSignature = trustSignature(nextTrust, nextCells);
    const nextNotebookMetadataSignature = notebookMetadataSignature(nextNotebookMetadata);
    if (nextVersion < appliedDocumentVersionRef.current) {
      return;
    }
    if (
      nextVersion === appliedDocumentVersionRef.current
      && nextTrustSignature === appliedTrustSignatureRef.current
      && nextNotebookMetadataSignature === appliedNotebookMetadataSignatureRef.current
    ) {
      return;
    }
    if (nextNotebookMetadataSignature !== appliedNotebookMetadataSignatureRef.current) {
      // Notebook metadata changed — full reset needed
      appliedDocumentVersionRef.current = nextVersion;
      appliedTrustSignatureRef.current = nextTrustSignature;
      appliedNotebookMetadataSignatureRef.current = nextNotebookMetadataSignature;
      lastSyncedCellsRef.current = cloneCells(nextCells);
      setInitialCells(nextCells);
      setInitialDocumentVersion(nextVersion);
      setInitialNotebookMetadata(nextNotebookMetadata);
      setInitialTrustSnapshot(nextTrust);
      setTrustSnapshot(nextTrust);
      return;
    }
    applyRemoteCells(nextCells);
    appliedDocumentVersionRef.current = nextVersion;
    appliedTrustSignatureRef.current = nextTrustSignature;
    appliedNotebookMetadataSignatureRef.current = nextNotebookMetadataSignature;
    setTrustSnapshot(nextTrust);
  }, [applyRemoteCells, fetchSharedModelSnapshot, currentNotebookPath]);

  const clearCellOutputs = useCallback((cellId: string) => {
    const model = modelRef.current;
    if (!model) return;
    const idx = findCellIndexById(model, cellId);
    if (idx < 0) return;
    const cellModel = model.cells.get(idx);
    if (!cellModel || cellModel.type !== 'code') return;
    const codeModel = cellModel as unknown as { outputs: { clear: (wait?: boolean) => void }; executionCount: number | null };
    codeModel.outputs.clear();
    codeModel.executionCount = null;
    displayIdMapRef.current.delete(cellId);
  }, []);

  const clearAllOutputs = useCallback(() => {
    const model = modelRef.current;
    if (!model) return;
    for (let i = 0; i < model.cells.length; i++) {
      const cellModel = model.cells.get(i);
      if (!cellModel || cellModel.type !== 'code') continue;
      const codeModel = cellModel as unknown as { outputs: { clear: (wait?: boolean) => void }; executionCount: number | null };
      codeModel.outputs.clear();
      codeModel.executionCount = null;
    }
    displayIdMapRef.current.clear();
    executedCellIdsRef.current.clear();
    setCellModelVersion((v) => v + 1);
    showToolbarStatus('Cleared all outputs');
  }, [showToolbarStatus]);

  const applyIOPubMessage = useCallback((msg: any) => {
    const model = modelRef.current;
    if (!model) return;
    const cellId: string = msg.cell_id;
    const msgType: string | undefined = msg.data?.msg_type;
    const iopubOutput = msg.data?.iopub_output;
    const displayId: string | undefined = msg.data?.display_id;
    if (!cellId || !msgType || !iopubOutput) return;

    const idx = findCellIndexById(model, cellId);
    if (idx < 0) return;
    const cellModel = model.cells.get(idx);
    if (!cellModel || cellModel.type !== 'code') return;
    const codeModel = cellModel as unknown as {
      outputs: { add: (output: any) => void; set: (index: number, output: any) => void; clear: (wait?: boolean) => void; length: number };
    };

    switch (msgType) {
      case 'stream':
      case 'execute_result':
      case 'error':
        codeModel.outputs.add(iopubOutput);
        break;
      case 'display_data': {
        // If we already have this display_id, update in-place instead of adding
        if (displayId) {
          const cellMap = displayIdMapRef.current.get(cellId);
          const existing = cellMap?.get(displayId);
          if (existing && existing.length > 0) {
            for (const index of existing) {
              codeModel.outputs.set(index, iopubOutput);
            }
            break;
          }
        }
        codeModel.outputs.add(iopubOutput);
        if (displayId) {
          let cellMap = displayIdMapRef.current.get(cellId);
          if (!cellMap) { cellMap = new Map(); displayIdMapRef.current.set(cellId, cellMap); }
          const targets = cellMap.get(displayId) || [];
          targets.push(codeModel.outputs.length - 1);
          cellMap.set(displayId, targets);
        }
        break;
      }
      case 'update_display_data': {
        if (!displayId) break;
        const cellMap = displayIdMapRef.current.get(cellId);
        const targets = cellMap?.get(displayId);
        if (targets) {
          const output = { ...iopubOutput, output_type: 'display_data' };
          for (const index of targets) {
            codeModel.outputs.set(index, output);
          }
        }
        break;
      }
      case 'clear_output':
        codeModel.outputs.clear(msg.data?.wait ?? false);
        displayIdMapRef.current.delete(cellId);
        break;
    }
    setCellModelVersion((v) => v + 1);
  }, []);

  const trustNotebook = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    setError(null);
    showToolbarStatus('Trusting notebook...', 2200);
    await postJson('/api/standalone/notebook/trust', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    const snapshot = await fetchSharedModelSnapshot();
    const nextCells = Array.isArray(snapshot.cells) ? snapshot.cells : [];
    const nextVersion = typeof snapshot.document_version === 'number' ? snapshot.document_version : 0;
    const nextNotebookMetadata = normalizeNotebookMetadata(snapshot.notebook_metadata);
    const nextTrust = trustSnapshotFromPayload(snapshot, nextCells);
    appliedDocumentVersionRef.current = nextVersion;
    appliedTrustSignatureRef.current = trustSignature(nextTrust, nextCells);
    appliedNotebookMetadataSignatureRef.current = notebookMetadataSignature(nextNotebookMetadata);
    lastSyncedCellsRef.current = cloneCells(nextCells);
    setInitialCells(nextCells);
    setInitialDocumentVersion(nextVersion);
    setInitialNotebookMetadata(nextNotebookMetadata);
    setInitialTrustSnapshot(nextTrust);
    setTrustSnapshot(nextTrust);
    showToolbarStatus('Notebook trusted');
  }, [fetchSharedModelSnapshot, currentNotebookPath, showToolbarStatus]);

  const loadRuntime = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    const [runtimeResult, statusResult] = await Promise.all([
      postJson<{
        runtime?: {
          busy?: boolean;
        } | null;
        runtime_record?: {
          label?: string;
        } | null;
      }>('/api/standalone/notebook/runtime', {
        client_id: PREVIEW_CLIENT_ID,
        path: currentNotebookPath,
      }),
      postJson<{
        running?: Array<{ cell_id?: string }>;
        queued?: Array<{ cell_id?: string }>;
      }>('/api/standalone/notebook/status', {
        client_id: PREVIEW_CLIENT_ID,
        path: currentNotebookPath,
      }),
    ]);

    const nextRuntime: RuntimeSnapshot = {
      busy: Boolean(runtimeResult.runtime?.busy),
      kernel_label: runtimeResult.runtime_record?.label ?? '',
      kernel_generation: typeof (runtimeResult.runtime_record as any)?.kernel_generation === 'number'
        ? (runtimeResult.runtime_record as any).kernel_generation : 0,
      current_execution: null,
      running_cell_ids: Array.isArray(statusResult.running)
        ? statusResult.running.map((entry) => entry.cell_id).filter((cellId): cellId is string => typeof cellId === 'string')
        : [],
      queued_cell_ids: Array.isArray(statusResult.queued)
        ? statusResult.queued.map((entry) => entry.cell_id).filter((cellId): cellId is string => typeof cellId === 'string')
        : [],
    };

    runtimeBusyRef.current = nextRuntime.busy;
    setRuntime(nextRuntime);
  }, [currentNotebookPath]);

  const loadWorkspaceTree = useCallback(async () => {
    const result = await postJson<{
      root?: WorkspaceTreeNode | null;
      workspace_name?: string;
      selected_path?: string | null;
    }>('/api/standalone/workspace-tree', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    setWorkspaceTree(result.root ?? null);
    setWorkspaceName(result.workspace_name ?? '');
    const selectedPath = result.selected_path ?? currentNotebookPath;
    if (selectedPath) {
      setExplorerExpandedPaths((current) => new Set([...current, ...collectExplorerAncestorPaths(selectedPath)]));
    }
  }, [currentNotebookPath]);

  const loadKernels = useCallback(async () => {
    const result = await postJson<{
      kernels?: Kernel[];
      preferred_kernel?: { id?: string | null } | null;
    }>('/api/standalone/kernels', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    setKernels(result.kernels ?? []);
    setSelectedKernelId((current) => {
      if (current && (result.kernels ?? []).some((kernel) => kernel.id === current)) {
        return current;
      }
      return result.preferred_kernel?.id ?? '';
    });
  }, [currentNotebookPath]);

  const selectKernel = useCallback(async (kernelId: string) => {
    setSelectedKernelId(kernelId);
    setKernelMenuOpen(false);
    await postJson('/api/standalone/notebook/select-kernel', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
      kernel_id: kernelId,
    });
    await Promise.all([loadKernels(), loadRuntime()]);
    showToolbarStatus('Kernel selected');
  }, [currentNotebookPath, loadKernels, loadRuntime, showToolbarStatus]);

  useEffect(() => {
    if (!kernelMenuOpen) {
      return;
    }
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target;
      if (target instanceof Node && kernelMenuRef.current?.contains(target)) {
        return;
      }
      setKernelMenuOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [kernelMenuOpen]);

  const flushAutosave = useCallback(async (options?: { announce?: boolean }) => {
    const model = modelRef.current;
    if (!model || !currentNotebookPath) {
      return true;
    }

    const notebook = notebookRef.current;
    const lastSynced = lastSyncedCellsRef.current;

    if (!notebook) {
      return true;
    }

    const currentCells = notebookCellsFromWidgets(notebook, model);

    const sameStructure = currentCells.length === lastSynced.length
      && currentCells.every((cell, index) => cell.cell_id === lastSynced[index]?.cell_id);

    if (!sameStructure) {
      showToolbarStatus('Cell add/move/delete syncing is not wired yet.', 2200);
      return false;
    }

    const changes = currentCells.flatMap((cell, index) => (
      cell.source !== lastSynced[index]?.source
        ? [{ cell_id: cell.cell_id, cell_index: index, source: cell.source }]
        : []
    ));

    if (changes.length === 0) {
      model.dirty = false;
      return true;
    }

    await postJson('/api/standalone/notebook/edit', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
      operations: buildReplaceSourceOperations(changes),
    });

    lastSyncedCellsRef.current = cloneCells(currentCells);
    model.dirty = false;
    await loadContents();
    if (options?.announce) {
      showToolbarStatus('Saved');
    }
    return true;
  }, [loadContents, currentNotebookPath, showToolbarStatus]);

  const syncSnapshotFromModel = useCallback(() => {
    const model = modelRef.current;
    if (!model) {
      return;
    }
    const notebook = notebookRef.current;
    if (!notebook) {
      return;
    }
    lastSyncedCellsRef.current = cloneCells(notebookCellsFromWidgets(notebook, model));
    model.dirty = false;
  }, []);

  const restartAndRunAll = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    const saved = await flushAutosave();
    if (!saved) {
      return;
    }
    setError(null);
    pendingExecutionRef.current = true;
    await postJson('/api/standalone/notebook/restart-and-run-all-async', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    await loadRuntime();
  }, [currentNotebookPath, flushAutosave, loadRuntime]);

  const persistStructureOperations = useCallback(async (
    operations: NotebookEditOperation[],
    successLabel: string,
    options?: {
      inverseOperations?: NotebookEditOperation[];
      recordUndo?: boolean;
    },
  ) => {
    if (!currentNotebookPath || operations.length === 0) {
      return;
    }
    await postJson('/api/standalone/notebook/edit', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
      operations,
    });
    syncSnapshotFromModel();
    await loadContents();
    if (options?.recordUndo !== false && options?.inverseOperations && options.inverseOperations.length > 0) {
      structureUndoStackRef.current.push({
        inverseOperations: options.inverseOperations,
        forwardOperations: operations,
        successLabel,
      });
      structureRedoStackRef.current = [];
    }
    showToolbarStatus(successLabel);
  }, [loadContents, currentNotebookPath, showToolbarStatus, syncSnapshotFromModel]);

  const scheduleAutosave = useCallback(() => {
    if (autoSaveTimerRef.current != null) {
      window.clearTimeout(autoSaveTimerRef.current);
    }
    autoSaveTimerRef.current = window.setTimeout(() => {
      void flushAutosave().catch((err) => {
        setError(err instanceof Error ? err.message : String(err));
      });
      autoSaveTimerRef.current = null;
    }, AUTOSAVE_DELAY_MS);
  }, [flushAutosave]);

  const insertRelativeCell = useCallback(async (position: 'above' | 'below', options?: { switchToEdit?: boolean }) => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model) {
      return;
    }
    const beforeCells = notebookCellsFromWidgets(notebook, model);
    const beforeIds = new Set(beforeCells.map((cell) => cell.cell_id));

    applyingNotebookActionRef.current = true;
    try {
      if (position === 'above') {
        NotebookActions.insertAbove(notebook);
      } else {
        NotebookActions.insertBelow(notebook);
      }
      if (options?.switchToEdit) {
        notebook.mode = 'edit';
        focusActiveCellEditor(notebook);
      }
    } finally {
      applyingNotebookActionRef.current = false;
    }

    const afterCells = notebookCellsFromWidgets(notebook, model);
    const insertedIndex = afterCells.findIndex((cell) => !beforeIds.has(cell.cell_id));
    if (insertedIndex < 0) {
      return;
    }
    const insertedCell = afterCells[insertedIndex];
    const successLabel = position === 'above' ? 'Inserted cell above' : 'Inserted cell below';
    const forwardOps: NotebookEditOperation[] = [
      {
        op: 'insert',
        at_index: insertedIndex,
        cell_type: insertedCell.cell_type === 'markdown' ? 'markdown' : 'code',
        cell_id: insertedCell.cell_id,
        source: insertedCell.source,
        metadata: insertedCell.metadata ?? {},
        outputs: insertedCell.outputs ?? [],
        execution_count: insertedCell.execution_count ?? null,
      },
    ];
    const undoEntry = {
      inverseOperations: [
        {
          op: 'delete',
          cell_index: insertedIndex,
        },
      ],
      forwardOperations: forwardOps,
      successLabel,
    } satisfies StructureUndoEntry;
    structureUndoStackRef.current.push(undoEntry);
    structureRedoStackRef.current = [];
    try {
      await persistStructureOperations(forwardOps, successLabel, {
        recordUndo: false,
      });
    } catch (error) {
      if (structureUndoStackRef.current.at(-1) === undoEntry) {
        structureUndoStackRef.current.pop();
      }
      throw error;
    }
  }, [persistStructureOperations]);

  const deleteSelectedCells = useCallback(async () => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model) {
      return;
    }
    const selectedIndices = getSelectedCellIndices(notebook);
    if (selectedIndices.length === 0) {
      return;
    }
    const beforeCells = notebookCellsFromWidgets(notebook, model);
    const deleteOperations = selectedIndices
      .map((index) => beforeCells[index])
      .filter(Boolean)
      .map((cell) => ({ op: 'delete', cell_id: cell.cell_id }));
    const inverseOperations = selectedIndices
      .map((index) => ({ index, cell: beforeCells[index] }))
      .filter((entry): entry is { index: number; cell: NotebookCell } => Boolean(entry.cell))
      .map(({ index, cell }) => ({
        op: 'insert',
        at_index: index,
        cell_type: cell.cell_type === 'markdown' ? 'markdown' : 'code',
        cell_id: cell.cell_id,
        source: cell.source,
        metadata: cell.metadata ?? {},
        outputs: cell.outputs ?? [],
        execution_count: cell.execution_count ?? null,
      }));

    applyingNotebookActionRef.current = true;
    try {
      NotebookActions.deleteCells(notebook);
    } finally {
      applyingNotebookActionRef.current = false;
    }

    await persistStructureOperations(deleteOperations, deleteOperations.length === 1 ? 'Deleted cell' : 'Deleted cells', {
      inverseOperations,
    });
  }, [persistStructureOperations]);

  const changeSelectedCellType = useCallback(async (nextType: 'code' | 'markdown') => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model) {
      return;
    }
    const selectedIndices = getSelectedCellIndices(notebook);
    if (selectedIndices.length === 0) {
      return;
    }
    const beforeCells = notebookCellsFromWidgets(notebook, model);
    const selectedIds = selectedIndices
      .map((index) => beforeCells[index]?.cell_id)
      .filter((cellId): cellId is string => typeof cellId === 'string');

    applyingNotebookActionRef.current = true;
    try {
      NotebookActions.changeCellType(notebook, nextType, nullTranslator);
    } finally {
      applyingNotebookActionRef.current = false;
    }

    const afterCells = notebookCellsFromWidgets(notebook, model);
    const operations = selectedIds.map((cellId) => {
      const nextCell = afterCells.find((cell) => cell.cell_id === cellId);
      return {
        op: 'change-cell-type',
        cell_id: cellId,
        cell_type: nextType,
        source: nextCell?.source ?? '',
      };
    });
    const inverseOperations = selectedIds.map((cellId) => {
      const previousCell = beforeCells.find((cell) => cell.cell_id === cellId);
      return {
        op: 'change-cell-type',
        cell_id: cellId,
        cell_type: previousCell?.cell_type === 'markdown' ? 'markdown' : 'code',
        source: previousCell?.source ?? '',
      };
    });
    await persistStructureOperations(operations, nextType === 'markdown' ? 'Changed cell to Markdown' : 'Changed cell to Code', {
      inverseOperations,
    });
  }, [persistStructureOperations]);

  const moveSelectedCell = useCallback(async (direction: 'up' | 'down') => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model) {
      return;
    }

    const selectedIndices = getSelectedCellIndices(notebook);
    if (selectedIndices.length !== 1) {
      return;
    }

    const currentIndex = selectedIndices[0];
    if ((direction === 'up' && currentIndex <= 0) || (direction === 'down' && currentIndex >= model.cells.length - 1)) {
      return;
    }

    const beforeCells = notebookCellsFromWidgets(notebook, model);
    const targetCell = beforeCells[currentIndex];
    if (!targetCell) {
      return;
    }

    applyingNotebookActionRef.current = true;
    try {
      if (direction === 'up') {
        NotebookActions.moveUp(notebook);
      } else {
        NotebookActions.moveDown(notebook);
      }
    } finally {
      applyingNotebookActionRef.current = false;
    }

    const afterCells = notebookCellsFromWidgets(notebook, model);
    const nextIndex = afterCells.findIndex((cell) => cell.cell_id === targetCell.cell_id);
    if (nextIndex < 0 || nextIndex === currentIndex) {
      return;
    }

    await persistStructureOperations([
      {
        op: 'move',
        cell_id: targetCell.cell_id,
        to_index: nextIndex,
      },
    ], direction === 'up' ? 'Moved cell up' : 'Moved cell down', {
      inverseOperations: [
        {
          op: 'move',
          cell_id: targetCell.cell_id,
          to_index: currentIndex,
        },
      ],
    });
  }, [persistStructureOperations]);

  const undoNotebookStructure = useCallback(async () => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model) {
      return;
    }

    const saved = await flushAutosave();
    if (!saved) {
      return;
    }

    const undoEntry = structureUndoStackRef.current.pop();
    if (!undoEntry) {
      showToolbarStatus('Nothing to undo', 1200);
      focusNotebookCommandSelection(notebook);
      return;
    }

    applyingNotebookActionRef.current = true;
    try {
      const currentCells = notebookCellsFromWidgets(notebook, model);
      const nextCells = applyNotebookOperationsToCells(currentCells, undoEntry.inverseOperations);
      applyRemoteCells(nextCells);
    } finally {
      applyingNotebookActionRef.current = false;
    }

    if (undoEntry.forwardOperations) {
      structureRedoStackRef.current.push({
        inverseOperations: undoEntry.inverseOperations,
        forwardOperations: undoEntry.forwardOperations,
        successLabel: undoEntry.successLabel,
      });
    }

    await persistStructureOperations(
      undoEntry.inverseOperations,
      `Undid ${undoEntry.successLabel.toLowerCase()}`,
      { recordUndo: false },
    );
    focusNotebookCommandSelection(notebook);
  }, [applyRemoteCells, flushAutosave, persistStructureOperations, showToolbarStatus]);

  const redoNotebookStructure = useCallback(async () => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model) {
      return;
    }

    const saved = await flushAutosave();
    if (!saved) {
      return;
    }

    const redoEntry = structureRedoStackRef.current.pop();
    if (!redoEntry) {
      showToolbarStatus('Nothing to redo', 1200);
      focusNotebookCommandSelection(notebook);
      return;
    }

    applyingNotebookActionRef.current = true;
    try {
      const currentCells = notebookCellsFromWidgets(notebook, model);
      const nextCells = applyNotebookOperationsToCells(currentCells, redoEntry.forwardOperations!);
      applyRemoteCells(nextCells);
    } finally {
      applyingNotebookActionRef.current = false;
    }

    structureUndoStackRef.current.push({
      inverseOperations: redoEntry.inverseOperations,
      forwardOperations: redoEntry.forwardOperations,
      successLabel: redoEntry.successLabel,
    });

    await persistStructureOperations(
      redoEntry.forwardOperations!,
      `Redid ${redoEntry.successLabel.toLowerCase()}`,
      { recordUndo: false },
    );
    focusNotebookCommandSelection(notebook);
  }, [applyRemoteCells, flushAutosave, persistStructureOperations, showToolbarStatus]);

  const executeActiveCell = useCallback(async (mode: 'run' | 'advance' | 'insert') => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model || !currentNotebookPath) {
      return;
    }

    const activeIndex = notebook.activeCellIndex;
    const currentCells = notebookCellsFromWidgets(notebook, model);
    const activeCell = currentCells[activeIndex];
    if (!activeCell) {
      return;
    }

    if (activeCell.cell_type === 'markdown') {
      const activeWidget = notebook.activeCell;
      if (activeWidget && 'rendered' in activeWidget) {
        (activeWidget as unknown as { rendered: boolean }).rendered = true;
      }
      if (mode === 'insert') {
        await insertRelativeCell('below', { switchToEdit: true });
      } else if (mode === 'advance') {
        if (activeIndex < currentCells.length - 1) {
          notebook.activeCellIndex = activeIndex + 1;
          focusNotebookCommandSelection(notebook);
        } else {
          await insertRelativeCell('below');
          focusNotebookCommandSelection(notebook);
        }
      }
      return;
    }

    if (activeCell.cell_type !== 'code') {
      return;
    }

    // Skip if cell is already running or queued
    const cellId = activeCell.cell_id;
    if (runtime.running_cell_ids.includes(cellId) || runtime.queued_cell_ids.includes(cellId)) {
      // Still advance if requested
      if (mode === 'advance' && activeIndex < currentCells.length - 1) {
        notebook.activeCellIndex = activeIndex + 1;
        focusNotebookCommandSelection(notebook);
      } else if (mode === 'insert') {
        await insertRelativeCell('below', { switchToEdit: true });
      }
      return;
    }

    const saved = await flushAutosave();
    if (!saved) {
      return;
    }

    setError(null);
    pendingExecutionRef.current = true;

    // Advance to next cell IMMEDIATELY — don't wait for execution
    if (mode === 'insert') {
      await insertRelativeCell('below', { switchToEdit: true });
    } else if (mode === 'advance') {
      if (activeIndex < currentCells.length - 1) {
        notebook.activeCellIndex = activeIndex + 1;
        focusNotebookCommandSelection(notebook);
      } else {
        await insertRelativeCell('below');
        focusNotebookCommandSelection(notebook);
      }
    }

    // Fire async execution — returns immediately, output arrives via WebSocket
    postJson('/api/standalone/notebook/execute-cell-async', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
      cell_id: activeCell.cell_id,
      cell_index: activeIndex,
    }).then(() => loadRuntime()).catch(() => {});
  }, [flushAutosave, insertRelativeCell, loadContents, loadRuntime, currentNotebookPath]);

  const executeAll = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    const saved = await flushAutosave();
    if (!saved) {
      return;
    }
    setError(null);
    pendingExecutionRef.current = true;
    await postJson('/api/standalone/notebook/execute-all-async', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    await loadRuntime();
  }, [flushAutosave, loadRuntime, currentNotebookPath]);

  const restartKernel = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    setError(null);
    showToolbarStatus('Restarting kernel...', 2200);
    await postJson('/api/standalone/notebook/restart', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    pendingExecutionRef.current = false;
    await Promise.all([loadRuntime(), loadContents()]);
  }, [loadContents, loadRuntime, currentNotebookPath, showToolbarStatus]);

  const interruptExecution = useCallback(async () => {
    if (!currentNotebookPath) {
      return;
    }
    setError(null);
    showToolbarStatus('Interrupting...', 2200);
    await postJson('/api/standalone/notebook/interrupt', {
      client_id: PREVIEW_CLIENT_ID,
      path: currentNotebookPath,
    });
    pendingExecutionRef.current = false;
    await Promise.all([loadRuntime(), loadContents()]);
  }, [loadContents, loadRuntime, currentNotebookPath, showToolbarStatus]);

  useEffect(() => {
    const mountNode = mountRef.current;
    if (!mountNode || !initialCells) {
      return;
    }
    const restoreWarningFilter = installBenignYjsWarningFilter();
    let disposed = false;

    const translator = nullTranslator;
    const languages = new EditorLanguageRegistry();
    for (const language of EditorLanguageRegistry.getDefaultLanguages(translator)) {
      languages.addLanguage(language);
    }
    const extensions = new EditorExtensionRegistry();
    for (const extension of EditorExtensionRegistry.getDefaultExtensions({ translator })) {
      extensions.addExtension(extension);
    }
    extensions.addExtension({
      name: 'agent-repl-autocomplete',
      default: true,
      factory: () => ({
        instance: (enabled: boolean) => (enabled
          ? autocompletion({
            activateOnTyping: true,
            maxRenderedOptions: 12,
            override: [localCompletionSource, globalCompletion],
          })
          : []),
        reconfigure: () => null,
      }),
    });
    extensions.addExtension({
      name: 'agent-repl-search',
      default: true,
      factory: () => ({
        instance: () => searchExtension(),
        reconfigure: () => null,
      }),
    });
    const editorFactory = new CodeMirrorEditorFactory({ languages, extensions, translator });
    const mimeTypeService = new CodeMirrorMimeTypeService(languages);
    const widgetManager = new HTMLManager();
    const rendermime = widgetManager.renderMime;
    const inlineEditorFactory = editorFactory.newInlineEditor.bind(editorFactory);
    const contentFactory = new Notebook.ContentFactory({
      editorFactory: inlineEditorFactory,
    });
    const model = new NotebookModel({ translator });
    model.fromJSON(toNotebookJson(initialCells, initialNotebookMetadata));
    appliedDocumentVersionRef.current = initialDocumentVersion;
    appliedTrustSignatureRef.current = trustSignature(initialTrustSnapshot, initialCells);
    appliedNotebookMetadataSignatureRef.current = notebookMetadataSignature(initialNotebookMetadata);
    const notebook = new Notebook({
      rendermime,
      contentFactory,
      mimeTypeService,
      translator,
      notebookConfig: {
        ...StaticNotebook.defaultNotebookConfig,
        windowingMode: 'none',
      },
    });

    model.readOnly = false;
    notebook.model = model;
    for (let index = 0; index < initialCells.length; index += 1) {
      const cell = initialCells[index];
      const cellModel = model.cells.get(index);
      if (!cellModel || cellModel.type !== 'code' || cell.cell_type !== 'code') {
        continue;
      }
      (cellModel as unknown as { trusted: boolean }).trusted = cell.trusted === true;
    }
    notebook.mode = 'command';
    notebook.node.style.height = 'auto';
    notebook.node.style.overflow = 'visible';
    notebook.addClass('jp-NotebookPanel-notebook');
    notebook.addClass('agent-repl-jupyterlab-notebook');
    notebook.node.tabIndex = 0;

    const commands = new CommandRegistry();
    const addCommand = (id: string, execute: () => Promise<unknown> | unknown) => {
      commands.addCommand(id, { execute });
    };
    const wrapCommand = (execute: () => Promise<unknown> | unknown) => async () => {
      pendingNotebookCommandCountRef.current += 1;
      try {
        return await execute();
      } catch (error) {
        setError(error instanceof Error ? error.message : String(error));
        throw error;
      } finally {
        pendingNotebookCommandCountRef.current = Math.max(0, pendingNotebookCommandCountRef.current - 1);
      }
    };

    addCommand(COMMAND_IDS.enterEditMode, wrapCommand(() => {
      notebook.mode = 'edit';
      focusActiveCellEditor(notebook);
    }));
    addCommand(COMMAND_IDS.enterCommandMode, wrapCommand(() => {
      focusNotebookCommandSelection(notebook);
    }));
    addCommand(COMMAND_IDS.selectAll, wrapCommand(() => {
      NotebookActions.selectAll(notebook);
      focusNotebookCommandSelection(notebook);
    }));
    addCommand(COMMAND_IDS.selectAbove, wrapCommand(() => NotebookActions.selectAbove(notebook)));
    addCommand(COMMAND_IDS.selectBelow, wrapCommand(() => NotebookActions.selectBelow(notebook)));
    addCommand(COMMAND_IDS.extendSelectionAbove, wrapCommand(() => NotebookActions.extendSelectionAbove(notebook)));
    addCommand(COMMAND_IDS.extendSelectionBelow, wrapCommand(() => NotebookActions.extendSelectionBelow(notebook)));
    addCommand(COMMAND_IDS.insertAbove, wrapCommand(() => insertRelativeCell('above', { switchToEdit: true })));
    addCommand(COMMAND_IDS.insertBelow, wrapCommand(() => insertRelativeCell('below', { switchToEdit: true })));
    addCommand(COMMAND_IDS.changeToCode, wrapCommand(() => changeSelectedCellType('code')));
    addCommand(COMMAND_IDS.changeToMarkdown, wrapCommand(() => changeSelectedCellType('markdown')));
    addCommand(COMMAND_IDS.deleteCells, wrapCommand(() => deleteSelectedCells()));
    addCommand(COMMAND_IDS.moveUp, wrapCommand(() => moveSelectedCell('up')));
    addCommand(COMMAND_IDS.moveDown, wrapCommand(() => moveSelectedCell('down')));
    addCommand(COMMAND_IDS.runCell, wrapCommand(() => executeActiveCell('run')));
    addCommand(COMMAND_IDS.runAndAdvance, wrapCommand(() => executeActiveCell('advance')));
    addCommand(COMMAND_IDS.runAndInsertBelow, wrapCommand(() => executeActiveCell('insert')));
    addCommand(COMMAND_IDS.runAll, wrapCommand(() => executeAll()));
    addCommand(COMMAND_IDS.save, wrapCommand(() => flushAutosave({ announce: true })));
    addCommand(COMMAND_IDS.undoNotebook, wrapCommand(() => undoNotebookStructure()));
    addCommand(COMMAND_IDS.redoNotebook, wrapCommand(() => redoNotebookStructure()));
    addCommand(COMMAND_IDS.clearAllOutputs, wrapCommand(() => clearAllOutputs()));
    addCommand(COMMAND_IDS.splitCell, wrapCommand(() => {
      NotebookActions.splitCell(notebook);
    }));
    addCommand(COMMAND_IDS.mergeCells, wrapCommand(() => {
      NotebookActions.mergeCells(notebook);
    }));
    addCommand(COMMAND_IDS.copyCells, wrapCommand(() => {
      NotebookActions.copy(notebook);
    }));
    addCommand(COMMAND_IDS.pasteCells, wrapCommand(() => {
      NotebookActions.paste(notebook);
    }));

    const bindCommand = (command: string, keys: string[] | string, selector: string) => {
      commands.addKeyBinding({
        command,
        keys: Array.isArray(keys) ? keys : [keys],
        selector,
      });
    };

    bindCommand(COMMAND_IDS.runAndAdvance, 'Shift Enter', NOTEBOOK_COMMAND_SELECTOR);
    bindCommand(COMMAND_IDS.runCell, 'Accel Enter', NOTEBOOK_COMMAND_SELECTOR);
    bindCommand(COMMAND_IDS.runAndInsertBelow, 'Alt Enter', NOTEBOOK_COMMAND_SELECTOR);
    bindCommand(COMMAND_IDS.save, 'Accel S', NOTEBOOK_COMMAND_SELECTOR);
    bindCommand(COMMAND_IDS.enterCommandMode, 'Escape', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-editMode`);
    bindCommand(COMMAND_IDS.enterEditMode, 'Enter', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.selectAll, 'Accel A', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.selectAbove, 'ArrowUp', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.selectAbove, 'K', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.selectBelow, 'ArrowDown', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.selectBelow, 'J', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.extendSelectionAbove, 'Shift ArrowUp', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.extendSelectionBelow, 'Shift ArrowDown', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.insertAbove, 'A', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.insertBelow, 'B', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.changeToMarkdown, 'M', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.changeToCode, 'Y', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.deleteCells, ['D', 'D'], `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.undoNotebook, 'Z', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.redoNotebook, 'Shift Z', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.moveUp, 'Accel Shift ArrowUp', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.moveDown, 'Accel Shift ArrowDown', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.splitCell, 'Accel Shift -', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-editMode`);
    bindCommand(COMMAND_IDS.mergeCells, 'Shift M', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.copyCells, 'C', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);
    bindCommand(COMMAND_IDS.pasteCells, 'V', `${NOTEBOOK_COMMAND_SELECTOR}.jp-mod-commandMode`);

    const debugWindow = window as JupyterLabPreviewDebugWindow;
    debugWindow.__agentReplJupyterLab = {
      executeCommand: (commandId: string) => commands.execute(commandId),
      focusNotebook: () => focusNotebookCommandSelection(notebook),
      getPendingCommandCount: () => pendingNotebookCommandCountRef.current,
      getUndoDepth: () => structureUndoStackRef.current.length,
      getRedoDepth: () => structureRedoStackRef.current.length,
    };

    const handleModelChanged = () => {
      if (applyingRemoteRef.current || applyingNotebookActionRef.current) {
        return;
      }
      scheduleAutosave();
    };
    const configureNotebookEditors = () => {
      for (const widget of notebook.widgets) {
        if (widget.model.type !== 'code' || !widget.editor) {
          continue;
        }
        configureEditor(widget.editor);
      }
    };
    const cellContentListeners = new Map<string, { model: { contentChanged: { disconnect: (cb: () => void, ctx?: unknown) => void } }, callback: () => void }>();
    const bindCellContentListeners = () => {
      configureNotebookEditors();
      const nextCellIds = new Set<string>();
      for (const widget of notebook.widgets) {
        const cellModel = widget.model as {
          id?: string;
          contentChanged?: {
            connect: (cb: () => void, ctx?: unknown) => void;
            disconnect: (cb: () => void, ctx?: unknown) => void;
          };
          sharedModel?: {
            getId?: () => string;
          };
        };
        const cellId = typeof cellModel.sharedModel?.getId === 'function'
          ? cellModel.sharedModel.getId()
          : (cellModel.id ?? `cell-${nextCellIds.size}`);
        nextCellIds.add(cellId);
        if (cellContentListeners.has(cellId) || !cellModel.contentChanged) {
          continue;
        }
        const callback = () => {
          handleModelChanged();
        };
        cellModel.contentChanged.connect(callback);
        cellContentListeners.set(cellId, { model: cellModel as { contentChanged: { disconnect: (cb: () => void, ctx?: unknown) => void } }, callback });
      }

      for (const [cellId, listener] of cellContentListeners) {
        if (nextCellIds.has(cellId)) {
          continue;
        }
        listener.model.contentChanged.disconnect(listener.callback);
        cellContentListeners.delete(cellId);
      }
    };
    const handleCellsChanged = () => {
      bindCellContentListeners();
      handleModelChanged();
    };
    const handleDocumentKeyDown = (event: KeyboardEvent) => {
      // Cmd+S always saves — bypass all routing checks
      const accelKey = event.metaKey || event.ctrlKey;
      const normalizedKeyEarly = event.key.length === 1 ? event.key.toLowerCase() : event.key;
      if (accelKey && normalizedKeyEarly === 's') {
        event.preventDefault();
        void commands.execute(COMMAND_IDS.save);
        return;
      }
      const target = event.target;
      if (!shouldRouteNotebookEvent(target, mountNode, notebook)) {
        return;
      }
      if (!shouldHandleNotebookShortcut(event, notebook)) {
        return;
      }
      const normalizedKey = normalizedKeyEarly;
      const isNotebookTarget = target === document.body
        || target === document.documentElement
        || (target instanceof HTMLElement && (
          mountNode.contains(target)
          || target.closest('.agent-repl-jupyterlab-notebook')
        ));
      if (isNotebookTarget) {
        const accel = event.metaKey || event.ctrlKey;
        const directCommand = (() => {
          if (notebook.mode === 'edit') {
            if (normalizedKey === 'Escape') {
              return COMMAND_IDS.enterCommandMode;
            }
            if (normalizedKey === 'Enter' && event.shiftKey) {
              return COMMAND_IDS.runAndAdvance;
            }
            if (normalizedKey === 'Enter' && accel) {
              return COMMAND_IDS.runCell;
            }
            if (normalizedKey === 'Enter' && event.altKey) {
              return COMMAND_IDS.runAndInsertBelow;
            }
            if (accel && event.shiftKey && (normalizedKey === '-' || normalizedKey === 'minus')) {
              return COMMAND_IDS.splitCell;
            }
            return null;
          }
          if (normalizedKey === 'Enter' && event.shiftKey) {
            return COMMAND_IDS.runAndAdvance;
          }
          if (normalizedKey === 'Enter' && accel) {
            return COMMAND_IDS.runCell;
          }
          if (normalizedKey === 'Enter' && event.altKey) {
            return COMMAND_IDS.runAndInsertBelow;
          }
          if (accel && normalizedKey === 'a') {
            return COMMAND_IDS.selectAll;
          }
          if (normalizedKey === 'Enter') {
            return COMMAND_IDS.enterEditMode;
          }
          if (normalizedKey === 'ArrowUp' || normalizedKey === 'k') {
            return COMMAND_IDS.selectAbove;
          }
          if (normalizedKey === 'ArrowDown' || normalizedKey === 'j') {
            return COMMAND_IDS.selectBelow;
          }
          if (normalizedKey === 'a') {
            return COMMAND_IDS.insertAbove;
          }
          if (normalizedKey === 'b') {
            return COMMAND_IDS.insertBelow;
          }
          if (normalizedKey === 'm' && event.shiftKey) {
            return COMMAND_IDS.mergeCells;
          }
          if (normalizedKey === 'm' && !event.shiftKey) {
            return COMMAND_IDS.changeToMarkdown;
          }
          if (normalizedKey === 'y') {
            return COMMAND_IDS.changeToCode;
          }
          if (normalizedKey === 'z' && !event.shiftKey) {
            return COMMAND_IDS.undoNotebook;
          }
          if (normalizedKey === 'z' && event.shiftKey) {
            return COMMAND_IDS.redoNotebook;
          }
          if (normalizedKey === 'c' && !accel) {
            return COMMAND_IDS.copyCells;
          }
          if (normalizedKey === 'v' && !accel) {
            return COMMAND_IDS.pasteCells;
          }
          return null;
        })();
        if (directCommand) {
          event.preventDefault();
          void commands.execute(directCommand);
          return;
        }
      }
      if (
        notebook.mode === 'command'
        && normalizedKey === 'z'
        && !event.altKey
        && !event.ctrlKey
        && !event.metaKey
        && !event.shiftKey
      ) {
        event.preventDefault();
        void commands.execute(COMMAND_IDS.undoNotebook);
        return;
      }
      if (
        notebook.mode === 'command'
        && normalizedKey === 'z'
        && event.shiftKey
        && !event.altKey
        && !event.ctrlKey
        && !event.metaKey
      ) {
        event.preventDefault();
        void commands.execute(COMMAND_IDS.redoNotebook);
        return;
      }
      commands.processKeydownEvent(event);
    };
    const handleDocumentKeyUp = (event: KeyboardEvent) => {
      const target = event.target;
      if (!shouldRouteNotebookEvent(target, mountNode, notebook)) {
        return;
      }
      commands.processKeyupEvent(event);
    };
    const handleDocumentInput = (event: Event) => {
      const target = event.target;
      if (!(target instanceof Node) || !mountNode.contains(target)) {
        return;
      }
      if (!(target instanceof HTMLElement) || !target.closest('.cm-content')) {
        return;
      }
      handleModelChanged();
    };

    model.contentChanged.connect(handleModelChanged);
    model.cells.changed.connect(handleCellsChanged);
    bindCellContentListeners();
    document.addEventListener('keydown', handleDocumentKeyDown, true);
    document.addEventListener('keyup', handleDocumentKeyUp, true);
    document.addEventListener('input', handleDocumentInput, true);

    void (async () => {
      const widgetState = extractWidgetState(initialNotebookMetadata);
      if (widgetState && !disposed) {
        await widgetManager.clear_state();
        await widgetManager.set_state(widgetState);
      }
      Widget.attach(notebook, mountNode);
      notebook.update();
      lastSyncedCellsRef.current = cloneCells(initialCells);
      model.dirty = false;
      configureNotebookEditors();
      if (notebook.widgets.length > 0) {
        notebook.activeCellIndex = Math.max(0, notebook.activeCellIndex);
      }
      window.requestAnimationFrame(() => {
        if (disposed) {
          return;
        }
        // Render all markdown cells on load
        for (const widget of notebook.widgets) {
          if (widget.model.type === 'markdown' && 'rendered' in widget) {
            (widget as unknown as { rendered: boolean }).rendered = true;
          }
        }
        // Activate the first code cell (not markdown) so Shift+Enter runs immediately
        const firstCodeIndex = notebook.widgets.findIndex(w => w.model.type === 'code');
        if (firstCodeIndex >= 0) {
          notebook.activeCellIndex = firstCodeIndex;
        }
        focusNotebookCommandSelection(notebook);
      });

      modelRef.current = model;
      notebookRef.current = notebook;
      setSurfaceReady(true);
      setLoading(false);
    })().catch((err) => {
      if (!disposed) {
        setSurfaceReady(false);
        setLoading(false);
        setError(err instanceof Error ? err.message : String(err));
      }
    });

    return () => {
      disposed = true;
      if (autoSaveTimerRef.current != null) {
        window.clearTimeout(autoSaveTimerRef.current);
        autoSaveTimerRef.current = null;
      }
      if (toolbarStatusTimerRef.current != null) {
        window.clearTimeout(toolbarStatusTimerRef.current);
        toolbarStatusTimerRef.current = null;
      }
      document.removeEventListener('keydown', handleDocumentKeyDown, true);
      document.removeEventListener('keyup', handleDocumentKeyUp, true);
      document.removeEventListener('input', handleDocumentInput, true);
      model.contentChanged.disconnect(handleModelChanged);
      model.cells.changed.disconnect(handleCellsChanged);
      for (const listener of cellContentListeners.values()) {
        listener.model.contentChanged.disconnect(listener.callback);
      }
      cellContentListeners.clear();
      notebook.dispose();
      model.dispose();
      void widgetManager.clear_state().catch(() => {});
      restoreWarningFilter();
      if (debugWindow.__agentReplJupyterLab?.focusNotebook) {
        delete debugWindow.__agentReplJupyterLab;
      }
      notebookRef.current = null;
      modelRef.current = null;
      setSurfaceReady(false);
      mountNode.textContent = '';
    };
  }, [changeSelectedCellType, clearAllOutputs, deleteSelectedCells, executeActiveCell, executeAll, flushAutosave, initialCells, initialDocumentVersion, initialNotebookMetadata, initialTrustSnapshot, insertRelativeCell, moveSelectedCell, redoNotebookStructure, scheduleAutosave, undoNotebookStructure]);

  // Global keyboard shortcuts — independent of notebook lifecycle
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const accel = event.metaKey || event.ctrlKey;
      const key = event.key.toLowerCase();
      if (accel && key === 's') {
        event.preventDefault();
        event.stopPropagation();
        void flushAutosave({ announce: true });
      }
      if (accel && key === 'b') {
        event.preventDefault();
        event.stopPropagation();
        setExplorerCollapsed((prev: boolean) => !prev);
      }
    };
    document.addEventListener('keydown', handler, true);
    return () => document.removeEventListener('keydown', handler, true);
  }, [flushAutosave]);

  // Inject cell status bars and executing-cell data attributes
  useEffect(() => {
    const notebook = notebookRef.current;
    const model = modelRef.current;
    if (!notebook || !model || !surfaceReady) {
      return;
    }

    const runningSet = new Set(runtime.running_cell_ids);
    const queuedSet = new Set(runtime.queued_cell_ids);

    // Detect kernel restart — clear all status bars when generation changes
    const kernelRestarted = runtime.kernel_generation !== lastKernelGenerationRef.current
      && lastKernelGenerationRef.current !== 0;
    if (kernelRestarted) {
      executedCellIdsRef.current.clear();
    }
    lastKernelGenerationRef.current = runtime.kernel_generation;

    for (let i = 0; i < notebook.widgets.length; i++) {
      const widget = notebook.widgets[i];
      const cellModel = model.cells.get(i);
      if (!cellModel || cellModel.type !== 'code') {
        widget.node.removeAttribute('data-cell-executing');
        const oldBar = widget.node.querySelector('.agent-repl-cell-status');
        if (oldBar) oldBar.remove();
        continue;
      }

      const cellId = typeof (cellModel.sharedModel as any).getId === 'function'
        ? (cellModel.sharedModel as any).getId()
        : (cellModel as any).id ?? '';

      const isRunning = runningSet.has(cellId);
      const isQueued = queuedSet.has(cellId);

      // Set data attribute for CSS shimmer
      if (isRunning) {
        widget.node.setAttribute('data-cell-executing', 'true');
      } else {
        widget.node.removeAttribute('data-cell-executing');
      }

      // Inject or update status bar
      let bar = widget.node.querySelector('.agent-repl-cell-status') as HTMLElement | null;

      const ensureBar = () => {
        if (!bar) {
          bar = document.createElement('div');
          bar.className = 'agent-repl-cell-status';
          // Insert between input and output areas (not at the end)
          const outputWrapper = widget.node.querySelector('.jp-Cell-outputWrapper');
          if (outputWrapper) {
            widget.node.insertBefore(bar, outputWrapper);
          } else {
            const inputWrapper = widget.node.querySelector('.jp-Cell-inputWrapper');
            if (inputWrapper && inputWrapper.nextSibling) {
              widget.node.insertBefore(bar, inputWrapper.nextSibling);
            } else {
              widget.node.appendChild(bar);
            }
          }
        }
        return bar;
      };

      const setBarContent = (className: string, html: string) => {
        const b = ensureBar();
        if (b.className !== className) b.className = className;
        // Only update innerHTML if content changed — avoids restarting CSS animations
        if (b.getAttribute('data-status-html') !== html) {
          b.innerHTML = html;
          b.setAttribute('data-status-html', html);
        }
      };

      // Skip empty cells — no status for cells with no source
      const cellSource = (cellModel.sharedModel as any).getSource?.() ?? '';
      if (!cellSource.trim()) {
        if (bar) bar.remove();
        widget.node.removeAttribute('data-cell-executing');
        continue;
      }

      if (isRunning || isQueued) {
        if (isRunning) {
          setBarContent('agent-repl-cell-status agent-repl-cell-status-running', '<span class="agent-repl-cell-status-spinner"></span>');
        } else {
          setBarContent('agent-repl-cell-status agent-repl-cell-status-queued', '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><circle cx="8" cy="8" r="6" stroke="currentColor" stroke-width="1.5" fill="none" stroke-dasharray="3 3"/></svg>');
        }
      } else if (kernelRestarted) {
        if (bar) bar.remove();
      } else {
        const hasError = (cellModel as any).executionCount != null
          && Array.isArray((cellModel.toJSON() as any).outputs)
          && (cellModel.toJSON() as any).outputs.some((o: any) => o.output_type === 'error');
        const hasOutput = (cellModel as any).executionCount != null
          && Array.isArray((cellModel.toJSON() as any).outputs)
          && (cellModel.toJSON() as any).outputs.length > 0;
        const wasExecuted = executedCellIdsRef.current.has(cellId);

        if (hasError) {
          setBarContent('agent-repl-cell-status agent-repl-cell-status-failed', '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M4.5 4.5L11.5 11.5M11.5 4.5L4.5 11.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>');
        } else if (hasOutput || wasExecuted) {
          setBarContent('agent-repl-cell-status agent-repl-cell-status-completed', '<svg width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="M3.5 8.5L6.5 11.5L12.5 4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>');
        } else if (bar) {
          bar.remove();
        }
      }
    }
  }, [runtime, surfaceReady, cellModelVersion]);

  useEffect(() => {
    let disposed = false;

    async function bootstrap() {
      if (!currentNotebookPath) {
        setError('No notebook path provided.');
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      setSurfaceReady(false);
      setToolbarStatus(null);
      structureUndoStackRef.current = [];
      structureRedoStackRef.current = [];
      contentsRequestSeqRef.current = 0;
      appliedDocumentVersionRef.current = 0;
      appliedTrustSignatureRef.current = '';
      appliedNotebookMetadataSignatureRef.current = '';
      fallbackDocumentVersionRef.current = 0;
      for (let attempt = 0; attempt < BOOTSTRAP_MAX_ATTEMPTS && !disposed; attempt += 1) {
        try {
          await ensureAttached();
          const [runtimeResult, snapshot, workspaceResult, kernelsResult] = await Promise.all([
            (async () => {
              await loadRuntime();
              return true;
            })(),
            fetchSharedModelSnapshot(),
            (async () => {
              await loadWorkspaceTree();
              return true;
            })(),
            (async () => {
              await loadKernels();
              return true;
            })(),
          ]);
          void runtimeResult;
          void workspaceResult;
          void kernelsResult;
          const nextCells = Array.isArray(snapshot.cells) ? snapshot.cells : [];
          const nextNotebookMetadata = normalizeNotebookMetadata(snapshot.notebook_metadata);
          const nextTrust = trustSnapshotFromPayload(snapshot, nextCells);
          setInitialCells(nextCells);
          setInitialDocumentVersion(typeof snapshot.document_version === 'number' ? snapshot.document_version : 0);
          setInitialNotebookMetadata(nextNotebookMetadata);
          setInitialTrustSnapshot(nextTrust);
          setTrustSnapshot(nextTrust);
          return;
        } catch (err) {
          if (disposed) {
            return;
          }
          const retryable = isTransientBootstrapError(err) && attempt < BOOTSTRAP_MAX_ATTEMPTS - 1;
          if (retryable) {
            await delay(BOOTSTRAP_RETRY_DELAY_MS * (attempt + 1));
            continue;
          }
          setError(err instanceof Error ? err.message : String(err));
          setLoading(false);
          return;
        }
      }
    }

    void bootstrap();

    return () => {
      disposed = true;
    };
  }, [ensureAttached, fetchSharedModelSnapshot, loadKernels, loadRuntime, loadWorkspaceTree, currentNotebookPath]);

  useEffect(() => {
    if (!currentNotebookPath || !surfaceReady) {
      return;
    }

    const origin = window.location.origin;
    const ws = new DaemonWebSocket({
      daemonUrl: origin,
      daemonToken: '', // same-origin; proxy handles auth
      createSocket: (url: string) => new WebSocket(url) as any,
      fetchFn: window.fetch.bind(window),
      onMessage: (msg: any) => {
        const msgType = msg.type;

        // Streaming IOPub: apply incrementally, don't reload full notebook
        if (msgType === 'cell-output-appended' && msg.data?.iopub_output) {
          applyIOPubMessage(msg);
          void loadRuntime();
          return;
        }
        if (msgType === 'cell-outputs-cleared') {
          clearCellOutputs(msg.cell_id || msg.data?.cell_id);
          void loadRuntime();
          return;
        }
        if (msgType === 'execution-started' && msg.cell_id) {
          clearCellOutputs(msg.cell_id);
          executedCellIdsRef.current.add(msg.cell_id);
          setCellModelVersion((v) => v + 1);
          void loadRuntime();
          return;
        }
        if (msgType === 'execution-finished' && msg.cell_id) {
          executedCellIdsRef.current.add(msg.cell_id);
        }

        // Non-streaming events: full refresh for reconciliation
        const isExecutionFinished = msgType === 'execution-finished'
          || msgType === 'cell-outputs-updated';
        if (msg.runtime || isExecutionFinished) {
          void loadRuntime();
        }
        if (isExecutionFinished) {
          // Final reconciliation — loadContents does fromJSON which replaces streaming outputs cleanly
          void loadContents();
        } else {
          // Non-execution events (source edits, structure changes)
          const events: Array<{ type: string }> = msg ? [msg] : [];
          if (shouldReloadStandaloneNotebookContents(events)
            && msgType !== 'cell-output-appended'
            && msgType !== 'execution-started') {
            void loadContents();
          }
        }
        if (pendingExecutionRef.current && !runtimeBusyRef.current) {
          pendingExecutionRef.current = false;
        }
      },
      onConnect: () => {
        ws.subscribe(currentNotebookPath);
      },
      onDisconnect: () => { /* reconnect is automatic */ },
      onInstanceChange: () => {
        void loadRuntime();
        void loadContents();
      },
    });
    ws.connect();

    return () => {
      ws.close();
    };
  }, [loadContents, loadRuntime, currentNotebookPath, surfaceReady]);

  const compactToolbar = false;
  const tightToolbar = false;
  const tbBtn = createToolbarButtonBaseStyle();
  const tbOutline = createToolbarOutlineButtonStyle(compactToolbar);
  const tbGhost: React.CSSProperties = {
    ...tbBtn,
    padding: compactToolbar ? '0 8px' : '0 10px',
    border: 'none',
    background: 'transparent',
    color: 'var(--cds-text-helper)',
  };
  const tbGhostStrong: React.CSSProperties = {
    ...tbGhost,
    color: 'var(--cds-text-secondary)',
  };
  const tbSegmentGroup: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'stretch',
    border: '1px solid var(--cds-border-subtle)',
    borderRadius: 6,
    overflow: 'hidden',
    flexShrink: 0,
    background: 'transparent',
  };
  const themeModes: Array<{ value: ThemeMode; label: string }> = [
    { value: 'system', label: 'System' },
    { value: 'light', label: 'Light' },
    { value: 'dark', label: 'Dark' },
  ];
  const selectedKernel = kernels.find((kernel) => kernel.id === selectedKernelId) ?? null;
  const activeNotebookLabel = currentNotebookPath?.split(/[\\/]/).pop() ?? 'Notebook';
  const canStop = Boolean(runtime.busy || runtime.current_execution || runtime.queued_cell_ids.length > 0 || runtime.running_cell_ids.length > 0);

  const toggleExplorerDirectory = useCallback((directoryPath: string) => {
    setExplorerExpandedPaths((current) => {
      const next = new Set(current);
      if (next.has(directoryPath)) {
        next.delete(directoryPath);
      } else {
        next.add(directoryPath);
      }
      return next;
    });
  }, []);

  const switchNotebookFromExplorer = useCallback(async (nextPath: string) => {
    if (!nextPath || nextPath === currentNotebookPath) {
      return;
    }
    const saved = await flushAutosave();
    if (!saved) {
      return;
    }
    setCurrentNotebookPath(nextPath);
    setExplorerExpandedPaths((current) => new Set([...current, ...collectExplorerAncestorPaths(nextPath)]));
    const url = new URL(window.location.href);
    url.searchParams.set('path', nextPath);
    window.history.replaceState(null, '', url.toString());
  }, [currentNotebookPath, flushAutosave]);

  const renderExplorerNode = (node: WorkspaceTreeNode, depth = 0): React.ReactNode => {
    if (node.kind === 'directory') {
      const expanded = explorerExpandedPaths.has(node.path);
      return (
        <div key={node.path || `workspace-${node.name}`}>
          <button
            type="button"
            data-explorer-item="directory"
            data-explorer-item-path={node.path}
            aria-expanded={expanded}
            onClick={() => toggleExplorerDirectory(node.path)}
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              padding: `4px 10px 4px ${12 + depth * 14}px`,
              border: 'none',
              background: 'transparent',
              color: 'var(--cds-text-secondary)',
              cursor: 'pointer',
              textAlign: 'left',
              fontFamily: `"${UI_FONT}", sans-serif`,
              fontSize: 12.5,
              lineHeight: 1.5,
            }}
          >
            <span style={{ width: 12, display: 'inline-flex', justifyContent: 'center', color: 'var(--cds-text-helper)' }}>
              {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            </span>
            <span style={{ display: 'inline-flex', color: 'var(--cds-text-helper)' }}>
              {expanded ? <FolderOpen size={14} /> : <Folder size={14} />}
            </span>
            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {node.name}
            </span>
          </button>
          {expanded ? <div>{(node.children ?? []).map((child) => renderExplorerNode(child, depth + 1))}</div> : null}
        </div>
      );
    }

    const active = node.path === currentNotebookPath;
    return (
      <button
        key={node.path}
        type="button"
        data-explorer-item="notebook"
        data-explorer-item-path={node.path}
        data-active={active ? 'true' : 'false'}
        onClick={() => void switchNotebookFromExplorer(node.path)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: `5px 10px 5px ${30 + depth * 14}px`,
          border: 'none',
          background: active ? 'var(--cds-layer-accent)' : 'transparent',
          color: active ? 'var(--cds-text-primary)' : 'var(--cds-text-secondary)',
          cursor: 'pointer',
          textAlign: 'left',
          fontFamily: `"${UI_FONT}", sans-serif`,
          fontSize: 12.5,
          lineHeight: 1.45,
          borderRadius: 6,
        }}
      >
        <span style={{ display: 'inline-flex', color: active ? 'var(--cds-interactive)' : 'var(--cds-text-helper)' }}>
          <NotebookIcon size={14} />
        </span>
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
      </button>
    );
  };

  const secondaryStatus = toolbarStatus;

  return (
    <Theme
      theme={carbonTheme}
      style={{
        minHeight: '100vh',
        fontFamily: `"${UI_FONT}", 'IBM Plex Sans', system-ui, sans-serif`,
      }}
    >
      <div
        className="jp-LabShell jp-ThemedContainer agent-repl-jupyterlab-shell"
        translate="no"
        data-jp-theme-light={dark ? 'false' : 'true'}
        data-jp-theme-name={dark ? 'agent-repl-dark' : 'agent-repl-light'}
        data-jupyterlab-ready={surfaceReady ? 'true' : 'false'}
        data-jupyterlab-error={error ? 'true' : 'false'}
        data-jupyterlab-phase={error ? 'error' : (surfaceReady ? 'ready' : 'booting')}
        data-browser-shell="true"
        style={{
          display: 'flex',
          flexDirection: 'row',
          minHeight: '100vh',
          background: 'var(--cds-background)',
        }}
      >
        <aside
          style={{
            width: 48,
            borderRight: '1px solid var(--cds-border-subtle)',
            background: 'var(--cds-layer)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            paddingTop: 10,
            gap: 8,
            flexShrink: 0,
          }}
        >
          <button
            type="button"
            data-explorer-toggle="true"
            aria-label={explorerCollapsed ? 'Show Explorer' : 'Hide Explorer'}
            aria-pressed={!explorerCollapsed}
            onClick={() => setExplorerCollapsed((current) => !current)}
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              border: 'none',
              background: explorerCollapsed ? 'transparent' : 'var(--cds-layer-accent)',
              color: explorerCollapsed ? 'var(--cds-text-helper)' : 'var(--cds-text-primary)',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
            }}
            title="Explorer"
          >
            <Folder size={18} />
          </button>
        </aside>
        <aside
          data-explorer-panel="true"
          data-collapsed={explorerCollapsed ? 'true' : 'false'}
          style={{
            width: explorerCollapsed ? 0 : 272,
            opacity: explorerCollapsed ? 0 : 1,
            overflow: 'hidden',
            pointerEvents: explorerCollapsed ? 'none' : 'auto',
            transition: 'width 140ms ease, opacity 120ms ease',
            borderRight: explorerCollapsed ? 'none' : '1px solid var(--cds-border-subtle)',
            background: 'var(--cds-layer)',
            flexShrink: 0,
          }}
        >
          <div style={{ width: 272, height: '100%', display: 'flex', flexDirection: 'column' }}>
            <div
              style={{
                padding: '14px 12px 10px',
                borderBottom: '1px solid var(--cds-border-subtle)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{
                  fontSize: 11,
                  letterSpacing: '0.08em',
                  color: 'var(--cds-text-helper)',
                  textTransform: 'uppercase',
                  fontWeight: 600,
                }}>
                  Explorer
                </div>
                <div style={{
                  marginTop: 4,
                  fontSize: 13,
                  color: 'var(--cds-text-primary)',
                  fontWeight: 600,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {workspaceName || workspaceTree?.name || 'Workspace'}
                </div>
              </div>
            </div>
            <div
              data-explorer-tree="true"
              style={{
                flex: 1,
                overflowY: 'auto',
                padding: '8px 6px 14px',
              }}
            >
              <div style={{
                padding: '0 10px 10px',
                fontSize: 12,
                color: 'var(--cds-text-helper)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {activeNotebookLabel}
              </div>
              {workspaceTree?.children?.length ? (
                workspaceTree.children.map((child) => renderExplorerNode(child))
              ) : (
                <div style={{ padding: '8px 10px', fontSize: 12.5, color: 'var(--cds-text-helper)' }}>
                  No notebooks found in this workspace.
                </div>
              )}
            </div>
          </div>
        </aside>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--cds-background)' }}>
          <div
            className="agent-repl-jupyterlab-toolbar"
            data-toolbar="notebook"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '12px 24px',
              borderBottom: '1px solid var(--cds-border-subtle)',
            }}
          >
            <div style={{
              display: 'flex',
              width: '100%',
              gap: 4,
              alignItems: 'center',
              justifyContent: 'center',
              flexWrap: 'nowrap',
              minWidth: 0,
              paddingLeft: CELL_GUTTER_WIDTH,
              overflow: 'visible',
            }}>
              <button
                type="button"
                onClick={() => void flushAutosave({ announce: true })}
                aria-label="Save notebook"
                title="Save notebook"
                style={tbOutline}
              >
                <Save size={14} />
                <span>Save</span>
              </button>
              <button
                type="button"
                onClick={() => void interruptExecution()}
                aria-label="Stop"
                title="Stop"
                disabled={!canStop}
                style={{
                  ...tbGhostStrong,
                  opacity: canStop ? 1 : 0.4,
                }}
              >
                <span style={{
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: 'currentColor',
                  display: 'inline-block',
                  flexShrink: 0,
                }} />
                <span>Stop</span>
              </button>
              <button
                type="button"
                onClick={() => void restartKernel()}
                aria-label="Restart"
                title="Restart"
                style={tbGhostStrong}
              >
                <Restart size={14} />
                <span>Restart</span>
              </button>
              <button
                type="button"
                onClick={() => void executeAll()}
                aria-label="Run All"
                title="Run All"
                style={tbOutline}
              >
                <PlayFilled size={14} />
                <span>Run All</span>
              </button>
              <button
                type="button"
                onClick={() => void restartAndRunAll()}
                aria-label="Restart and Run All"
                title="Restart and Run All"
                style={tbGhostStrong}
              >
                <Restart size={14} />
                <span>Restart &amp; Run All</span>
              </button>
              <button
                type="button"
                onClick={() => clearAllOutputs()}
                aria-label="Clear All Outputs"
                title="Clear All Outputs"
                style={tbGhostStrong}
              >
                <Erase size={14} />
                <span>Clear Outputs</span>
              </button>
              <span style={{ width: 1, height: 16, background: 'var(--cds-border-subtle)', margin: '0 8px', flexShrink: 0 }} />
              <div role="group" aria-label="Theme mode" style={tbSegmentGroup}>
                {themeModes.map((option, index) => {
                  const active = themeMode === option.value;
                  return (
                    <button
                      key={option.value}
                      type="button"
                      aria-pressed={active}
                      onClick={() => setThemeMode(option.value)}
                      style={{
                        ...tbBtn,
                        height: 30,
                        borderRadius: 0,
                        border: 'none',
                        padding: '0 10px',
                        background: active ? 'var(--cds-layer-accent)' : 'transparent',
                        color: active ? 'var(--cds-text-primary)' : 'var(--cds-text-secondary)',
                        borderLeft: index === 0 ? 'none' : '1px solid var(--cds-border-subtle)',
                      }}
                    >
                      {option.label}
                    </button>
                  );
                })}
              </div>
              <div
                ref={kernelMenuRef}
                style={{
                  position: 'relative',
                  minWidth: 220,
                  maxWidth: 320,
                  flex: '1 1 auto',
                }}
              >
                <button
                  type="button"
                  className="toolbar-kernel-button"
                  aria-haspopup="listbox"
                  aria-expanded={kernelMenuOpen}
                  onClick={() => setKernelMenuOpen((open) => !open)}
                  style={{
                    ...tbOutline,
                    width: '100%',
                    justifyContent: 'space-between',
                    padding: '0 12px',
                    color: selectedKernel ? 'var(--cds-text-primary)' : 'var(--cds-text-helper)',
                  }}
                >
                  <span style={{
                    display: 'block',
                    flex: '1 1 auto',
                    minWidth: 0,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}>
                    {selectedKernel?.label ?? runtime.kernel_label ?? '.venv (workspace)'}
                  </span>
                  <ChevronDown
                    size={16}
                    style={{
                      transform: kernelMenuOpen ? 'rotate(180deg)' : 'rotate(0deg)',
                      transition: 'transform 120ms ease',
                      flexShrink: 0,
                    }}
                  />
                </button>
                {kernelMenuOpen ? (
                  <div
                    className="toolbar-kernel-menu"
                    role="listbox"
                    aria-label="Kernel selector"
                    style={{
                      position: 'absolute',
                      top: 'calc(100% + 8px)',
                      left: 0,
                      minWidth: '100%',
                      maxWidth: 320,
                      padding: 6,
                      borderRadius: 8,
                      border: '1px solid var(--cds-border-subtle)',
                      background: 'var(--cds-layer)',
                      boxShadow: '0 12px 32px rgba(28,25,23,0.12)',
                      zIndex: 10,
                    }}
                  >
                    {kernels.length === 0 ? (
                      <div style={{ padding: '8px 10px', fontSize: 12, color: 'var(--cds-text-helper)' }}>
                        No kernels available
                      </div>
                    ) : kernels.map((kernel) => {
                      const isSelected = kernel.id === selectedKernelId;
                      return (
                        <button
                          key={kernel.id}
                          type="button"
                          className="toolbar-kernel-option"
                          role="option"
                          aria-selected={isSelected}
                          onClick={() => void selectKernel(kernel.id)}
                          style={{
                            width: '100%',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: 10,
                            padding: '8px 10px',
                            border: 'none',
                            borderRadius: 6,
                            background: isSelected ? 'var(--cds-layer-accent)' : 'transparent',
                            color: isSelected ? 'var(--cds-text-primary)' : 'var(--cds-text-secondary)',
                            cursor: 'pointer',
                            textAlign: 'left',
                          }}
                        >
                          <span style={{
                            display: 'block',
                            flex: '1 1 auto',
                            minWidth: 0,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                            fontSize: 13,
                            fontWeight: isSelected ? 600 : 500,
                          }}>
                            {kernel.label}
                          </span>
                          <span style={{
                            fontSize: 11,
                            color: isSelected ? '#d97706' : 'var(--cds-text-helper)',
                            whiteSpace: 'nowrap',
                            flexShrink: 0,
                          }}>
                            {isSelected ? 'Selected' : kernel.recommended ? 'Recommended' : ''}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
              {!trustSnapshot.notebook_trusted ? (
                <button type="button" onClick={() => void trustNotebook()} style={tbOutline}>
                  Trust
                </button>
              ) : null}
            </div>
          </div>

          {error ? (
            <div className="agent-repl-jupyterlab-preview-state agent-repl-jupyterlab-preview-state--error" style={{
              margin: '30px 24px 0',
              padding: '12px 16px',
              borderRadius: 4,
              border: '1px solid var(--cds-support-error)',
              background: 'rgba(225,29,72,0.06)',
              color: 'var(--cds-support-error)',
              fontSize: 13,
            }}>
              {error}
            </div>
          ) : null}

          {secondaryStatus ? (
            <div className="agent-repl-jupyterlab-toolbarStatus" style={{
              position: 'fixed',
              bottom: 16,
              right: 16,
              padding: '8px 14px',
              borderRadius: 6,
              background: 'var(--cds-layer)',
              color: 'var(--cds-text-secondary)',
              fontSize: 12,
              boxShadow: '0 2px 8px rgba(0,0,0,0.2)',
              zIndex: 1000,
              pointerEvents: 'none',
              opacity: 0.9,
            }}>
              {secondaryStatus}
            </div>
          ) : null}

          {loading ? (
            <div className="agent-repl-jupyterlab-preview-state" style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              minHeight: 240,
              margin: '30px 24px',
              borderRadius: 4,
              border: '1px dashed var(--cds-border-subtle)',
              background: 'var(--cds-layer)',
              fontSize: 14,
              fontWeight: 500,
              color: 'var(--cds-text-secondary)',
            }}>
              Loading notebook canvas...
            </div>
          ) : null}

          <div
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: 'auto',
              padding: '30px 24px 5rem',
              display: loading ? 'none' : 'flex',
              flexDirection: 'column',
              gap: '30px',
            }}
          >
            <div
              className="agent-repl-jupyterlab-surfaceMount"
              ref={mountRef}
              style={{ minHeight: 320, width: '100%' }}
            />
          </div>
        </div>
      </div>
    </Theme>
  );
}

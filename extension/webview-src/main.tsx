import DOMPurify from 'dompurify';
import { marked } from 'marked';
import React, {
  createContext,
  memo,
  startTransition,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createRoot } from 'react-dom/client';
import {
  Theme,
} from '@carbon/react';
import {
  Add,
  CheckmarkFilled,
  Copy,
  Folder,
  FolderOpen,
  Notebook,
  Play,
  PlayFilled,
  Restart,
  Save,
  TrashCan,
  ChevronUp,
  ChevronDown,
  ChevronRight,
  ChevronLeft,
} from '@carbon/icons-react';
import { InterfaceKit } from 'interface-kit/react';
import { PageFeedbackToolbarCSS } from 'agentation';
import { CodeMirrorCell, CodeMirrorCellHandle } from './codemirror-cell';
import { createStandaloneHost, readStandaloneConfig } from './standalone-host';
import { deriveCellStatusKind, type CellStatusKind } from '../src/shared/cellStatus';
import { resolveIdleExecutionTransition } from '../src/shared/executionState';
import { decideNotebookCommandKeyAction } from '../src/shared/notebookCommandController';
import '@carbon/styles/css/styles.css';
import './styles.css';

declare global {
  interface Window {
    acquireVsCodeApi?: () => {
      postMessage: (message: unknown) => void;
    };
  }
}

// ── Types ─────────────────────────────────────────────────

type NotebookOutput = {
  output_type: string;
  name?: string;
  text?: string | string[];
  ename?: string;
  evalue?: string;
  traceback?: string[];
  data?: Record<string, string | string[]>;
};

type NotebookCell = {
  cell_id: string;
  cell_type: 'code' | 'markdown' | 'raw' | string;
  source: string;
  outputs?: NotebookOutput[];
  execution_count?: number | null;
  index?: number;
  metadata?: Record<string, any>;
};

type DraftChange = {
  cell_id: string;
  source: string;
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

type ActivityEvent = {
  event_type?: string;
  type?: string;
  cell_id?: string;
  data?: {
    cell?: NotebookCell;
  };
};

type RuntimeInfo = {
  active?: boolean;
  busy?: boolean;
  kernel_label?: string;
  runtime_id?: string;
  kernel_generation?: number | null;
  current_execution?: Record<string, unknown> | null;
  running_cell_ids?: string[];
  queued_cell_ids?: string[];
};

type CellDiagnostic = {
  from: number;
  to: number;
  severity: 'error' | 'warning' | 'info' | 'hint';
  message: string;
  source?: string;
};

type LspStatus = {
  state: 'starting' | 'ready' | 'unavailable';
  message: string;
};

type ThemeMode = 'system' | 'light' | 'dark';

type LspCompletionItem = {
  label: string;
  kind?: string;
  detail?: string;
  documentation?: string;
  apply?: string;
  sortText?: string;
  filterText?: string;
};

type CompletionRequestOptions = {
  explicit?: boolean;
  triggerCharacter?: string;
};

type HostMessage = {
  type: string;
  requestId?: string;
  [key: string]: unknown;
};

type HostApi = {
  postMessage: (message: HostMessage) => void;
};

const EMPTY_DIAGNOSTICS: CellDiagnostic[] = [];

// ── Design constants ──────────────────────────────────────

const MONO_FONT = '"IBM Plex Mono", "SF Mono", Monaco, Menlo, Consolas, monospace';
const UI_FONT = 'IBM Plex Sans';
const CODE_FONT_SIZE = 12.5;
const OUTPUT_FONT_SIZE = 13;
const CODE_LINE_HEIGHT = 1.6;
const CELL_GAP = 30;
const CELL_PADDING = 16;
const OUTPUT_PADDING = 12;
const CONTENT_PADDING_X = 24;
const CORNER_RADIUS = 4;
const SELECTION_COLOR = '#d97706';
const SELECTION_RING_OPACITY = 0.12;
const FOCUS_RING_WIDTH = 3;
const OUTPUT_BG = 'var(--cds-background)';
const LABEL_FONT_SIZE = 11;
const CODE_TOP_PADDING = 10;
const CONTROLS_TOP_PADDING = 8;
const MARKDOWN_LEFT_PADDING = 8;
const CELL_GUTTER_WIDTH = 40;
const THEME_MODE_STORAGE_KEY = 'agent-repl.theme-mode';
const EXPLORER_COLLAPSED_STORAGE_KEY = 'agent-repl.explorer-collapsed';

type CarbonThemeName = 'white' | 'g10' | 'g90' | 'g100';

// ── Live design params ───────────────────────────────────

type DesignParams = {
  codeFontSize: number;
  outputFontSize: number;
  cellGap: number;
  codeTopPadding: number;
  outputPadding: number;
  contentPaddingX: number;
  cornerRadius: number;
  labelFontSize: number;
  outputBg: string;
};

const DEFAULT_DESIGN: DesignParams = {
  codeFontSize: CODE_FONT_SIZE,
  outputFontSize: OUTPUT_FONT_SIZE,
  cellGap: CELL_GAP,
  codeTopPadding: CODE_TOP_PADDING,
  outputPadding: OUTPUT_PADDING,
  contentPaddingX: CONTENT_PADDING_X,
  cornerRadius: CORNER_RADIUS,
  labelFontSize: LABEL_FONT_SIZE,
  outputBg: OUTPUT_BG,
};

const DesignContext = createContext<DesignParams>(DEFAULT_DESIGN);
const useDesign = () => useContext(DesignContext);

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

// ── Helpers ───────────────────────────────────────────────

function hexToRgb(hex: string): string {
  if (!hex.startsWith('#')) return '0,0,0';
  return `${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)}`;
}

const selRgb = hexToRgb(SELECTION_COLOR);

function humanizeDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) {
    const m = Math.floor(ms / 60_000);
    const s = Math.round((ms % 60_000) / 1000);
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  const h = Math.floor(ms / 3_600_000);
  const m = Math.round((ms % 3_600_000) / 60_000);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function formatExecutionDuration(ms: number): string {
  const clamped = Math.max(0, ms);
  if (clamped < 60_000) {
    return `${(clamped / 1000).toFixed(1)}s`;
  }
  if (clamped < 3_600_000) {
    const minutes = Math.floor(clamped / 60_000);
    const seconds = Math.floor((clamped % 60_000) / 1000);
    return `${minutes}m ${seconds}s`;
  }
  const hours = Math.floor(clamped / 3_600_000);
  const minutes = Math.floor((clamped % 3_600_000) / 60_000);
  return `${hours}h ${minutes}m`;
}


function cloneOutput(output: NotebookOutput): NotebookOutput {
  return {
    ...output,
    text: Array.isArray(output.text) ? [...output.text] : output.text,
    traceback: output.traceback ? [...output.traceback] : output.traceback,
    data: output.data ? { ...output.data } : output.data,
  };
}

function cloneCell(cell: NotebookCell): NotebookCell {
  return {
    ...cell,
    outputs: cell.outputs?.map(cloneOutput) ?? [],
    metadata: cell.metadata ? JSON.parse(JSON.stringify(cell.metadata)) : cell.metadata,
  };
}

function cellHasErrorOutput(cell: NotebookCell | null | undefined): boolean {
  return Boolean(cell?.outputs?.some((output) => output.output_type === 'error'));
}

function getCellRuntimeProvenance(cell: NotebookCell | null | undefined): {
  runtimeId: string;
  kernelGeneration: number;
  status: 'ok' | 'error';
} | null {
  const lastRun = cell?.metadata?.custom?.['agent-repl']?.last_run;
  if (!lastRun || typeof lastRun !== 'object') {
    return null;
  }
  if (typeof lastRun.runtime_id !== 'string' || typeof lastRun.kernel_generation !== 'number') {
    return null;
  }
  if (lastRun.status !== 'ok' && lastRun.status !== 'error') {
    return null;
  }
  return {
    runtimeId: lastRun.runtime_id,
    kernelGeneration: lastRun.kernel_generation,
    status: lastRun.status,
  };
}

function cellMatchesCurrentRuntime(cell: NotebookCell | null | undefined, runtime: RuntimeInfo): boolean {
  const provenance = getCellRuntimeProvenance(cell);
  if (!provenance || !runtime.active || typeof runtime.runtime_id !== 'string' || typeof runtime.kernel_generation !== 'number') {
    return false;
  }
  return provenance.runtimeId === runtime.runtime_id
    && provenance.kernelGeneration === runtime.kernel_generation;
}

function currentExecutionCellId(runtime: RuntimeInfo): string | null {
  const cellId = runtime.current_execution?.cell_id;
  return typeof cellId === 'string' && cellId ? cellId : null;
}

function runtimeCellIds(runtime: RuntimeInfo, kind: 'running' | 'queued'): string[] {
  const raw = kind === 'running' ? runtime.running_cell_ids : runtime.queued_cell_ids;
  const cellIds = Array.isArray(raw)
    ? raw.filter((cellId): cellId is string => typeof cellId === 'string' && cellId.length > 0)
    : [];
  if (kind === 'running') {
    const currentCellId = currentExecutionCellId(runtime);
    if (currentCellId && !cellIds.includes(currentCellId)) {
      return [currentCellId, ...cellIds];
    }
  }
  return cellIds;
}

function withCellRuntimeProvenance(
  cell: NotebookCell,
  runtime: Pick<RuntimeInfo, 'runtime_id' | 'kernel_generation'>,
  status: 'ok' | 'error',
): NotebookCell {
  if (typeof runtime.runtime_id !== 'string' || typeof runtime.kernel_generation !== 'number') {
    return cell;
  }
  const metadata = cell.metadata ? JSON.parse(JSON.stringify(cell.metadata)) : {};
  const custom = metadata.custom && typeof metadata.custom === 'object' ? { ...metadata.custom } : {};
  const agentRepl = custom['agent-repl'] && typeof custom['agent-repl'] === 'object'
    ? { ...custom['agent-repl'] }
    : {};
  agentRepl.last_run = {
    runtime_id: runtime.runtime_id,
    kernel_generation: runtime.kernel_generation,
    status,
    updated_at: Date.now() / 1000,
  };
  custom['agent-repl'] = agentRepl;
  metadata.custom = custom;
  return { ...cell, metadata };
}

function clearCellRuntimeProvenance(cell: NotebookCell): NotebookCell {
  if (!cell.metadata?.custom?.['agent-repl']?.last_run) {
    return cell;
  }
  const metadata = JSON.parse(JSON.stringify(cell.metadata));
  const custom = metadata.custom && typeof metadata.custom === 'object' ? metadata.custom : {};
  const agentRepl = custom['agent-repl'] && typeof custom['agent-repl'] === 'object'
    ? custom['agent-repl']
    : null;
  if (agentRepl && typeof agentRepl === 'object') {
    delete agentRepl.last_run;
  }
  return { ...cell, metadata };
}

function normalizeDraftChanges(raw: unknown): DraftChange[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') {
      return [];
    }
    const change = entry as Record<string, unknown>;
    if (typeof change.cell_id !== 'string' || typeof change.source !== 'string') {
      return [];
    }
    return [{ cell_id: change.cell_id, source: change.source }];
  });
}

function collectDirtyDraftChanges(cells: NotebookCell[], drafts: Record<string, string>): DraftChange[] {
  return cells.flatMap((cell) => {
    const draft = drafts[cell.cell_id];
    if (draft === undefined || draft === cell.source) {
      return [];
    }
    return [{ cell_id: cell.cell_id, source: draft }];
  });
}

function applyDraftChangesToCells(cells: NotebookCell[], changes: DraftChange[]): NotebookCell[] {
  if (changes.length === 0) {
    return cells;
  }
  const changesByCellId = new Map(changes.map((change) => [change.cell_id, change.source]));
  return cells.map((cell) => {
    const nextSource = changesByCellId.get(cell.cell_id);
    if (nextSource === undefined || nextSource === cell.source) {
      return cell;
    }
    const nextCell = {
      ...cell,
      source: nextSource,
    };
    return cell.cell_type === 'code'
      ? { ...clearCellRuntimeProvenance(nextCell), outputs: [], execution_count: null }
      : nextCell;
  });
}

function moveTextareaCaretToLineEnd(element: HTMLTextAreaElement): void {
  const position = element.selectionEnd ?? 0;
  const value = element.value;
  const nextBreak = value.indexOf('\n', position);
  const lineEnd = nextBreak >= 0 ? nextBreak : value.length;
  element.selectionStart = lineEnd;
  element.selectionEnd = lineEnd;
}

function moveTextareaCaretToBoundary(element: HTMLTextAreaElement, boundary: 'start' | 'end'): void {
  const position = boundary === 'start' ? 0 : element.value.length;
  element.selectionStart = position;
  element.selectionEnd = position;
}


// ── Preview host (browser dev mode) ──────────────────────

function createPreviewHost(): HostApi {
  let executionCount = 2;
  let selectedKernelId = 'pyodide';
  const previewRuntimeId = 'preview-runtime';
  let previewKernelGeneration = 1;
  let runtime: RuntimeInfo = {
    active: false,
    busy: false,
    kernel_label: 'Python 3.11 (Pyodide preview)',
    runtime_id: previewRuntimeId,
    kernel_generation: previewKernelGeneration,
  };
  const kernels: Kernel[] = [
    { id: 'pyodide', label: 'Python 3.11 (Pyodide preview)', recommended: true },
  ];
  const previewWorkspaceName = 'Preview Workspace';
  const notebooks: Array<{ path: string; cells: NotebookCell[]; executionCount: number }> = [
    {
      path: 'preview/playground.ipynb',
      executionCount: 2,
      cells: [
        {
          cell_id: 'preview-md-1',
          cell_type: 'markdown',
          source: '# Notebook playground\n\nUse this browser preview to iterate on the canvas UI quickly before jumping back into VS Code.',
          outputs: [],
          execution_count: null,
        },
        {
          cell_id: 'preview-code-1',
          cell_type: 'code',
          source: 'message = "hello from preview"\nmessage',
          execution_count: 1,
          outputs: [
            {
              output_type: 'execute_result',
              data: { 'text/plain': '"hello from preview"' },
            },
          ],
        },
        {
          cell_id: 'preview-code-2',
          cell_type: 'code',
          source: 'for idx in range(3):\n    print(f"row {idx}")',
          execution_count: 2,
          outputs: [
            {
              output_type: 'stream',
              name: 'stdout',
              text: 'row 0\nrow 1\nrow 2\n',
            },
          ],
        },
      ],
    },
    {
      path: 'preview/agents-demo.ipynb',
      executionCount: 1,
      cells: [
        {
          cell_id: 'agents-md-1',
          cell_type: 'markdown',
          source: '# Agent Notes\n\nThis notebook demos the browser explorer switching between preview notebooks.',
          outputs: [],
          execution_count: null,
        },
        {
          cell_id: 'agents-code-1',
          cell_type: 'code',
          source: 'items = ["planner", "executor", "reviewer"]\nitems[-1]',
          execution_count: 1,
          outputs: [
            {
              output_type: 'execute_result',
              data: { 'text/plain': '"reviewer"' },
            },
          ],
        },
      ],
    },
    {
      path: 'analysis/charts.ipynb',
      executionCount: 1,
      cells: [
        {
          cell_id: 'charts-md-1',
          cell_type: 'markdown',
          source: '# Quick Plot\n\nA tiny notebook for switching validation.',
          outputs: [],
          execution_count: null,
        },
        {
          cell_id: 'charts-code-1',
          cell_type: 'code',
          source: 'sum([2, 4, 8])',
          execution_count: 1,
          outputs: [
            {
              output_type: 'execute_result',
              data: { 'text/plain': '14' },
            },
          ],
        },
      ],
    },
  ];
  let currentNotebook = notebooks[0];
  let currentNotebookPath = currentNotebook.path;
  let cells = currentNotebook.cells;

  const buildPreviewWorkspaceTree = (): WorkspaceTreeNode => ({
    kind: 'directory',
    name: previewWorkspaceName,
    path: '',
    children: [
      {
        kind: 'directory',
        name: 'analysis',
        path: 'analysis',
        children: [
          { kind: 'notebook', name: 'charts.ipynb', path: 'analysis/charts.ipynb' },
        ],
      },
      {
        kind: 'directory',
        name: 'preview',
        path: 'preview',
        children: [
          { kind: 'notebook', name: 'agents-demo.ipynb', path: 'preview/agents-demo.ipynb' },
          { kind: 'notebook', name: 'playground.ipynb', path: 'preview/playground.ipynb' },
        ],
      },
    ],
  });

  const persistNotebookExecutionCount = () => {
    currentNotebook.executionCount = executionCount;
  };

  const switchPreviewNotebook = (nextPath: string): boolean => {
    const nextNotebook = notebooks.find((notebook) => notebook.path === nextPath);
    if (!nextNotebook) {
      return false;
    }
    currentNotebook = nextNotebook;
    currentNotebookPath = nextNotebook.path;
    cells = nextNotebook.cells;
    executionCount = nextNotebook.executionCount;
    return true;
  };

  const dispatch = (message: HostMessage) => {
    window.dispatchEvent(new MessageEvent('message', { data: message }));
  };

  const sendContents = (requestId?: string) => {
    dispatch({
      type: 'contents',
      requestId,
      path: currentNotebookPath,
      cells: cells.map(cloneCell),
    });
  };

  const sendWorkspaceTree = (requestId?: string) => {
    dispatch({
      type: 'workspace-tree',
      requestId,
      workspace_name: previewWorkspaceName,
      root: buildPreviewWorkspaceTree(),
      selected_path: currentNotebookPath,
    });
  };

  const sendKernels = (requestId?: string) => {
    dispatch({
      type: 'kernels',
      requestId,
      kernels,
      preferred_kernel: kernels.find((kernel) => kernel.id === selectedKernelId),
    });
  };

  const sendRuntime = (requestId?: string) => {
    dispatch({
      type: 'runtime',
      requestId,
      path: currentNotebookPath,
      active: runtime.active,
      busy: runtime.busy,
      kernel_label: runtime.kernel_label,
      runtime_id: runtime.runtime_id,
      kernel_generation: runtime.kernel_generation ?? null,
      current_execution: runtime.current_execution ?? null,
    });
  };

  const findCellIndex = (cellId?: string) =>
    typeof cellId === 'string' ? cells.findIndex((cell) => cell.cell_id === cellId) : -1;

  const workerUrl = new URL('/media/preview-python-worker.js', window.location.href).toString();
  let previewWorker: Worker | null = null;
  let previewWorkerReady: Promise<void> | null = null;
  let previewWorkerInitialized = false;
  let activeExecution:
    | {
        cellIndex: number;
        cellId: string;
        outputs: NotebookOutput[];
        resolve: (interrupted: boolean) => void;
      }
    | null = null;

  const finishPreviewExecution = (
    cellIndex: number,
    outputs: NotebookOutput[],
    options?: { interrupted?: boolean; runtimeActive?: boolean },
  ) => {
    const interrupted = options?.interrupted ?? false;
    const runtimeActive = options?.runtimeActive ?? true;
    if (cellIndex >= 0 && cellIndex < cells.length) {
      const nextCell = {
        ...cells[cellIndex],
        execution_count: interrupted ? cells[cellIndex].execution_count : executionCount + 1,
        outputs,
      };
      cells[cellIndex] = withCellRuntimeProvenance(
        nextCell,
        runtime,
        outputs.some((output) => output.output_type === 'error') ? 'error' : 'ok',
      );
    }
    if (!interrupted) {
      executionCount += 1;
      persistNotebookExecutionCount();
    }
    runtime = { ...runtime, active: runtimeActive, busy: false, current_execution: null };
    dispatch({
      type: 'activity-update',
      events: [
        {
          event_type: 'cell-outputs-updated',
          cell_id: cells[cellIndex]?.cell_id,
          data: { cell: cloneCell(cells[cellIndex]) },
        },
        {
          event_type: 'execution-finished',
          cell_id: cells[cellIndex]?.cell_id,
        },
      ],
      runtime: { active: runtimeActive, busy: false, current_execution: null },
      cursor: Date.now(),
    });
    sendRuntime();
  };

  const bootPreviewWorker = () => {
    previewWorker?.terminate();
    if (previewWorkerInitialized) {
      previewKernelGeneration += 1;
    } else {
      previewWorkerInitialized = true;
    }
    runtime = {
      ...runtime,
      active: false,
      busy: false,
      current_execution: null,
      runtime_id: previewRuntimeId,
      kernel_generation: previewKernelGeneration,
    };
    previewWorker = new Worker(workerUrl);
    previewWorkerReady = new Promise((resolve, reject) => {
      if (!previewWorker) {
        reject(new Error('Preview worker unavailable'));
        return;
      }
      const onMessage = (event: MessageEvent<any>) => {
        const payload = event.data ?? {};
        if (payload.type === 'ready') {
          runtime = {
            ...runtime,
            active: false,
            busy: false,
            kernel_label: kernels.find((kernel) => kernel.id === selectedKernelId)?.label ?? runtime.kernel_label,
          };
          previewWorker?.removeEventListener('message', onMessage);
          sendRuntime();
          resolve();
        } else if (payload.type === 'init-error') {
          runtime = { ...runtime, active: false, busy: false, current_execution: null };
          previewWorker?.removeEventListener('message', onMessage);
          sendRuntime();
          reject(new Error(payload.message ?? 'Failed to initialize preview runtime'));
        }
      };
      previewWorker.addEventListener('message', onMessage);
      previewWorker.postMessage({ type: 'init' });
    });

    previewWorker.addEventListener('message', (event: MessageEvent<any>) => {
      if (!activeExecution) return;
      const payload = event.data ?? {};
      if (payload.type === 'stream' && typeof payload.text === 'string' && payload.text) {
        activeExecution.outputs.push({
          output_type: 'stream',
          name: payload.name === 'stderr' ? 'stderr' : 'stdout',
          text: payload.text,
        });
        return;
      }
      if (payload.type === 'completed') {
        const outputs = [...activeExecution.outputs];
        if (typeof payload.resultText === 'string' && payload.resultText.trim()) {
          outputs.push({
            output_type: 'execute_result',
            data: { 'text/plain': payload.resultText },
          });
        }
        const { cellIndex, resolve } = activeExecution;
        activeExecution = null;
        finishPreviewExecution(cellIndex, outputs, { interrupted: false, runtimeActive: true });
        resolve(false);
        return;
      }
      if (payload.type === 'failed') {
        const outputs = [
          ...activeExecution.outputs,
          {
            output_type: 'error',
            ename: payload.ename ?? 'PythonError',
            evalue: payload.evalue ?? 'Execution failed',
            traceback: Array.isArray(payload.traceback) ? payload.traceback : [String(payload.evalue ?? 'Execution failed')],
          },
        ];
        const { cellIndex, resolve } = activeExecution;
        activeExecution = null;
        finishPreviewExecution(cellIndex, outputs, { interrupted: false, runtimeActive: true });
        resolve(false);
      }
    });
  };

  const ensurePreviewWorker = async () => {
    if (!previewWorker || !previewWorkerReady) {
      bootPreviewWorker();
    }
    await previewWorkerReady;
  };

  const runPreviewCell = async (cellIndex: number, requestId?: string) => {
    const cell = cells[cellIndex];
    if (!cell || cell.cell_type !== 'code') return false;
    return new Promise<boolean>((resolve) => {
      activeExecution = {
        cellIndex,
        cellId: cell.cell_id,
        outputs: [],
        resolve,
      };
      runtime = {
        ...runtime,
        active: true,
        busy: true,
        current_execution: {
          cell_id: cell.cell_id,
          cell_index: cellIndex,
          source_preview: cell.source.split('\n')[0]?.slice(0, 80) ?? '',
          owner: 'preview',
        },
      };
      sendRuntime();
      void (async () => {
        try {
          await ensurePreviewWorker();
          if (!activeExecution || activeExecution.cellId !== cell.cell_id) return;
          dispatch({
            type: 'execute-started',
            requestId,
            cell_id: cell.cell_id,
          });
          previewWorker?.postMessage({ type: 'execute', code: cell.source });
        } catch (error) {
          if (!activeExecution || activeExecution.cellId !== cell.cell_id) return;
          activeExecution = null;
          finishPreviewExecution(cellIndex, [
            {
              output_type: 'error',
              ename: 'PreviewRuntimeError',
              evalue: error instanceof Error ? error.message : String(error),
              traceback: [error instanceof Error ? error.message : String(error)],
            },
          ], { interrupted: false, runtimeActive: false });
          resolve(false);
        }
      })();
    });
  };

  const interruptPreviewExecution = (requestId?: string) => {
    if (!activeExecution) {
      dispatch({ type: 'ok', requestId });
      return;
    }
    const { cellIndex, outputs, resolve } = activeExecution;
    activeExecution = null;
    bootPreviewWorker();
    const interruptedOutputs = [
      ...outputs,
      {
        output_type: 'error',
        ename: 'KeyboardInterrupt',
        evalue: 'Execution interrupted',
        traceback: ['KeyboardInterrupt: Execution interrupted'],
      },
    ];
    finishPreviewExecution(cellIndex, interruptedOutputs, { interrupted: true, runtimeActive: false });
    dispatch({ type: 'ok', requestId });
    resolve(true);
  };

  const runAllPreviewCells = async (requestId?: string, restartFirst = false) => {
    if (restartFirst) {
      bootPreviewWorker();
      runtime = { ...runtime, active: false, busy: false, current_execution: null };
      executionCount = 0;
      persistNotebookExecutionCount();
      for (let index = 0; index < cells.length; index += 1) {
        if (cells[index].cell_type === 'code') {
          cells[index] = clearCellRuntimeProvenance({
            ...cells[index],
            execution_count: null,
            outputs: [],
          });
        }
      }
      sendContents();
    }
    dispatch({ type: 'ok', requestId });
    for (let index = 0; index < cells.length; index += 1) {
      if (cells[index].cell_type !== 'code') continue;
      const interrupted = await runPreviewCell(index);
      if (interrupted) break;
    }
  };

  bootPreviewWorker();

  return {
    postMessage(message) {
      const requestId = typeof message.requestId === 'string' ? message.requestId : undefined;
      switch (message.type) {
        case 'webview-ready':
          sendRuntime(requestId);
          sendContents(requestId);
          sendKernels(requestId);
          sendWorkspaceTree(requestId);
          break;
        case 'load-contents':
          sendContents(requestId);
          break;
        case 'get-workspace-tree':
          sendWorkspaceTree(requestId);
          break;
        case 'switch-notebook':
          if (typeof message.path !== 'string' || !switchPreviewNotebook(message.path)) {
            dispatch({
              type: 'error',
              requestId,
              message: 'Preview notebook not found.',
            });
            break;
          }
          if (activeExecution) {
            activeExecution = null;
            bootPreviewWorker();
          }
          runtime = { ...runtime, active: false, busy: false, current_execution: null };
          dispatch({ type: 'ok', requestId });
          sendWorkspaceTree();
          sendContents(requestId);
          sendRuntime();
          break;
        case 'get-kernels':
          sendKernels(requestId);
          break;
        case 'get-runtime':
          sendRuntime(requestId);
          break;
        case 'select-kernel':
          if (typeof message.kernel_id === 'string') {
            selectedKernelId = message.kernel_id;
            runtime = {
              ...runtime,
              kernel_label:
                kernels.find((kernel) => kernel.id === selectedKernelId)?.label ?? runtime.kernel_label,
            };
          }
          dispatch({ type: 'ok', requestId });
          sendKernels(requestId);
          sendRuntime();
          break;
        case 'flush-draft': {
          const index = findCellIndex(typeof message.cell_id === 'string' ? message.cell_id : undefined);
          if (index >= 0 && typeof message.source === 'string') {
            cells = applyDraftChangesToCells(cells, [{
              cell_id: message.cell_id as string,
              source: message.source,
            }]);
          }
          dispatch({ type: 'ok', requestId });
          sendContents();
          break;
        }
        case 'save-notebook': {
          const changes = normalizeDraftChanges(message.changes);
          if (changes.length === 0) {
            dispatch({ type: 'ok', requestId });
            break;
          }
          cells = applyDraftChangesToCells(cells, changes);
          dispatch({ type: 'ok', requestId });
          sendContents(requestId);
          break;
        }
        case 'lsp-complete':
          dispatch({
            type: 'lsp-completions',
            requestId,
            cell_id: typeof message.cell_id === 'string' ? message.cell_id : '',
            items: [],
          });
          break;
        case 'edit': {
          const operations = Array.isArray(message.operations) ? message.operations : [];
          for (const operation of operations) {
            if (!operation || typeof operation !== 'object') {
              continue;
            }
            const op = operation as Record<string, unknown>;
            if (op.op === 'insert') {
              const atIndex = typeof op.at_index === 'number' ? op.at_index : cells.length;
              const cellType = op.cell_type === 'markdown' ? 'markdown' : 'code';
              const cellId = `preview-${cellType}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
              cells.splice(Math.max(0, Math.min(atIndex, cells.length)), 0, {
                cell_id: cellId,
                cell_type: cellType,
                source: typeof op.source === 'string' ? op.source : '',
                outputs: [],
                execution_count: null,
              });
            } else if (op.op === 'delete') {
              const index = findCellIndex(typeof op.cell_id === 'string' ? op.cell_id : undefined);
              if (index >= 0) {
                cells.splice(index, 1);
              }
            } else if (op.op === 'replace-source') {
              const index = findCellIndex(typeof op.cell_id === 'string' ? op.cell_id : undefined);
              if (index >= 0 && typeof op.source === 'string') {
                const nextCell = {
                  ...cells[index],
                  source: op.source,
                };
                cells[index] = cells[index].cell_type === 'code'
                  ? { ...clearCellRuntimeProvenance(nextCell), outputs: [], execution_count: null }
                  : nextCell;
              }
            } else if (op.op === 'change-cell-type') {
              const index = findCellIndex(typeof op.cell_id === 'string' ? op.cell_id : undefined);
              if (index >= 0) {
                const nextType = op.cell_type === 'markdown' ? 'markdown' : 'code';
                const nextCell = clearCellRuntimeProvenance({
                  ...cells[index],
                  cell_type: nextType,
                  source: typeof op.source === 'string' ? op.source : cells[index].source,
                  outputs: [],
                  execution_count: null,
                });
                cells[index] = nextCell;
              }
            } else if (op.op === 'move') {
              const index = findCellIndex(typeof op.cell_id === 'string' ? op.cell_id : undefined);
              const toIndex = typeof op.to_index === 'number' ? op.to_index : index;
              if (index >= 0 && index !== toIndex) {
                const [cell] = cells.splice(index, 1);
                cells.splice(Math.max(0, Math.min(toIndex, cells.length)), 0, cell);
              }
            }
          }
          dispatch({ type: 'ok', requestId });
          sendContents();
          break;
        }
        case 'execute-cell': {
          const index = findCellIndex(typeof message.cell_id === 'string' ? message.cell_id : undefined);
          if (index < 0 || cells[index].cell_type !== 'code') {
            dispatch({ type: 'ok', requestId });
            break;
          }
          if (typeof message.source === 'string') {
            cells[index] = {
              ...cells[index],
              source: message.source,
            };
          }
          void runPreviewCell(index, requestId);
          break;
        }
        case 'interrupt-execution':
          interruptPreviewExecution(requestId);
          break;
        case 'execute-all':
          void runAllPreviewCells(requestId, false);
          break;
        case 'restart-and-run-all': {
          void runAllPreviewCells(requestId, true);
          break;
        }
        case 'restart-kernel':
          interruptPreviewExecution();
          bootPreviewWorker();
          runtime = { ...runtime, active: false, busy: false, current_execution: null };
          executionCount = 0;
          persistNotebookExecutionCount();
          for (let index = 0; index < cells.length; index += 1) {
            if (cells[index].cell_type === 'code') {
              cells[index] = {
                ...cells[index],
                execution_count: null,
                outputs: [],
              };
            }
          }
          dispatch({ type: 'ok', requestId });
          sendContents();
          sendRuntime();
          break;
        case 'open-external-link':
          if (typeof message.url === 'string') {
            window.open?.(message.url, '_blank', 'noopener,noreferrer');
          }
          dispatch({ type: 'ok', requestId });
          break;
        default:
          dispatch({ type: 'ok', requestId });
      }
    },
  };
}

// ── Host and markdown setup ──────────────────────────────

const vscode = window.acquireVsCodeApi?.();
const standaloneConfig = vscode ? null : readStandaloneConfig();
const standaloneHost = standaloneConfig ? createStandaloneHost(standaloneConfig) : null;
const hostApi: HostApi = vscode ?? standaloneHost ?? createPreviewHost();
const isBrowserCanvas = !vscode;
const showInterfaceKit = isBrowserCanvas && (standaloneConfig?.features.interfaceKit ?? true);
const showAgentation = isBrowserCanvas && (standaloneConfig?.features.agentation ?? true);

marked.use({
  gfm: true,
  breaks: true,
});

function nextRequestIdFactory() {
  let requestCounter = 0;
  return () => `req-${++requestCounter}`;
}

const nextRequestId = nextRequestIdFactory();

function normalizeText(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value.join('');
  }
  return value ?? '';
}

const ANSI_ESCAPE_RE = /[\u001B\u009B][[\]()#;?]*(?:(?:(?:[a-zA-Z\d]*(?:;[a-zA-Z\d]*)*)?\u0007)|(?:(?:\d{1,4}(?:;\d{0,4})*)?[\dA-PR-TZcf-nq-uy=><~]))/g;
const ORPHAN_ANSI_COLOR_RE = /\[(?:\d{1,3}(?:;\d{1,3})*)m/g;

function stripTerminalFormatting(value: string): string {
  return value
    .replace(ANSI_ESCAPE_RE, '')
    .replace(ORPHAN_ANSI_COLOR_RE, '')
    .replace(/\r\n?/g, '\n');
}

function cleanTextOutput(value: string | string[] | undefined): string {
  return stripTerminalFormatting(normalizeText(value));
}

function summarizeErrorOutput(output: NotebookOutput): {
  summary: string;
  details: string | null;
  copyText: string;
} {
  const ename = cleanTextOutput(output.ename).trim();
  const evalue = cleanTextOutput(output.evalue).trim();
  const traceback = stripTerminalFormatting(
    Array.isArray(output.traceback) ? output.traceback.join('\n') : output.traceback ?? '',
  ).trim();
  const tracebackLines = traceback
    .split('\n')
    .map((line) => line.trimEnd())
    .filter(Boolean);
  const derivedSummary = [...tracebackLines].reverse().find((line) => {
    const trimmed = line.trim();
    return trimmed.includes(':')
      && !trimmed.startsWith('Traceback ')
      && !trimmed.startsWith('File ')
      && !trimmed.startsWith('at ')
      && !trimmed.startsWith('http');
  });
  const summary = derivedSummary || [ename, evalue].filter(Boolean).join(': ') || 'Execution error';
  if (!traceback) {
    return { summary, details: null, copyText: summary };
  }

  let lines = tracebackLines.filter((line) => {
    const trimmed = line.trim();
    return !/^[-_]{5,}$/.test(trimmed) && !trimmed.startsWith('at ');
  });

  if (ename && lines[0]?.trim() === ename) {
    lines = lines.slice(1);
  }
  if (summary && lines[lines.length - 1]?.trim() === summary) {
    lines = lines.slice(0, -1);
  }

  const details = lines.join('\n').trim() || null;
  return {
    summary,
    details,
    copyText: details ? `${summary}\n\n${details}` : summary,
  };
}

async function copyTextToClipboard(value: string): Promise<boolean> {
  if (!value.trim()) {
    return false;
  }

  let clipboardError: unknown = null;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch (error) {
    clipboardError = error;
  }

  try {
    const textarea = document.createElement('textarea');
    textarea.value = value;
    textarea.setAttribute('readonly', 'true');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    const didCopy = document.execCommand('copy');
    document.body.removeChild(textarea);
    return didCopy;
  } catch (error) {
    console.warn('Failed to copy notebook output', clipboardError ?? error);
    return false;
  }
}

function getCopyableOutputText(output: NotebookOutput): string | null {
  if (output.output_type === 'error') {
    return summarizeErrorOutput(output).copyText;
  }
  if (output.output_type === 'stream') {
    const text = cleanTextOutput(output.text);
    return text.length > 0 ? text : null;
  }
  if (output.output_type === 'execute_result' || output.output_type === 'display_data') {
    const text = output.data?.['text/plain'];
    if (text) {
      const normalized = cleanTextOutput(text);
      return normalized.length > 0 ? normalized : null;
    }
  }

  const fallback = cleanTextOutput(JSON.stringify(output, null, 2));
  return fallback.length > 0 ? fallback : null;
}

function joinOutputCopyText(parts: string[]): string | null {
  let combined = '';
  for (const part of parts) {
    if (!part) {
      continue;
    }
    if (!combined) {
      combined = part;
      continue;
    }
    combined += combined.endsWith('\n') || part.startsWith('\n') ? part : `\n${part}`;
  }
  return combined || null;
}

function renderMarkdown(source: string) {
  return DOMPurify.sanitize(marked.parse(source) as string);
}

function renderRichHtml(html: string) {
  return DOMPurify.sanitize(html);
}

function autoResize(element: HTMLTextAreaElement | null) {
  if (!element) {
    return;
  }
  element.style.height = '0px';
  element.style.height = `${Math.max(96, element.scrollHeight)}px`;
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

function selectionStartsInLeadingWhitespace(value: string, start: number): boolean {
  const lineStart = value.lastIndexOf('\n', Math.max(0, start - 1)) + 1;
  const prefix = value.slice(lineStart, start);
  return prefix.trim().length === 0;
}

function selectionTouchesMultipleLines(value: string, start: number, end: number): boolean {
  return value.slice(start, end).includes('\n');
}

function isPlaceholderCodeCell(cell: NotebookCell | undefined, draftSource?: string): boolean {
  if (!cell || cell.cell_type !== 'code') {
    return false;
  }
  const source = (draftSource ?? cell.source ?? '').trim();
  return source.length === 0 && cell.execution_count == null && (cell.outputs?.length ?? 0) === 0;
}

// ── Dark mode detection ──────────────────────────────────

function useDarkMode(): boolean {
  const [dark, setDark] = useState(() => {
    // VS Code sets body class — check that first
    if (document.body.classList.contains('vscode-dark') || document.body.classList.contains('vscode-high-contrast')) return true;
    if (document.body.classList.contains('vscode-light')) return false;
    // Fallback: system preference (for preview.html / standalone)
    return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false;
  });

  useEffect(() => {
    // Watch VS Code body class changes
    const observer = new MutationObserver(() => {
      if (document.body.classList.contains('vscode-dark') || document.body.classList.contains('vscode-high-contrast')) { setDark(true); return; }
      if (document.body.classList.contains('vscode-light')) { setDark(false); return; }
    });
    observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });

    // Watch system preference changes (for preview / standalone)
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)');
    const handleMq = (e: MediaQueryListEvent) => {
      // Only use system pref if no VS Code class is set
      if (!document.body.classList.contains('vscode-dark') && !document.body.classList.contains('vscode-light') && !document.body.classList.contains('vscode-high-contrast')) {
        setDark(e.matches);
      }
    };
    mq?.addEventListener('change', handleMq);

    return () => { observer.disconnect(); mq?.removeEventListener('change', handleMq); };
  }, []);

  return dark;
}

// ── OutputView ───────────────────────────────────────────

function TextualOutputBlock({
  tone = 'default',
  children,
}: {
  tone?: 'default' | 'error';
  children: React.ReactNode;
}) {
  const d = useDesign();

  return (
    <div
      data-output-text-block={tone}
      style={{
        position: 'relative',
        fontFamily: MONO_FONT,
        fontSize: d.outputFontSize,
        lineHeight: CODE_LINE_HEIGHT,
        padding: tone === 'error' ? `${d.outputPadding}px` : `0 ${d.outputPadding}px`,
        color: tone === 'error' ? '#e11d48' : 'var(--cds-text-primary)',
        background: tone === 'error' ? 'rgba(225,29,72,0.025)' : 'transparent',
        border: tone === 'error' ? '1px solid rgba(225,29,72,0.15)' : 'none',
      }}
    >
      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{children}</div>
    </div>
  );
}

function OutputCopyButton({ copyText }: { copyText: string }) {
  const [copied, setCopied] = useState(false);
  const resetTimerRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
    }
  }, []);

  const handleCopy = useCallback(async () => {
    const didCopy = await copyTextToClipboard(copyText);
    if (!didCopy) {
      return;
    }
    setCopied(true);
    if (resetTimerRef.current != null) {
      window.clearTimeout(resetTimerRef.current);
    }
    resetTimerRef.current = window.setTimeout(() => {
      setCopied(false);
      resetTimerRef.current = null;
    }, 1200);
  }, [copyText]);

  return (
    <button
      type="button"
      className="output-copy-button"
      data-output-copy="true"
      data-copied={copied ? 'true' : 'false'}
      aria-label={copied ? 'Output copied' : 'Copy output'}
      title={copied ? 'Copied' : 'Copy output'}
      onClick={handleCopy}
    >
      <span className="output-copy-button__icon-stack" aria-hidden="true">
        <Copy className="output-copy-button__icon output-copy-button__icon--copy" size={14} />
        <CheckmarkFilled className="output-copy-button__icon output-copy-button__icon--check" size={14} />
      </span>
    </button>
  );
}

function OutputView({
  output,
  onOpenExternal,
}: {
  output: NotebookOutput;
  onOpenExternal: (href: string) => void;
}) {
  const d = useDesign();

  if (output.output_type === 'error') {
    const { summary, details } = summarizeErrorOutput(output);
    return (
      <TextualOutputBlock tone="error">
        <div style={{ fontWeight: 600 }}>{summary}</div>
        {details ? <div style={{ marginTop: 8 }}>{details}</div> : null}
      </TextualOutputBlock>
    );
  }

  if (output.output_type === 'stream') {
    const text = cleanTextOutput(output.text);
    return (
      <TextualOutputBlock tone={output.name === 'stderr' ? 'error' : 'default'}>
        {text}
      </TextualOutputBlock>
    );
  }

  if (output.output_type === 'execute_result' || output.output_type === 'display_data') {
    const data = output.data ?? {};
    if (data['text/html']) {
      return (
        <div
          className="rich-output"
          style={{
            padding: `${d.outputPadding}px`,
            fontSize: d.outputFontSize,
            color: 'var(--cds-text-secondary)',
          }}
          onClickCapture={(event) => {
            const target = event.target as HTMLElement | null;
            const anchor = target?.closest('a[href]') as HTMLAnchorElement | null;
            if (!anchor) return;
            event.preventDefault();
            onOpenExternal(anchor.href);
          }}
          dangerouslySetInnerHTML={{
            __html: renderRichHtml(normalizeText(data['text/html'])),
          }}
        />
      );
    }

    if (data['text/plain']) {
      const text = cleanTextOutput(data['text/plain']);
      return (
        <TextualOutputBlock>{text}</TextualOutputBlock>
      );
    }
  }

  const fallback = cleanTextOutput(JSON.stringify(output, null, 2));

  return (
    <TextualOutputBlock>{fallback}</TextualOutputBlock>
  );
}

// ── CellCard ─────────────────────────────────────────────

type CellCardProps = {
  cell: NotebookCell;
  index: number;
  source: string;
  isSelected: boolean;
  isQueued: boolean;
  isExecuting: boolean;
  hasFailedExecution: boolean;
  hasPausedExecution: boolean;
  showRuntimeStatus: boolean;
  runtimeStatus: RuntimeInfo;
  isMarkdownEditing: boolean;
  onFocusCell: (index: number, mode: 'command' | 'edit') => void;
  onBlurCell: (
    cell: NotebookCell,
    source: string,
    index: number,
    exitMarkdown?: boolean,
    blurActive?: boolean,
  ) => void;
  onSourceChange: (cellId: string, value: string) => void;
  requestCompletions: (
    source: string,
    offset: number,
    options?: CompletionRequestOptions,
  ) => Promise<LspCompletionItem[]>;
  onEditorKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>, cell: NotebookCell, index: number) => void;
  onRunCell: (index: number) => void;
  onRunCellAndAdvance: (index: number | string) => void;
  onEnterMarkdownEdit: (cellId: string, index: number) => void;
  onOpenExternal: (href: string) => void;
  bindTextarea: (cellId: string, element: HTMLTextAreaElement | null) => void;
  bindCodeMirror: (cellId: string, handle: CodeMirrorCellHandle | null) => void;
  onMoveToAdjacentCell: (index: number, delta: number) => boolean;
  diagnostics: CellDiagnostic[];
  onMoveCell: (index: number, delta: number) => void;
  onDeleteCell: (index: number) => void;
  onInsertBelow: (index: number, cellType: 'code' | 'markdown') => void;
  compactToolbar: boolean;
  isFirstCell?: boolean;
  isLastCell?: boolean;
  completedTime?: number;
  executionStartedAt?: number | null;
  dark: boolean;
};

function CellCard({
  cell,
  index,
  source,
  isSelected,
  isQueued,
  isExecuting,
  hasFailedExecution,
  hasPausedExecution,
  showRuntimeStatus,
  runtimeStatus,
  isMarkdownEditing,
  onFocusCell,
  onBlurCell,
  onSourceChange,
  requestCompletions,
  onEditorKeyDown,
  onRunCell,
  onRunCellAndAdvance,
  onEnterMarkdownEdit,
  onOpenExternal,
  bindTextarea,
  bindCodeMirror,
  onMoveToAdjacentCell,
  diagnostics,
  onMoveCell,
  onDeleteCell,
  onInsertBelow,
  compactToolbar,
  isFirstCell = false,
  isLastCell = false,
  completedTime,
  executionStartedAt,
  dark,
}: CellCardProps) {
  const d = useDesign();
  const [hovered, setHovered] = useState(false);
  const [addControlsVisible, setAddControlsVisible] = useState(false);
  const [runningNow, setRunningNow] = useState(() => Date.now());
  const containerRef = useRef<HTMLDivElement>(null);
  const insertButtonStyle = useMemo(
    () => ({
      ...createToolbarOutlineButtonStyle(compactToolbar),
      height: 30,
      fontSize: 13,
      padding: compactToolbar ? '0 9px' : '0 11px',
      gap: 5,
      background: 'var(--cds-background)',
    }),
    [compactToolbar],
  );
  const isActive = isSelected;
  const isMarkdown = cell.cell_type === 'markdown';
  const isCode = cell.cell_type === 'code';
  const hasPersistedExecutionResult =
    cell.execution_count != null && (cell.outputs?.length ?? 0) > 0;
  const matchesCurrentRuntime = cellMatchesCurrentRuntime(cell, runtimeStatus);
  const runtimeProvenance = getCellRuntimeProvenance(cell);
  const statusKind: CellStatusKind = deriveCellStatusKind({
    isQueued,
    isExecuting,
    isPaused: hasPausedExecution,
    hasLocalFailure: hasFailedExecution,
    hasCompletedThisSession: completedTime != null,
    hasLiveRuntimeContext: showRuntimeStatus,
    hasRuntimeMatchedFailure: matchesCurrentRuntime && runtimeProvenance?.status === 'error',
    hasRuntimeMatchedCompletion: matchesCurrentRuntime && hasPersistedExecutionResult && runtimeProvenance?.status === 'ok',
  });
  const shouldKeepEditorFocus = useCallback((target: EventTarget | null) => {
    if (!(target instanceof HTMLElement)) {
      return false;
    }
    return Boolean(target.closest(
      '.cm-editor, .cm-content, .cm-line, textarea, button, select, input, a[href], [contenteditable="true"]',
    ));
  }, []);

  useEffect(() => {
    if (!isExecuting) return;
    setRunningNow(Date.now());
    const intervalId = window.setInterval(() => setRunningNow(Date.now()), 250);
    return () => window.clearInterval(intervalId);
  }, [isExecuting]);

  const cellBorder = isMarkdown && !isActive
    ? '1px solid transparent'
    : `1px solid ${isExecuting ? 'var(--cds-border-subtle)' : isActive ? SELECTION_COLOR : 'var(--cds-border-subtle)'}`;

  const cellShadow = !isExecuting && isActive
    ? `0 0 0 ${FOCUS_RING_WIDTH}px rgba(${selRgb},${SELECTION_RING_OPACITY})`
    : 'none';
  const outputCopyText = useMemo(() => {
    const parts = (cell.outputs ?? [])
      .map((output) => getCopyableOutputText(output))
      .filter((value): value is string => value != null);
    return joinOutputCopyText(parts);
  }, [cell.outputs]);

  return (
    <div
      ref={containerRef}
      data-cell-id={cell.cell_id}
      onMouseEnter={() => {
        setHovered(true);
        setAddControlsVisible(true);
      }}
      onMouseLeave={() => {
        setHovered(false);
        setAddControlsVisible(false);
      }}
      onClick={(event) => {
        if (shouldKeepEditorFocus(event.target)) {
          return;
        }
        onFocusCell(index, 'command');
        containerRef.current?.focus();
      }}
      tabIndex={-1}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        gap: 0,
        outline: 'none',
      }}
    >
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 0 }}>
        {/* Left gutter — selection indicator + run button */}
        <div style={{
          width: CELL_GUTTER_WIDTH,
          flexShrink: 0,
          position: 'relative',
          display: 'flex',
          justifyContent: 'flex-start',
          alignSelf: 'stretch',
          paddingTop: 0,
          paddingLeft: 6,
        }}>
          {isActive || hovered ? (
            <div
              style={{
                position: 'absolute',
                left: 2,
                top: 0,
                bottom: 0,
                width: 3,
                borderRadius: 999,
                background: isActive ? SELECTION_COLOR : 'var(--cds-border-subtle)',
                opacity: isActive ? 1 : 0.9,
              }}
            />
          ) : null}
          {isCode ? (
            <button
              onClick={(e) => { e.stopPropagation(); onRunCell(index); }}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: 25,
                height: 23,
                border: 'none',
                borderRadius: 4,
                background: 'transparent',
                cursor: 'pointer',
                color: hovered || isActive ? 'var(--cds-text-secondary)' : 'transparent',
                transition: 'color 100ms, background 100ms',
              }}
              onMouseOver={(e) => {
                e.currentTarget.style.color = SELECTION_COLOR;
                e.currentTarget.style.background = 'var(--cds-layer-accent)';
              }}
              onMouseOut={(e) => {
                e.currentTarget.style.color = (hovered || isActive) ? 'var(--cds-text-secondary)' : 'transparent';
                e.currentTarget.style.background = 'transparent';
              }}
              title="Run cell (Shift+Enter)"
            >
              <Play size={18} />
            </button>
          ) : null}
        </div>

        {/* Cell body */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
      <article
        className={isExecuting ? 'cell-executing' : undefined}
        style={{
          position: 'relative',
          flex: 1,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: d.cornerRadius,
          border: cellBorder,
          background: isMarkdown ? 'var(--cds-background)' : 'var(--cds-layer)',
          boxShadow: cellShadow,
          cursor: 'pointer',
          overflow: 'visible',
        }}
      >
      {/* Floating label — top-left */}
      {(isCode || false /* markdownOutline */) && (
        <span style={{
          position: 'absolute', top: -8, left: 10, zIndex: 1,
          display: 'inline-flex', alignItems: 'center', gap: 5,
          background: 'var(--cds-layer)', padding: '1px 6px',
          fontSize: d.labelFontSize, lineHeight: '14px',
          color: isActive ? SELECTION_COLOR : 'var(--cds-text-helper)',
          pointerEvents: 'none',
        }}>
          {isCode && (
            <code style={{
              fontFamily: MONO_FONT,
              display: 'inline-block',
              lineHeight: '14px',
              padding: 0,
              borderRadius: 0,
              background: 'transparent',
            }}>
              {cell.execution_count != null ? `[${cell.execution_count}]` : '[ ]'}
            </code>
          )}
          <span>{isCode ? 'Python' : 'Markdown'}</span>
        </span>
      )}

      {/* Hover controls — top-right */}
      {isCode && (
        <div style={{
          position: 'absolute', top: CONTROLS_TOP_PADDING, right: CELL_PADDING, zIndex: 2,
          display: 'flex', gap: 2, alignItems: 'center',
          opacity: hovered ? 1 : 0,
          transition: 'opacity 70ms',
        }}>
          <button
            onClick={(e) => { e.stopPropagation(); onMoveCell(index, -1); }}
            disabled={isFirstCell}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 24, height: 24, border: 'none', borderRadius: 3,
              background: 'transparent', color: 'var(--cds-text-helper)',
              cursor: isFirstCell ? 'default' : 'pointer',
              opacity: isFirstCell ? 0.4 : 1,
            }}
          >
            <ChevronUp size={14} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onMoveCell(index, 1); }}
            disabled={isLastCell}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 24, height: 24, border: 'none', borderRadius: 3,
              background: 'transparent', color: 'var(--cds-text-helper)',
              cursor: isLastCell ? 'default' : 'pointer',
              opacity: isLastCell ? 0.4 : 1,
            }}
          >
            <ChevronDown size={14} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDeleteCell(index); }}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              width: 24, height: 24, border: 'none', borderRadius: 3,
              background: 'transparent', color: '#e11d48', cursor: 'pointer',
            }}
            onMouseOver={(e) => (e.currentTarget.style.background = 'var(--cds-layer-accent)')}
            onMouseOut={(e) => (e.currentTarget.style.background = 'transparent')}
          >
            <TrashCan size={14} />
          </button>
        </div>
      )}

      {/* Body — code cell */}
      {isCode ? (
        <CodeMirrorCell
          source={source}
          diagnostics={diagnostics}
          onSourceChange={(value) => onSourceChange(cell.cell_id, value)}
          requestCompletions={requestCompletions}
          onFocus={() => onFocusCell(index, 'edit')}
          onBlur={(value) => onBlurCell(cell, value, index)}
          onRunCell={() => onRunCellAndAdvance(cell.cell_id)}
          onEscape={() => onBlurCell(cell, source, index, false, true)}
          onMoveToAdjacentCell={(direction) => onMoveToAdjacentCell(index, direction === 'up' ? -1 : 1)}
          fontSize={d.codeFontSize}
          lineHeight={CODE_LINE_HEIGHT}
          topPadding={d.codeTopPadding}
          bottomPadding={Math.round(CELL_PADDING * 0.5)}
          dark={dark}
          bindHandle={(handle) => bindCodeMirror(cell.cell_id, handle)}
        />
      ) : null}

      {isMarkdown ? (
        isMarkdownEditing ? (
          <textarea
            ref={(el) => bindTextarea(cell.cell_id, el)}
            value={source}
            spellCheck={false}
            onFocus={() => onFocusCell(index, 'edit')}
            onBlur={(e) => onBlurCell(cell, e.target.value, index, true)}
            onChange={(e) => {
              onSourceChange(cell.cell_id, e.target.value);
              autoResize(e.target);
            }}
            onKeyDown={(e) => onEditorKeyDown(e, cell, index)}
            style={{
              fontFamily: MONO_FONT,
              fontSize: d.codeFontSize,
              lineHeight: CODE_LINE_HEIGHT,
              padding: `${CELL_PADDING * 0.5}px ${CELL_PADDING}px`,
              margin: 0,
              border: 'none', outline: 'none', resize: 'none',
              minHeight: 64,
              background: 'transparent', color: 'var(--cds-text-primary)',
              width: '100%',
            }}
          />
        ) : (
          <div
            className="markdown-content"
            onDoubleClick={() => onEnterMarkdownEdit(cell.cell_id, index)}
            onClickCapture={(event) => {
              const target = event.target as HTMLElement | null;
              const anchor = target?.closest('a[href]') as HTMLAnchorElement | null;
              if (!anchor) return;
              event.preventDefault();
              onOpenExternal(anchor.href);
            }}
            style={{
              padding: `${CELL_PADDING * 0.75}px ${MARKDOWN_LEFT_PADDING}px`,
              outline: 'none',
            }}
            dangerouslySetInnerHTML={{ __html: renderMarkdown(source) }}
          />
        )
      ) : null}

      {cell.cell_type === 'raw' ? (
        <div style={{
          fontFamily: MONO_FONT,
          fontSize: d.codeFontSize,
          lineHeight: CODE_LINE_HEIGHT,
          padding: `${CELL_PADDING}px`,
          color: 'var(--cds-text-secondary)',
          border: '1px dashed var(--cds-border-subtle)',
          borderRadius: d.cornerRadius,
          background: 'var(--cds-layer-accent)',
        }}>
          {source}
        </div>
      ) : null}

      {/* Status bar */}
      {isCode && statusKind ? (
        <div style={{
          maxHeight: 24,
          opacity: 1,
          padding: `2px ${CELL_PADDING}px 4px ${CELL_PADDING}px`,
          fontSize: d.labelFontSize, lineHeight: '16px',
          color: 'var(--cds-text-helper)',
          overflow: 'hidden',
          transition: 'max-height 160ms ease, opacity 140ms ease, padding 160ms ease',
        }}
        data-cell-status={statusKind}
        >
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            minHeight: 16,
            transform: 'translateY(0)',
            transition: 'transform 180ms cubic-bezier(0.22, 1, 0.36, 1)',
          }}>
            {statusKind === 'queued' ? (
              <>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: 'var(--cds-text-secondary)',
                  }}
                  dangerouslySetInnerHTML={{
                    __html: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3.5 4.5H12.5M3.5 8H9.5M3.5 11.5H8.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
                  }}
                />
                <span>Queued</span>
              </>
            ) : statusKind === 'running' ? (
              <>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  border: '1.5px solid var(--cds-border-subtle)',
                  borderTopColor: '#3b82f6', flexShrink: 0,
                  animation: 'spin 0.6s linear infinite',
                }} />
                <span>Running</span>
                {executionStartedAt != null ? (
                  <span style={{ color: 'var(--cds-text-secondary)' }}>
                    {formatExecutionDuration(runningNow - executionStartedAt)}
                  </span>
                ) : null}
              </>
            ) : statusKind === 'failed' ? (
              <>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#e11d48',
                  }}
                  dangerouslySetInnerHTML={{
                    __html: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M4.5 4.5L11.5 11.5M11.5 4.5L4.5 11.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
                  }}
                />
                <span>Error</span>
              </>
            ) : statusKind === 'paused' ? (
              <>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#d97706',
                  }}
                  dangerouslySetInnerHTML={{
                    __html: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M5.25 4.5V11.5M10.75 4.5V11.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
                  }}
                />
                <span>Paused</span>
              </>
            ) : statusKind === 'completed' ? (
              <>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: '#5ba67c',
                  }}
                  dangerouslySetInnerHTML={{
                    __html: '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3.5 8.5L6.5 11.5L12.5 4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
                  }}
                />
                <span>Completed</span>
                {completedTime != null ? (
                  <span style={{ color: 'var(--cds-text-secondary)' }}>
                    {formatExecutionDuration(completedTime)}
                  </span>
                ) : null}
              </>
            ) : null}
          </div>
        </div>
      ) : null}

      <div
        data-insert-hotspot="true"
        style={{
          position: 'absolute',
          left: '50%',
          bottom: -58,
          transform: 'translateX(-50%)',
          width: '112%',
          maxWidth: 860,
          minWidth: 520,
          height: 132,
          borderRadius: 999,
          background: 'transparent',
          zIndex: 3,
          pointerEvents: 'none',
        }}
      />
      <div
        data-insert-controls="true"
        onMouseEnter={() => setAddControlsVisible(true)}
        onMouseLeave={() => setAddControlsVisible(false)}
        style={{
          position: 'absolute',
          left: '50%',
          bottom: -15,
          zIndex: 4,
          display: 'flex',
          gap: 8,
          opacity: addControlsVisible ? 1 : 0,
          transform: 'translateX(-50%)',
          transition: 'opacity 90ms ease',
          pointerEvents: addControlsVisible ? 'auto' : 'none',
        }}
      >
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onInsertBelow(index, 'code');
          }}
          style={insertButtonStyle}
        >
          <Add size={14} /> Code
        </button>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onInsertBelow(index, 'markdown');
          }}
          style={insertButtonStyle}
        >
          <Add size={14} /> Markdown
        </button>
      </div>

    </article>
        </div>
      </div>

      {/* Outputs — outside cell border, on page bg */}
      <div
        style={{
          position: 'relative',
          paddingLeft: CELL_GUTTER_WIDTH,
          paddingRight: outputCopyText ? 42 : 0,
          paddingTop: 8,
        }}
      >
        {outputCopyText ? (
          <div style={{ position: 'absolute', top: 8, right: 0, zIndex: 3 }}>
            <OutputCopyButton copyText={outputCopyText} />
          </div>
        ) : null}
        {cell.outputs?.map((output, outputIndex) => (
          <OutputView
            key={`${cell.cell_id}-output-${outputIndex}`}
            output={output}
            onOpenExternal={onOpenExternal}
          />
        ))}
      </div>
    </div>
  );
}

const MemoCellCard = memo(CellCard, (prev, next) => (
  prev.cell === next.cell &&
  prev.index === next.index &&
  prev.source === next.source &&
  prev.isSelected === next.isSelected &&
  prev.isQueued === next.isQueued &&
  prev.isExecuting === next.isExecuting &&
  prev.hasFailedExecution === next.hasFailedExecution &&
  prev.hasPausedExecution === next.hasPausedExecution &&
  prev.showRuntimeStatus === next.showRuntimeStatus &&
  prev.isMarkdownEditing === next.isMarkdownEditing &&
  prev.diagnostics === next.diagnostics &&
  prev.compactToolbar === next.compactToolbar &&
  prev.isFirstCell === next.isFirstCell &&
  prev.isLastCell === next.isLastCell &&
  prev.completedTime === next.completedTime &&
  prev.executionStartedAt === next.executionStartedAt &&
  prev.dark === next.dark
));

// ── App ──────────────────────────────────────────────────

function App() {
  const design = useMemo<DesignParams>(() => DEFAULT_DESIGN, []);

  const [cells, setCells] = useState<NotebookCell[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [selectedIndices, setSelectedIndices] = useState<number[]>([0]);
  const [mode, setMode] = useState<'command' | 'edit'>('command');
  const [editingMarkdownId, setEditingMarkdownId] = useState<string | null>(null);
  const [queuedIds, setQueuedIds] = useState<string[]>([]);
  const [executingIds, setExecutingIds] = useState<string[]>([]);
  const [failedCellIds, setFailedCellIds] = useState<string[]>([]);
  const [pausedCellIds, setPausedCellIds] = useState<string[]>([]);
  const executionStartRef = useRef(new Map<string, number>());
  const [completedTimes, setCompletedTimes] = useState<Record<string, number>>({});
  const [kernels, setKernels] = useState<Kernel[]>([]);
  const [selectedKernelId, setSelectedKernelId] = useState('');
  const [kernelMenuOpen, setKernelMenuOpen] = useState(false);
  const [kernelStatus, setKernelStatus] = useState<RuntimeInfo>({ busy: false });
  const [loading, setLoading] = useState(true);
  const [showLoadingPlaceholder, setShowLoadingPlaceholder] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [errorIsConflict, setErrorIsConflict] = useState(false);
  const [diagnosticsByCell, setDiagnosticsByCell] = useState<Record<string, CellDiagnostic[]>>({});
  const [lspStatus, setLspStatus] = useState<LspStatus | null>(null);
  const [workspaceTree, setWorkspaceTree] = useState<WorkspaceTreeNode | null>(null);
  const [workspaceName, setWorkspaceName] = useState('');
  const [activeNotebookPath, setActiveNotebookPath] = useState<string | null>(standaloneConfig?.notebookPath ?? null);
  const [explorerExpandedPaths, setExplorerExpandedPaths] = useState<Set<string>>(new Set(
    collectExplorerAncestorPaths(standaloneConfig?.notebookPath ?? null),
  ));
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
  const [toolbarWidth, setToolbarWidth] = useState(0);
  const canStop = Boolean(kernelStatus.busy || kernelStatus.current_execution || queuedIds.length > 0 || executingIds.length > 0);
  const hasLiveRuntime = Boolean(
    kernelStatus.active ||
    kernelStatus.busy ||
    kernelStatus.current_execution ||
    queuedIds.length > 0 ||
    executingIds.length > 0,
  );

  const systemDark = useDarkMode();
  const dark = themeMode === 'system' ? systemDark : themeMode === 'dark';
  const carbonTheme: CarbonThemeName = dark ? 'g100' : 'g10';
  const toolbarDensity = toolbarWidth > 0 && toolbarWidth < 900
    ? 'tight'
    : toolbarWidth > 0 && toolbarWidth < 1220
      ? 'compact'
      : 'roomy';
  const compactToolbar = toolbarDensity !== 'roomy';
  const tightToolbar = toolbarDensity === 'tight';
  const stopLabel = tightToolbar ? '' : 'Stop';
  const restartLabel = tightToolbar ? '' : 'Restart';
  const runAllLabel = tightToolbar ? 'Run' : 'Run All';
  const restartRunAllLabel = tightToolbar ? 'Re-run' : compactToolbar ? 'Restart + Run' : 'Restart & Run All';

  useEffect(() => {
    try {
      window.localStorage.setItem(THEME_MODE_STORAGE_KEY, themeMode);
    } catch {
      // Ignore persistence failures in constrained webview environments.
    }
  }, [themeMode]);

  useEffect(() => {
    try {
      window.localStorage.setItem(EXPLORER_COLLAPSED_STORAGE_KEY, explorerCollapsed ? 'true' : 'false');
    } catch {
      // Ignore persistence failures in constrained webview environments.
    }
  }, [explorerCollapsed]);

  useEffect(() => {
    if (!loading) {
      setShowLoadingPlaceholder(false);
      return;
    }

    const timerId = window.setTimeout(() => {
      setShowLoadingPlaceholder(true);
    }, 120);

    return () => window.clearTimeout(timerId);
  }, [loading]);

  const textareasRef = useRef(new Map<string, HTMLTextAreaElement | null>());
  const codeMirrorRef = useRef(new Map<string, CodeMirrorCellHandle | null>());
  const lspSyncTimersRef = useRef(new Map<string, number>());
  const toolbarContentRef = useRef<HTMLDivElement | null>(null);
  const kernelMenuRef = useRef<HTMLDivElement | null>(null);
  const lastDPressRef = useRef(0);
  const pendingCellActivationRef = useRef<{ index: number; mode: 'command' | 'edit' } | null>(null);
  const pendingAdvanceRef = useRef<{ cellId: string; awaitingInsert: boolean } | null>(null);
  const completionRequestsRef = useRef(new Map<string, {
    resolve: (items: LspCompletionItem[]) => void;
    reject: (reason?: unknown) => void;
    timerId: number;
  }>());
  const ignoredBlurCellIdRef = useRef<string | null>(null);
  const stateRef = useRef({
    cells,
    drafts,
    focusedIndex,
    selectedIndices,
    mode,
    editingMarkdownId,
    queuedIds,
    executingIds,
    failedCellIds,
    pausedCellIds,
  });
  stateRef.current = {
    cells,
    drafts,
    focusedIndex,
    selectedIndices,
    mode,
    editingMarkdownId,
    queuedIds,
    executingIds,
    failedCellIds,
    pausedCellIds,
  };

  const updateExecutionStateRef = useCallback((next: {
    queuedIds?: string[];
    executingIds?: string[];
    failedCellIds?: string[];
    pausedCellIds?: string[];
  }) => {
    stateRef.current = {
      ...stateRef.current,
      ...(next.queuedIds ? { queuedIds: next.queuedIds } : {}),
      ...(next.executingIds ? { executingIds: next.executingIds } : {}),
      ...(next.failedCellIds ? { failedCellIds: next.failedCellIds } : {}),
      ...(next.pausedCellIds ? { pausedCellIds: next.pausedCellIds } : {}),
    };
  }, []);

  useEffect(() => {
    const element = toolbarContentRef.current;
    if (!element) {
      return;
    }

    const updateWidth = () => {
      setToolbarWidth(element.clientWidth);
    };

    updateWidth();
    const observer = new ResizeObserver(() => updateWidth());
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const send = useCallback((message: Record<string, unknown>) => {
    hostApi.postMessage(message as HostMessage);
  }, []);

  const sendRequest = useCallback(
    (message: Record<string, unknown>) => {
      const requestId = (message.requestId as string | undefined) ?? nextRequestId();
      setErrorMessage(null);
      setErrorIsConflict(false);
      send({ ...message, requestId });
      return requestId;
    },
    [send],
  );

  const reconcileDrafts = useCallback((nextCells: NotebookCell[]) => {
    setDrafts((currentDrafts) => {
      const nextDrafts = { ...currentDrafts };
      for (const cell of nextCells) {
        if (nextDrafts[cell.cell_id] === cell.source) {
          delete nextDrafts[cell.cell_id];
        }
      }
      return nextDrafts;
    });
  }, []);

  const reconcileDiagnostics = useCallback((nextCells: NotebookCell[]) => {
    const nextIds = new Set(nextCells.map((cell) => cell.cell_id));
    setDiagnosticsByCell((currentDiagnostics) => {
      const nextDiagnostics: Record<string, CellDiagnostic[]> = {};
      for (const [cellId, diagnostics] of Object.entries(currentDiagnostics)) {
        if (nextIds.has(cellId)) {
          nextDiagnostics[cellId] = diagnostics;
        }
      }
      return nextDiagnostics;
    });
  }, []);

  const trimExecutionStateToCells = useCallback((nextCells: NotebookCell[]) => {
    const nextIds = new Set(nextCells.map((cell) => cell.cell_id));
    setQueuedIds((current) => current.filter((cellId) => nextIds.has(cellId)));
    setExecutingIds((current) => current.filter((cellId) => nextIds.has(cellId)));
    setFailedCellIds((current) => current.filter((cellId) => nextIds.has(cellId)));
    setPausedCellIds((current) => current.filter((cellId) => nextIds.has(cellId)));
    setCompletedTimes((current) => {
      const nextTimes: Record<string, number> = {};
      for (const [cellId, value] of Object.entries(current)) {
        if (nextIds.has(cellId)) {
          nextTimes[cellId] = value;
        }
      }
      return nextTimes;
    });
    for (const cellId of [...executionStartRef.current.keys()]) {
      if (!nextIds.has(cellId)) {
        executionStartRef.current.delete(cellId);
      }
    }
  }, []);

  const clearKernelExecutionState = useCallback(() => {
    executionStartRef.current.clear();
    updateExecutionStateRef({
      queuedIds: [],
      executingIds: [],
      failedCellIds: [],
      pausedCellIds: [],
    });
    setQueuedIds([]);
    setExecutingIds([]);
    setFailedCellIds([]);
    setPausedCellIds([]);
    setCompletedTimes({});
  }, [updateExecutionStateRef]);

  const queueCellIds = useCallback((cellIds: string[]) => {
    if (cellIds.length === 0) {
      return;
    }

    const nextQueuedIds = Array.from(new Set(cellIds));
    const queuedIds = Array.from(new Set([...stateRef.current.queuedIds, ...nextQueuedIds]));
    const executingIds = stateRef.current.executingIds.filter((cellId) => !nextQueuedIds.includes(cellId));
    const failedCellIds = stateRef.current.failedCellIds.filter((cellId) => !nextQueuedIds.includes(cellId));
    const pausedCellIds = stateRef.current.pausedCellIds.filter((cellId) => !nextQueuedIds.includes(cellId));
    updateExecutionStateRef({
      queuedIds,
      executingIds,
      failedCellIds,
      pausedCellIds,
    });
    setQueuedIds((current) => Array.from(new Set([...current, ...nextQueuedIds])));
    setExecutingIds((current) => current.filter((cellId) => !nextQueuedIds.includes(cellId)));
    setFailedCellIds((current) => current.filter((cellId) => !nextQueuedIds.includes(cellId)));
    setPausedCellIds((current) => current.filter((cellId) => !nextQueuedIds.includes(cellId)));
    setCompletedTimes((current) => {
      const nextTimes = { ...current };
      for (const cellId of nextQueuedIds) {
        delete nextTimes[cellId];
        executionStartRef.current.delete(cellId);
      }
      return nextTimes;
    });
  }, [updateExecutionStateRef]);

  const startCellIds = useCallback((cellIds: string[]) => {
    if (cellIds.length === 0) {
      return;
    }

    const nextExecutingIds = Array.from(new Set(cellIds));
    const startedAt = Date.now();
    const queuedIds = stateRef.current.queuedIds.filter((cellId) => !nextExecutingIds.includes(cellId));
    const executingIds = Array.from(new Set([...stateRef.current.executingIds, ...nextExecutingIds]));
    const failedCellIds = stateRef.current.failedCellIds.filter((cellId) => !nextExecutingIds.includes(cellId));
    const pausedCellIds = stateRef.current.pausedCellIds.filter((cellId) => !nextExecutingIds.includes(cellId));
    updateExecutionStateRef({
      queuedIds,
      executingIds,
      failedCellIds,
      pausedCellIds,
    });
    setQueuedIds((current) => current.filter((cellId) => !nextExecutingIds.includes(cellId)));
    setExecutingIds((current) => Array.from(new Set([...current, ...nextExecutingIds])));
    setFailedCellIds((current) => current.filter((cellId) => !nextExecutingIds.includes(cellId)));
    setPausedCellIds((current) => current.filter((cellId) => !nextExecutingIds.includes(cellId)));
    setCompletedTimes((current) => {
      const nextTimes = { ...current };
      for (const cellId of nextExecutingIds) {
        delete nextTimes[cellId];
      }
      return nextTimes;
    });
    for (const cellId of nextExecutingIds) {
      executionStartRef.current.set(cellId, executionStartRef.current.get(cellId) ?? startedAt);
    }
  }, [updateExecutionStateRef]);

  const applyCells = useCallback(
    (nextCells: NotebookCell[]) => {
      setLoading(false);
      startTransition(() => {
        setCells(nextCells);
      });
      reconcileDrafts(nextCells);
      reconcileDiagnostics(nextCells);
      trimExecutionStateToCells(nextCells);
      setFocusedIndex((current) =>
        nextCells.length === 0 ? 0 : Math.min(current, nextCells.length - 1),
      );
      setSelectedIndices((current) =>
        current.length === 0
          ? [0]
          : current
              .filter((index) => index < nextCells.length)
              .slice(0, Math.max(1, current.length)),
      );
    },
    [reconcileDiagnostics, reconcileDrafts, trimExecutionStateToCells],
  );

  const syncLspDraft = useCallback(
    (cellId: string, source: string) => {
      const timers = lspSyncTimersRef.current;
      const existingTimer = timers.get(cellId);
      if (existingTimer) {
        window.clearTimeout(existingTimer);
      }
      const timerId = window.setTimeout(() => {
        timers.delete(cellId);
        send({
          type: 'lsp-sync-cell',
          requestId: nextRequestId(),
          cell_id: cellId,
          source,
        });
      }, 120);
      timers.set(cellId, timerId);
    },
    [send],
  );

  useEffect(() => () => {
    for (const timerId of lspSyncTimersRef.current.values()) {
      window.clearTimeout(timerId);
    }
    lspSyncTimersRef.current.clear();
  }, []);

  useEffect(() => {
    if (!kernelMenuOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node | null;
      if (target && kernelMenuRef.current?.contains(target)) {
        return;
      }
      setKernelMenuOpen(false);
    };

    const handleWindowKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setKernelMenuOpen(false);
      }
    };

    window.addEventListener('mousedown', handlePointerDown);
    window.addEventListener('touchstart', handlePointerDown);
    window.addEventListener('keydown', handleWindowKeyDown);
    return () => {
      window.removeEventListener('mousedown', handlePointerDown);
      window.removeEventListener('touchstart', handlePointerDown);
      window.removeEventListener('keydown', handleWindowKeyDown);
    };
  }, [kernelMenuOpen]);

  useEffect(() => () => {
    for (const pending of completionRequestsRef.current.values()) {
      window.clearTimeout(pending.timerId);
      pending.reject(new Error('Canvas disposed'));
    }
    completionRequestsRef.current.clear();
  }, []);

  const flushDraft = useCallback(
    (cellId: string, source: string, cellIndex?: number) => {
      sendRequest({
        type: 'flush-draft',
        cell_id: cellId,
        source,
        ...(typeof cellIndex === 'number' ? { cell_index: cellIndex } : {}),
      });
    },
    [sendRequest],
  );

  const requestLspCompletions = useCallback(
    (
      cellId: string,
      source: string,
      offset: number,
      options: CompletionRequestOptions = {},
    ) => new Promise<LspCompletionItem[]>((resolve, reject) => {
      const requestId = nextRequestId();
      const timerId = window.setTimeout(() => {
        completionRequestsRef.current.delete(requestId);
        resolve([]);
      }, 2000);

      completionRequestsRef.current.set(requestId, {
        resolve: (items) => {
          window.clearTimeout(timerId);
          resolve(items);
        },
        reject: (reason) => {
          window.clearTimeout(timerId);
          reject(reason);
        },
        timerId,
      });

      send({
        type: 'lsp-complete',
        requestId,
        cell_id: cellId,
        source,
        offset,
        explicit: Boolean(options.explicit),
        trigger_character: options.triggerCharacter,
      });
    }),
    [send],
  );

  const flushActiveDraft = useCallback(() => {
    const current = stateRef.current;
    const focusedCell = current.cells[current.focusedIndex];
    if (!focusedCell) return;
    const draft = current.drafts[focusedCell.cell_id];
    if (draft !== undefined && draft !== focusedCell.source) {
      flushDraft(focusedCell.cell_id, draft, current.focusedIndex);
    }
  }, [flushDraft]);

  const saveNotebookDrafts = useCallback(() => {
    const current = stateRef.current;
    const changes = current.cells.flatMap((cell, index) => {
      const liveCodeSource = codeMirrorRef.current.get(cell.cell_id)?.getView()?.state.doc.toString();
      const liveMarkdownSource = textareasRef.current.get(cell.cell_id)?.value;
      const nextSource = liveCodeSource ?? liveMarkdownSource ?? current.drafts[cell.cell_id] ?? cell.source;
      if (nextSource === cell.source) {
        return [];
      }
      return [{ cell_id: cell.cell_id, source: nextSource, cell_index: index }];
    });
    if (changes.length === 0) {
      return false;
    }
    sendRequest({
      type: 'edit',
      operations: changes.map((change) => ({
        op: 'replace-source',
        cell_id: change.cell_id,
        cell_index: change.cell_index,
        source: change.source,
      })),
    });
    return true;
  }, [sendRequest]);

  const blurActiveEditor = useCallback((cellId?: string) => {
    const activeElement = document.activeElement as HTMLElement | null;
    activeElement?.blur?.();
    const targetCellId = cellId ?? stateRef.current.cells[stateRef.current.focusedIndex]?.cell_id;
    if (!targetCellId) {
      return;
    }
    codeMirrorRef.current.get(targetCellId)?.blur();
    textareasRef.current.get(targetCellId)?.blur?.();
  }, []);

  const ignoreNextBlurForCell = useCallback((cellId?: string) => {
    if (!cellId) {
      return;
    }
    ignoredBlurCellIdRef.current = cellId;
  }, []);

  const focusCell = useCallback((index: number, nextMode: 'command' | 'edit' = 'command') => {
    const current = stateRef.current;
    if (nextMode === 'command' && current.mode === 'edit') {
      ignoreNextBlurForCell(current.cells[current.focusedIndex]?.cell_id);
      blurActiveEditor(current.cells[current.focusedIndex]?.cell_id);
    }
    setFocusedIndex(index);
    setSelectedIndices([index]);
    setMode(nextMode);
  }, [blurActiveEditor, ignoreNextBlurForCell]);

  const scrollToCell = useCallback((index: number) => {
    const target = document.querySelector<HTMLElement>(`[data-cell-id="${stateRef.current.cells[index]?.cell_id ?? ''}"]`);
    target?.scrollIntoView({ block: 'nearest' });
  }, []);

  const moveFocus = useCallback(
    (delta: number) => {
      const current = stateRef.current;
      const nextIndex = Math.max(
        0,
        Math.min(current.cells.length - 1, current.focusedIndex + delta),
      );
      focusCell(nextIndex, 'command');
      scrollToCell(nextIndex);
    },
    [focusCell, scrollToCell],
  );

  const advanceToNextCell = useCallback(
    (nextIndex: number) => {
      const current = stateRef.current;
      const nextCell = current.cells[nextIndex];
      if (!nextCell) {
        return;
      }

      const shouldOpenInline = isPlaceholderCodeCell(
        nextCell,
        current.drafts[nextCell.cell_id],
      );

      if (!shouldOpenInline) {
        focusCell(nextIndex, 'command');
        scrollToCell(nextIndex);
        return;
      }

      setEditingMarkdownId(null);
      setFocusedIndex(nextIndex);
      setSelectedIndices([nextIndex]);
      setMode('edit');
      scrollToCell(nextIndex);
      requestAnimationFrame(() => {
        const latestCell = stateRef.current.cells[nextIndex];
        if (!latestCell) {
          return;
        }
        const cmHandle = codeMirrorRef.current.get(latestCell.cell_id);
        if (cmHandle) {
          cmHandle.focusAtBoundary('start');
          return;
        }
        const textarea = textareasRef.current.get(latestCell.cell_id);
        if (textarea) {
          textarea.focus();
          moveTextareaCaretToBoundary(textarea, 'start');
        }
      });
    },
    [focusCell, scrollToCell],
  );

  const extendSelection = useCallback(
    (delta: number) => {
      const current = stateRef.current;
      const nextIndex = Math.max(
        0,
        Math.min(current.cells.length - 1, current.focusedIndex + delta),
      );
      if (current.mode === 'edit') {
        ignoreNextBlurForCell(current.cells[current.focusedIndex]?.cell_id);
        blurActiveEditor(current.cells[current.focusedIndex]?.cell_id);
      }
      setFocusedIndex(nextIndex);
      setSelectedIndices((existing) =>
        existing.includes(nextIndex) ? existing : [...existing, nextIndex],
      );
      scrollToCell(nextIndex);
    },
    [blurActiveEditor, ignoreNextBlurForCell, scrollToCell],
  );

  const insertCell = useCallback(
    (
      where: 'above' | 'below',
      cellType: 'code' | 'markdown' = 'code',
      indexOverride?: number,
      nextMode: 'command' | 'edit' = 'edit',
    ) => {
      const current = stateRef.current;
      const focusedCellId = current.cells[current.focusedIndex]?.cell_id;
      if (current.mode === 'edit' && focusedCellId) {
        ignoreNextBlurForCell(focusedCellId);
        blurActiveEditor(focusedCellId);
      }
      const clampedBaseIndex = current.cells.length === 0
        ? 0
        : Math.max(0, Math.min(indexOverride ?? current.focusedIndex, current.cells.length - 1));
      const atIndex = current.cells.length === 0
        ? 0
        : where === 'above'
          ? clampedBaseIndex
          : clampedBaseIndex + 1;
      pendingCellActivationRef.current = { index: atIndex, mode: nextMode };
      setFocusedIndex(atIndex);
      setSelectedIndices([atIndex]);
      setMode(nextMode);
      sendRequest({
        type: 'edit',
        operations: [{ op: 'insert', source: '', cell_type: cellType, at_index: atIndex }],
      });
    },
    [blurActiveEditor, ignoreNextBlurForCell, sendRequest],
  );

  const resolveAdvanceTarget = useCallback(
    (cellId: string) => {
      const current = stateRef.current;
      const cellIndex = current.cells.findIndex((cell) => cell.cell_id === cellId);
      if (cellIndex < 0) {
        pendingAdvanceRef.current = null;
        return;
      }

      const nextCell = current.cells[cellIndex + 1];
      if (!nextCell) {
        const pending = pendingAdvanceRef.current;
        if (pending?.cellId === cellId && pending.awaitingInsert) {
          return;
        }
        pendingAdvanceRef.current = { cellId, awaitingInsert: true };
        insertCell('below', 'code', cellIndex, 'edit');
        return;
      }

      pendingAdvanceRef.current = null;
      if (isPlaceholderCodeCell(nextCell, current.drafts[nextCell.cell_id])) {
        advanceToNextCell(cellIndex + 1);
        return;
      }
      focusCell(cellIndex + 1, 'command');
      scrollToCell(cellIndex + 1);
    },
    [advanceToNextCell, focusCell, insertCell, scrollToCell],
  );

  useEffect(() => {
    const pendingAdvance = pendingAdvanceRef.current;
    if (!pendingAdvance?.awaitingInsert) {
      return;
    }
    resolveAdvanceTarget(pendingAdvance.cellId);
  }, [cells, resolveAdvanceTarget]);

  const deleteSelectedCells = useCallback(() => {
    const current = stateRef.current;
    const deletedIndices = (
      current.selectedIndices.length > 0
        ? [...current.selectedIndices]
        : current.focusedIndex >= 0 && current.focusedIndex < current.cells.length
          ? [current.focusedIndex]
          : []
    ).sort((a, b) => a - b);
    const operations = [...deletedIndices]
      .sort((a, b) => b - a)
      .map((index) => ({
        op: 'delete',
        cell_id: current.cells[index]?.cell_id,
        cell_index: index,
      }))
      .filter((operation) => operation.cell_id);
    if (operations.length > 0) {
      const nextCellCount = current.cells.length - operations.length;
      if (nextCellCount > 0) {
        const targetIndex = Math.min(deletedIndices[0], nextCellCount - 1);
        pendingCellActivationRef.current = { index: targetIndex, mode: 'command' };
        ignoreNextBlurForCell(current.cells[current.focusedIndex]?.cell_id);
        blurActiveEditor();
        setFocusedIndex(targetIndex);
        setSelectedIndices([targetIndex]);
        setMode('command');
      } else {
        pendingCellActivationRef.current = null;
        ignoreNextBlurForCell(current.cells[current.focusedIndex]?.cell_id);
        blurActiveEditor();
        setFocusedIndex(0);
        setSelectedIndices([]);
        setMode('command');
      }
      sendRequest({ type: 'edit', operations });
    }
  }, [blurActiveEditor, ignoreNextBlurForCell, sendRequest]);

  const changeSelectedCellType = useCallback(
    (cellType: 'code' | 'markdown') => {
      const current = stateRef.current;
      const targetIndices = (
        current.selectedIndices.length > 0
          ? [...current.selectedIndices]
          : current.focusedIndex >= 0 && current.focusedIndex < current.cells.length
            ? [current.focusedIndex]
            : []
      ).sort((a, b) => a - b);
      const operations = targetIndices
        .map((index) => {
          const cell = current.cells[index];
          if (!cell || cell.cell_type === cellType) {
            return null;
          }
          return {
            op: 'change-cell-type' as const,
            cell_id: cell.cell_id,
            cell_index: index,
            cell_type: cellType,
            source: current.drafts[cell.cell_id] ?? cell.source,
          };
        })
        .filter((operation): operation is {
          op: 'change-cell-type';
          cell_id: string;
          cell_index: number;
          cell_type: 'code' | 'markdown';
          source: string;
        } => operation != null);
      if (operations.length === 0) {
        return;
      }
      setEditingMarkdownId(null);
      setMode('command');
      sendRequest({ type: 'edit', operations });
    },
    [sendRequest],
  );

  const selectKernel = useCallback((kernelId: string) => {
    setSelectedKernelId(kernelId);
    setKernelMenuOpen(false);
    if (kernelId) {
      clearKernelExecutionState();
      sendRequest({ type: 'select-kernel', kernel_id: kernelId });
    }
  }, [clearKernelExecutionState, sendRequest]);

  const deleteCellAtIndex = useCallback(
    (index: number) => {
      const current = stateRef.current;
      const cellId = current.cells[index]?.cell_id;
      if (!cellId) return;
      const nextCellCount = current.cells.length - 1;
      if (nextCellCount > 0) {
        const targetIndex = Math.min(index, nextCellCount - 1);
        pendingCellActivationRef.current = { index: targetIndex, mode: 'command' };
        ignoreNextBlurForCell(current.cells[current.focusedIndex]?.cell_id);
        blurActiveEditor();
        setFocusedIndex(targetIndex);
        setSelectedIndices([targetIndex]);
      } else {
        pendingCellActivationRef.current = null;
        ignoreNextBlurForCell(current.cells[current.focusedIndex]?.cell_id);
        blurActiveEditor();
        setFocusedIndex(0);
        setSelectedIndices([]);
      }
      setMode('command');
      sendRequest({ type: 'edit', operations: [{ op: 'delete', cell_id: cellId, cell_index: index }] });
    },
    [blurActiveEditor, ignoreNextBlurForCell, sendRequest],
  );

  const moveCell = useCallback(
    (index: number, delta: number) => {
      const current = stateRef.current;
      const cell = current.cells[index];
      if (!cell) return;
      const toIndex = Math.max(0, Math.min(current.cells.length - 1, index + delta));
      if (toIndex === index) return;
      flushActiveDraft();
      setFocusedIndex(toIndex);
      setSelectedIndices([toIndex]);
      sendRequest({
        type: 'edit',
        operations: [{ op: 'move', cell_id: cell.cell_id, cell_index: index, to_index: toIndex }],
      });
    },
    [flushActiveDraft, sendRequest],
  );

  const runCell = useCallback(
    (index: number, options: { advance: boolean }) => {
      const current = stateRef.current;
      const cell = current.cells[index];
      if (!cell) {
        return;
      }
      if (cell.cell_type !== 'code') {
        if (options.advance) {
          if (index < current.cells.length - 1) {
            advanceToNextCell(index + 1);
          } else {
            insertCell('below', 'code', index, 'edit');
          }
        }
        return;
      }
      if (
        current.queuedIds.includes(cell.cell_id) ||
        current.executingIds.includes(cell.cell_id)
      ) {
        return;
      }
      const liveEditorSource = codeMirrorRef.current.get(cell.cell_id)?.getView()?.state.doc.toString();
      const draft = current.drafts[cell.cell_id];
      const sourceToExecute = liveEditorSource ?? draft ?? cell.source;
      const executionAlreadyActive =
        current.executingIds.length > 0 ||
        current.queuedIds.length > 0 ||
        kernelStatus.busy ||
        kernelStatus.current_execution != null;
      console.debug('[queue-debug] runCell decision', {
        cell_id: cell.cell_id,
        index,
        executionAlreadyActive,
        executingIds: current.executingIds,
        queuedIds: current.queuedIds,
        kernelBusy: kernelStatus.busy,
        currentExecution: kernelStatus.current_execution,
        decision: executionAlreadyActive ? 'queue' : 'start',
      });
      if (executionAlreadyActive) {
        queueCellIds([cell.cell_id]);
      } else {
        startCellIds([cell.cell_id]);
      }
      sendRequest({
        type: 'execute-cell',
        cell_id: cell.cell_id,
        cell_index: index,
        ...(sourceToExecute !== cell.source ? { source: sourceToExecute } : {}),
      });
      if (!options.advance) {
        return;
      }
      pendingAdvanceRef.current = { cellId: cell.cell_id, awaitingInsert: false };
      resolveAdvanceTarget(cell.cell_id);
    },
    [kernelStatus.busy, kernelStatus.current_execution, queueCellIds, resolveAdvanceTarget, sendRequest, startCellIds],
  );

  const runCellAndAdvance = useCallback(
    (index: number | string) => {
      if (typeof index === 'string') {
        const resolvedIndex = stateRef.current.cells.findIndex((cell) => cell.cell_id === index);
        if (resolvedIndex < 0) {
          return;
        }
        runCell(resolvedIndex, { advance: true });
        return;
      }
      runCell(index, { advance: true });
    },
    [runCell],
  );

  const openExternalLink = useCallback(
    (href: string) => {
      send({ type: 'open-external-link', requestId: nextRequestId(), url: href });
    },
    [send],
  );

  const handleRuntimeUpdate = useCallback((runtime: RuntimeInfo) => {
    setKernelStatus(runtime);
    const nextExecutingIds = runtimeCellIds(runtime, 'running');
    const nextQueuedIds = runtimeCellIds(runtime, 'queued').filter((cellId) => !nextExecutingIds.includes(cellId));
    if (nextExecutingIds.length > 0 || nextQueuedIds.length > 0) {
      const startedAt = Date.now();
      for (const cellId of nextExecutingIds) {
        executionStartRef.current.set(
          cellId,
          executionStartRef.current.get(cellId) ?? startedAt,
        );
      }
      const nextFailedIds = stateRef.current.failedCellIds.filter(
        (cellId) => !nextQueuedIds.includes(cellId) && !nextExecutingIds.includes(cellId),
      );
      const nextPausedIds = stateRef.current.pausedCellIds.filter(
        (cellId) => !nextQueuedIds.includes(cellId) && !nextExecutingIds.includes(cellId),
      );
      setQueuedIds(nextQueuedIds);
      setExecutingIds(nextExecutingIds);
      setFailedCellIds(nextFailedIds);
      setPausedCellIds(nextPausedIds);
      updateExecutionStateRef({
        queuedIds: nextQueuedIds,
        executingIds: nextExecutingIds,
        failedCellIds: nextFailedIds,
        pausedCellIds: nextPausedIds,
      });
    }
    if (!runtime.busy && !runtime.current_execution) {
      const current = stateRef.current;
      if (current.executingIds.length > 0 || current.queuedIds.length > 0) {
        const { completedIds, pausedIds } = resolveIdleExecutionTransition({
          queuedIds: current.queuedIds,
          executingIds: current.executingIds,
          failedCellIds: current.failedCellIds,
        });
        const finishedAt = Date.now();
        setQueuedIds([]);
        setExecutingIds([]);
        setPausedCellIds((existing) => Array.from(new Set([
          ...existing.filter((cellId) => !current.executingIds.includes(cellId)),
          ...pausedIds,
        ])));
        setCompletedTimes((prev) => {
          const next = { ...prev };
          for (const cellId of pausedIds) {
            delete next[cellId];
          }
          for (const cellId of completedIds) {
            const startTime = executionStartRef.current.get(cellId);
            executionStartRef.current.delete(cellId);
            next[cellId] = startTime != null ? finishedAt - startTime : next[cellId] ?? 0;
          }
          return next;
        });
        updateExecutionStateRef({
          queuedIds: [],
          executingIds: [],
          failedCellIds: current.failedCellIds,
          pausedCellIds: Array.from(new Set([
            ...current.pausedCellIds.filter((cellId) => !current.executingIds.includes(cellId)),
            ...pausedIds,
          ])),
        });
      }
    }
  }, [updateExecutionStateRef]);

  const bindTextarea = useCallback((cellId: string, element: HTMLTextAreaElement | null) => {
    textareasRef.current.set(cellId, element);
    autoResize(element);
  }, []);

  const bindCodeMirror = useCallback((cellId: string, handle: CodeMirrorCellHandle | null) => {
    codeMirrorRef.current.set(cellId, handle);
  }, []);

  const handleCellBlur = useCallback(
    (
      cell: NotebookCell,
      value: string,
      index: number,
      exitMarkdown = false,
      blurActive = false,
    ) => {
      if (value !== cell.source) {
        flushDraft(cell.cell_id, value, index);
      }
      if (ignoredBlurCellIdRef.current === cell.cell_id) {
        ignoredBlurCellIdRef.current = null;
        if (exitMarkdown) {
          setEditingMarkdownId(null);
        }
        return;
      }
      const current = stateRef.current;
      const focusedCellId = current.cells[current.focusedIndex]?.cell_id;
      const hasPendingActivation = pendingCellActivationRef.current != null;
      const blurIsStale =
        hasPendingActivation ||
        (focusedCellId != null && focusedCellId !== cell.cell_id);
      if (blurIsStale) {
        if (exitMarkdown && current.editingMarkdownId === cell.cell_id) {
          setEditingMarkdownId(null);
        }
        return;
      }
      if (exitMarkdown) {
        setEditingMarkdownId(null);
      }
      setFocusedIndex(index);
      setSelectedIndices([index]);
      setMode('command');
      if (blurActive) {
        requestAnimationFrame(() => {
          const active = document.activeElement as HTMLElement | null;
          active?.blur?.();
        });
      }
    },
    [flushDraft],
  );

  const focusCellEditor = useCallback((index: number, boundary: 'start' | 'end' = 'end') => {
    const current = stateRef.current;
    const focusedCell = current.cells[index];
    if (!focusedCell) return;
    if (focusedCell.cell_type === 'markdown') {
      setEditingMarkdownId(focusedCell.cell_id);
    } else {
      setEditingMarkdownId(null);
    }
    setFocusedIndex(index);
    setSelectedIndices([index]);
    setMode('edit');
    requestAnimationFrame(() => {
      // Try CodeMirror handle first (code cells), then textarea (markdown)
      const cmHandle = codeMirrorRef.current.get(focusedCell.cell_id);
      if (cmHandle) {
        cmHandle.focusAtBoundary(boundary);
        return;
      }
      const element = textareasRef.current.get(focusedCell.cell_id);
      if (element) {
        element.focus();
        moveTextareaCaretToBoundary(element, boundary);
      }
    });
  }, []);

  const enterEditModeForCell = useCallback((index: number) => {
    focusCellEditor(index, 'end');
  }, [focusCellEditor]);

  const moveToAdjacentCellFromEditor = useCallback((index: number, delta: number) => {
    const current = stateRef.current;
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= current.cells.length) {
      return false;
    }
    focusCellEditor(nextIndex, delta < 0 ? 'end' : 'start');
    scrollToCell(nextIndex);
    return true;
  }, [focusCellEditor, scrollToCell]);

  useEffect(() => {
    const pendingActivation = pendingCellActivationRef.current;
    if (!pendingActivation) {
      return;
    }
    if (pendingActivation.index < 0 || pendingActivation.index >= cells.length) {
      return;
    }

    pendingCellActivationRef.current = null;
    requestAnimationFrame(() => {
      if (pendingActivation.mode === 'edit') {
        enterEditModeForCell(pendingActivation.index);
        return;
      }
      focusCell(pendingActivation.index, 'command');
      scrollToCell(pendingActivation.index);
    });
  }, [cells, enterEditModeForCell, focusCell, scrollToCell]);

  const handleEditorKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>, cell: NotebookCell, index: number) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.currentTarget.blur();
        setMode('command');
        return;
      }
      if (event.key === 'Enter' && event.shiftKey) {
        event.preventDefault();
        event.currentTarget.blur();
        runCellAndAdvance(index);
        return;
      }
      if (event.key === 'Tab') {
        const textarea = event.currentTarget;
        const start = textarea.selectionStart;
        const end = textarea.selectionEnd;
        const currentValue = drafts[cell.cell_id] ?? cell.source;
        const allowIndent =
          selectionStartsInLeadingWhitespace(currentValue, start) ||
          selectionTouchesMultipleLines(currentValue, start, end);

        if (!allowIndent) {
          event.preventDefault();
          return;
        }

        event.preventDefault();

        if (event.shiftKey) {
          const before = currentValue.slice(0, start);
          const lineStart = before.lastIndexOf('\n') + 1;
          const line = currentValue.slice(lineStart);
          const match = line.match(/^ {1,4}/);
          if (!match) return;
          const nextValue =
            currentValue.slice(0, lineStart) + line.slice(match[0].length);
          setDrafts((existing) => ({ ...existing, [cell.cell_id]: nextValue }));
          requestAnimationFrame(() => {
            const target = textareasRef.current.get(cell.cell_id);
            if (!target) return;
            target.selectionStart = start - match[0].length;
            target.selectionEnd = start - match[0].length;
            autoResize(target);
          });
          return;
        }

        const nextValue = `${currentValue.slice(0, start)}    ${currentValue.slice(end)}`;
        setDrafts((existing) => ({ ...existing, [cell.cell_id]: nextValue }));
        requestAnimationFrame(() => {
          const target = textareasRef.current.get(cell.cell_id);
          if (!target) return;
          target.selectionStart = start + 4;
          target.selectionEnd = start + 4;
          autoResize(target);
        });
      }
    },
    [drafts, runCellAndAdvance],
  );

  const toggleExplorerVisibility = useCallback(() => {
    setExplorerCollapsed((current) => !current);
  }, []);

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

  const switchNotebookFromExplorer = useCallback((nextPath: string) => {
    if (!nextPath || nextPath === activeNotebookPath) {
      return;
    }
    flushActiveDraft();
    blurActiveEditor();
    setKernelMenuOpen(false);
    setErrorMessage(null);
    setErrorIsConflict(false);
    setLoading(true);
    setActiveNotebookPath(nextPath);
    setExplorerExpandedPaths((current) => new Set([
      ...current,
      ...collectExplorerAncestorPaths(nextPath),
    ]));
    sendRequest({ type: 'switch-notebook', path: nextPath });
  }, [activeNotebookPath, blurActiveEditor, flushActiveDraft, sendRequest]);

  useEffect(() => {
    if (!standaloneHost || !activeNotebookPath) {
      return;
    }
    const url = new URL(window.location.href);
    if (url.searchParams.get('path') === activeNotebookPath) {
      return;
    }
    url.searchParams.set('path', activeNotebookPath);
    window.history.replaceState({}, '', url);
  }, [activeNotebookPath]);

  // ── Message handler ──
  useEffect(() => {
    const handleMessage = (event: MessageEvent<any>) => {
      const message = event.data;

      if (message.type === 'contents') {
        setErrorMessage(null);
        setErrorIsConflict(false);
        if (typeof message.path === 'string') {
          setActiveNotebookPath(message.path);
        }
        applyCells(message.cells ?? []);
        return;
      }

      if (message.type === 'workspace-tree') {
        setWorkspaceTree(message.root ?? null);
        setWorkspaceName(typeof message.workspace_name === 'string' ? message.workspace_name : '');
        const nextActivePath = typeof message.selected_path === 'string' ? message.selected_path : null;
        setActiveNotebookPath(nextActivePath);
        if (!nextActivePath) {
          setLoading(false);
        }
        const ancestorPaths = collectExplorerAncestorPaths(nextActivePath);
        setExplorerExpandedPaths((current) => new Set([...current, ...ancestorPaths]));
        return;
      }

      if (message.type === 'kernels') {
        setKernels(message.kernels ?? []);
        setSelectedKernelId(message.preferred_kernel?.id ?? '');
        return;
      }

      if (message.type === 'runtime') {
        handleRuntimeUpdate({
          active: message.active,
          busy: message.busy,
          kernel_label: message.kernel_label,
          runtime_id: typeof message.runtime_id === 'string' ? message.runtime_id : undefined,
          kernel_generation: typeof message.kernel_generation === 'number' ? message.kernel_generation : null,
          current_execution: (message.current_execution as Record<string, unknown> | null | undefined) ?? null,
          running_cell_ids: Array.isArray(message.running_cell_ids)
            ? message.running_cell_ids.filter((cellId: unknown): cellId is string => typeof cellId === 'string')
            : undefined,
          queued_cell_ids: Array.isArray(message.queued_cell_ids)
            ? message.queued_cell_ids.filter((cellId: unknown): cellId is string => typeof cellId === 'string')
            : undefined,
        });
        return;
      }

      if (message.type === 'lsp-diagnostics') {
        setDiagnosticsByCell(message.diagnostics_by_cell ?? {});
        return;
      }

      if (message.type === 'lsp-status') {
        setLspStatus({
          state: message.state,
          message: typeof message.message === 'string' ? message.message : '',
        });
        return;
      }

      if (message.type === 'lsp-completions' && typeof message.requestId === 'string') {
        const pending = completionRequestsRef.current.get(message.requestId);
        if (!pending) {
          return;
        }
        completionRequestsRef.current.delete(message.requestId);
        pending.resolve(Array.isArray(message.items) ? message.items : []);
        return;
      }

      if (message.type === 'ok') {
        setErrorMessage(null);
        setErrorIsConflict(false);
        return;
      }

      if (message.type === 'error') {
        if (typeof message.requestId === 'string') {
          const pending = completionRequestsRef.current.get(message.requestId);
          if (pending) {
            completionRequestsRef.current.delete(message.requestId);
            pending.reject(new Error(
              typeof message.message === 'string' && message.message.trim()
                ? message.message
                : 'Completion request failed.',
            ));
            return;
          }
        }
        setLoading(false);
        setErrorMessage(
          typeof message.message === 'string' && message.message.trim()
            ? message.message
            : 'Notebook action failed.',
        );
        setErrorIsConflict(Boolean(message.conflict));
        return;
      }

      if (message.type === 'execute-started' && message.cell_id) {
        console.debug('[queue-debug] execute-started', {
          cell_id: message.cell_id,
          currentExecutingIds: stateRef.current.executingIds,
          currentQueuedIds: stateRef.current.queuedIds,
        });
        executionStartRef.current.set(message.cell_id, Date.now());
        setQueuedIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
        setExecutingIds((existing) =>
          existing.includes(message.cell_id) ? existing : [...existing, message.cell_id],
        );
        setFailedCellIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
        setPausedCellIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
        return;
      }

      if ((message.type === 'execute-finished' || message.type === 'execute-failed') && message.cell_id) {
        console.debug(`[queue-debug] ${message.type}`, {
          cell_id: message.cell_id,
          ok: message.ok,
          currentExecutingIds: stateRef.current.executingIds,
          currentQueuedIds: stateRef.current.queuedIds,
        });
        setQueuedIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
        setExecutingIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
        const startTime = executionStartRef.current.get(message.cell_id);
        executionStartRef.current.delete(message.cell_id);
        const failed = message.type === 'execute-failed' || message.ok === false;
        if (!failed && message.type === 'execute-finished' && startTime != null) {
          const elapsed = startTime != null ? Date.now() - startTime : 0;
          setCompletedTimes((prev) => ({ ...prev, [message.cell_id]: elapsed }));
        }
        if (failed) {
          setFailedCellIds((existing) =>
            existing.includes(message.cell_id) ? existing : [...existing, message.cell_id],
          );
          setPausedCellIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
          setCompletedTimes((prev) => {
            const next = { ...prev };
            delete next[message.cell_id];
            return next;
          });
        } else {
          setFailedCellIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
          setPausedCellIds((existing) => existing.filter((cellId) => cellId !== message.cell_id));
        }
        if (message.type === 'execute-failed') {
          setErrorMessage(
            typeof message.message === 'string' && message.message.trim()
              ? message.message
              : 'Execution failed.',
          );
          setErrorIsConflict(false);
        }
        return;
      }

      if (message.type === 'activity-update') {
        const current = stateRef.current;
        const nextCells = [...current.cells];
        const nextQueued = new Set(current.queuedIds);
        const nextExecuting = new Set(current.executingIds);
        const nextPaused = new Set(current.pausedCellIds);
        let cellsChanged = false;
        let needsFullReload = false;

        for (const eventItem of (message.events ?? []) as ActivityEvent[]) {
          const eventType = eventItem.event_type ?? eventItem.type;
          const cell = eventItem.data?.cell;
          const cellIndex = nextCells.findIndex(
            (entry) => entry.cell_id === eventItem.cell_id,
          );

          if (eventType === 'cell-source-updated' && cell && cellIndex >= 0) {
            nextCells[cellIndex] = cell;
            cellsChanged = true;
          } else if (
            (eventType === 'cell-output-appended' ||
              eventType === 'cell-outputs-updated') &&
            cell &&
            cellIndex >= 0
          ) {
            nextCells[cellIndex] = {
              ...nextCells[cellIndex],
              ...cell,
              outputs: cell.outputs ?? nextCells[cellIndex].outputs,
            };
            cellsChanged = true;
            if (eventItem.cell_id) {
              nextQueued.delete(eventItem.cell_id);
              nextExecuting.add(eventItem.cell_id);
              nextPaused.delete(eventItem.cell_id);
              executionStartRef.current.set(
                eventItem.cell_id,
                executionStartRef.current.get(eventItem.cell_id) ?? Date.now(),
              );
            }
          } else if (eventType === 'execution-started' && eventItem.cell_id) {
            executionStartRef.current.set(eventItem.cell_id, Date.now());
            nextQueued.delete(eventItem.cell_id);
            nextExecuting.add(eventItem.cell_id);
            nextPaused.delete(eventItem.cell_id);
          } else if (
            eventType === 'execution-finished' &&
            eventItem.cell_id
          ) {
            nextQueued.delete(eventItem.cell_id);
            nextExecuting.delete(eventItem.cell_id);
            nextPaused.delete(eventItem.cell_id);
            const startTime = executionStartRef.current.get(eventItem.cell_id);
            if (startTime != null || current.executingIds.includes(eventItem.cell_id) || current.queuedIds.includes(eventItem.cell_id)) {
              const elapsed = startTime != null ? Date.now() - startTime : 0;
              executionStartRef.current.delete(eventItem.cell_id);
              setCompletedTimes((prev) => ({ ...prev, [eventItem.cell_id!]: elapsed }));
            }
          } else if (
            eventType === 'cell-inserted' ||
            eventType === 'cell-removed' ||
            eventType === 'notebook-reset-needed'
          ) {
            needsFullReload = true;
          }
        }

        if (message.runtime) {
          handleRuntimeUpdate({
            active: message.runtime.active,
            busy: message.runtime.busy,
            kernel_label: typeof message.runtime.kernel_label === 'string'
              ? message.runtime.kernel_label
              : kernelStatus.kernel_label,
            runtime_id: typeof message.runtime.runtime_id === 'string'
              ? message.runtime.runtime_id
              : kernelStatus.runtime_id,
            kernel_generation: typeof message.runtime.kernel_generation === 'number'
              ? message.runtime.kernel_generation
              : kernelStatus.kernel_generation ?? null,
            current_execution: (message.runtime.current_execution as Record<string, unknown> | null | undefined) ?? null,
          });
        }

        if (needsFullReload) {
          sendRequest({ type: 'load-contents' });
        } else {
          if (cellsChanged) {
            applyCells(nextCells);
          }
          setQueuedIds([...nextQueued]);
          setExecutingIds([...nextExecuting]);
          setPausedCellIds([...nextPaused]);
        }
        return;
      }
    };

    window.addEventListener('message', handleMessage);
    send({ type: 'webview-ready', requestId: nextRequestId() });
    sendRequest({ type: 'get-workspace-tree' });
    sendRequest({ type: 'get-kernels' });
    sendRequest({ type: 'get-runtime' });
    return () => window.removeEventListener('message', handleMessage);
  }, [applyCells, handleRuntimeUpdate, kernelStatus.kernel_label, send, sendRequest]);

  // ── Keyboard handler ──
  useEffect(() => {
    if (!isBrowserCanvas) {
      return undefined;
    }

    const handleBrowserShortcutKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.shiftKey) {
        return;
      }
      const key = event.key.toLowerCase();
      if (key === 's') {
        event.preventDefault();
        event.stopPropagation();
        saveNotebookDrafts();
        return;
      }
      if (key === 'b') {
        event.preventDefault();
        event.stopPropagation();
        toggleExplorerVisibility();
      }
    };

    document.addEventListener('keydown', handleBrowserShortcutKeyDown, true);
    return () => document.removeEventListener('keydown', handleBrowserShortcutKeyDown, true);
  }, [isBrowserCanvas, saveNotebookDrafts, toggleExplorerVisibility]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const current = stateRef.current;
      const pendingActivation = pendingCellActivationRef.current;
      const target = event.target as HTMLElement | null;
      const targetTag = target?.tagName;
      const insideCodeMirror = Boolean(target?.closest('.cm-editor, .cm-content, .cm-line'));
      const isInteractive =
        insideCodeMirror ||
        targetTag === 'TEXTAREA' ||
        targetTag === 'SELECT' ||
        targetTag === 'BUTTON' ||
        targetTag === 'INPUT';
      const focusedCellExists =
        current.focusedIndex >= 0 && current.focusedIndex < current.cells.length;
      const focusedPendingCell = pendingActivation != null;
      const decision = decideNotebookCommandKeyAction(
        {
          mode: current.mode,
          focusedIndex: current.focusedIndex,
          cellCount: current.cells.length,
          focusedPendingCell,
        },
        {
          key: event.key,
          shiftKey: event.shiftKey,
          metaKey: event.metaKey,
          ctrlKey: event.ctrlKey,
          defaultPrevented: event.defaultPrevented,
          isInteractive,
        },
        lastDPressRef.current,
        Date.now(),
      );

      lastDPressRef.current = decision.nextLastDPressAt;
      if (decision.preventDefault) {
        event.preventDefault();
      }

      for (const action of decision.actions) {
        switch (action.type) {
          case 'select-all':
            setSelectedIndices(current.cells.map((_, index) => index));
            break;
          case 'insert-cell':
            insertCell(action.where, 'code', undefined, action.nextMode);
            break;
            case 'delete-selected':
              deleteSelectedCells();
              break;
            case 'change-cell-type':
              changeSelectedCellType(action.cellType);
              break;
            case 'activate-pending-edit':
              if (pendingActivation) {
                pendingCellActivationRef.current = {
                index: pendingActivation.index,
                mode: 'edit',
              };
              setMode('edit');
            }
            break;
          case 'enter-edit':
            if (focusedCellExists) {
              enterEditModeForCell(action.index);
            }
            break;
          case 'run-and-advance':
            if (focusedCellExists) {
              runCellAndAdvance(action.index);
            }
            break;
          case 'move-focus':
            moveFocus(action.delta);
            break;
          case 'extend-selection':
            extendSelection(action.delta);
            break;
          case 'set-command-mode':
            setMode('command');
            break;
        }
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [changeSelectedCellType, deleteSelectedCells, enterEditModeForCell, extendSelection, insertCell, moveFocus, runCellAndAdvance]);

  // ── Derived state ──
  const selectedSet = useMemo(() => new Set(selectedIndices), [selectedIndices]);
  const queuedSet = useMemo(() => new Set(queuedIds), [queuedIds]);
  const executingSet = useMemo(() => new Set(executingIds), [executingIds]);
  const failedSet = useMemo(() => new Set(failedCellIds), [failedCellIds]);
  const pausedSet = useMemo(() => new Set(pausedCellIds), [pausedCellIds]);
  const selectedKernel = useMemo(
    () => kernels.find((kernel) => kernel.id === selectedKernelId) ?? null,
    [kernels, selectedKernelId],
  );
  const dirtyDraftChanges = useMemo(
    () => collectDirtyDraftChanges(cells, drafts),
    [cells, drafts],
  );
  const hasDirtyDrafts = dirtyDraftChanges.length > 0;
  const browserSaveShortcutLabel = /Mac|iPhone|iPad|iPod/i.test(globalThis.navigator?.platform ?? '')
    ? 'Cmd+S'
    : 'Ctrl+S';

  // ── Button styles (inline, no Tailwind) ──
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
    { value: 'system', label: tightToolbar ? 'A' : compactToolbar ? 'Auto' : 'System' },
    { value: 'light', label: tightToolbar ? 'L' : 'Light' },
    { value: 'dark', label: tightToolbar ? 'D' : 'Dark' },
  ];
  const activeNotebookLabel = activeNotebookPath?.split(/[\\/]/).pop() ?? 'Notebook';

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
          {expanded ? (
            <div>{(node.children ?? []).map((child) => renderExplorerNode(child, depth + 1))}</div>
          ) : null}
        </div>
      );
    }

    const active = node.path === activeNotebookPath;
    return (
      <button
        key={node.path}
        type="button"
        data-explorer-item="notebook"
        data-explorer-item-path={node.path}
        data-active={active ? 'true' : 'false'}
        onClick={() => switchNotebookFromExplorer(node.path)}
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
          <Notebook size={14} />
        </span>
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.name}
        </span>
      </button>
    );
  };
  // ── Render ──
  const notebookCanvas = (
      <div style={{
        display: 'flex', flexDirection: 'column', minHeight: '100vh',
        background: 'var(--cds-background)',
      }}>
        {/* Toolbar */}
        <div
          data-toolbar="notebook"
          style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: `12px ${design.contentPaddingX}px`,
          borderBottom: '1px solid var(--cds-border-subtle)',
        }}
        >
          <div ref={toolbarContentRef} style={{
            display: 'flex',
            width: '100%',
            gap: tightToolbar ? 2 : 4,
            alignItems: 'center',
            justifyContent: compactToolbar ? 'flex-start' : 'center',
            flexWrap: 'nowrap',
            minWidth: 0,
            paddingLeft: CELL_GUTTER_WIDTH,
            overflow: 'hidden',
          }}>
            {isBrowserCanvas ? (
              <button
                type="button"
                data-save-notebook="true"
                onClick={() => {
                  saveNotebookDrafts();
                }}
                aria-label={`Save notebook (${browserSaveShortcutLabel})`}
                title={`Save notebook (${browserSaveShortcutLabel})`}
                disabled={!hasDirtyDrafts}
                style={{
                  ...tbOutline,
                  opacity: hasDirtyDrafts ? 1 : 0.45,
                  cursor: hasDirtyDrafts ? 'pointer' : 'default',
                }}
              >
                <Save size={14} />
                {tightToolbar ? null : <span>Save</span>}
              </button>
            ) : null}
            <button
              onClick={() => {
                sendRequest({ type: 'interrupt-execution' });
              }}
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
              {stopLabel ? <span>{stopLabel}</span> : null}
            </button>
            <button
              onClick={() => {
                flushActiveDraft();
                clearKernelExecutionState();
                sendRequest({ type: 'restart-kernel' });
              }}
              aria-label="Restart"
              title="Restart"
              style={tbGhostStrong}
            >
              <Restart size={14} /> {restartLabel ? <span>{restartLabel}</span> : null}
            </button>
            <button
              onClick={() => {
                flushActiveDraft();
                queueCellIds(cells.filter((c) => c.cell_type === 'code').map((c) => c.cell_id));
                sendRequest({ type: 'execute-all' });
              }}
              aria-label="Run All"
              title="Run All"
              style={tbOutline}
            >
              <PlayFilled size={14} /> {runAllLabel}
            </button>
            <button
              onClick={() => {
                flushActiveDraft();
                clearKernelExecutionState();
                queueCellIds(cells.filter((c) => c.cell_type === 'code').map((c) => c.cell_id));
                sendRequest({ type: 'restart-and-run-all' });
              }}
              aria-label="Restart and Run All"
              title="Restart and Run All"
              style={tbGhostStrong}
            >
              <Restart size={14} />
              <span style={{
                minWidth: 0,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}>
                {restartRunAllLabel}
              </span>
            </button>
            {tightToolbar ? null : <span style={{ width: 1, height: 16, background: 'var(--cds-border-subtle)', margin: compactToolbar ? '0 4px' : '0 8px', flexShrink: 0 }} />}
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
                      padding: tightToolbar ? '0 6px' : compactToolbar ? '0 8px' : '0 10px',
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
                minWidth: tightToolbar ? 132 : compactToolbar ? 160 : 220,
                maxWidth: tightToolbar ? 168 : compactToolbar ? 220 : 320,
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
                  padding: compactToolbar ? '0 10px' : '0 12px',
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
                  {selectedKernel?.label ?? 'Select kernel'}
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
                    <div style={{
                      padding: '8px 10px',
                      fontSize: 12,
                      color: 'var(--cds-text-helper)',
                    }}>
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
                        onClick={() => selectKernel(kernel.id)}
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
                          color: isSelected ? SELECTION_COLOR : 'var(--cds-text-helper)',
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
          </div>
        </div>

        {/* Error banner */}
        {errorMessage ? (
          <div style={{
            margin: `${design.cellGap}px ${design.contentPaddingX}px 0`,
            padding: '12px 16px',
            borderRadius: design.cornerRadius,
            border: `1px solid ${errorIsConflict ? 'var(--cds-support-warning)' : 'var(--cds-support-error)'}`,
            background: errorIsConflict ? 'rgba(217,119,6,0.06)' : 'rgba(225,29,72,0.06)',
            color: errorIsConflict ? 'var(--cds-support-warning)' : 'var(--cds-support-error)',
          }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>
              {errorIsConflict ? 'Notebook is temporarily locked by another active session.' : 'Notebook action failed.'}
            </div>
            <div style={{ marginTop: 4, fontSize: 13 }}>{errorMessage}</div>
          </div>
        ) : null}
        {lspStatus?.state === 'unavailable' ? (
          <div style={{
            margin: `${design.cellGap}px ${design.contentPaddingX}px 0`,
            padding: '12px 16px',
            borderRadius: design.cornerRadius,
            border: '1px solid var(--cds-border-subtle)',
            background: 'rgba(217,119,6,0.06)',
            color: 'var(--cds-text-primary)',
            fontSize: 13,
          }}>
            {lspStatus.message}
          </div>
        ) : null}

        {/* Cell list */}
        {loading ? (
          showLoadingPlaceholder ? (
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            minHeight: 240,
            margin: `${design.cellGap}px ${design.contentPaddingX}px`,
            borderRadius: design.cornerRadius,
            border: '1px dashed var(--cds-border-subtle)',
            background: 'var(--cds-layer)',
            fontSize: 14, fontWeight: 500,
            color: 'var(--cds-text-secondary)',
          }}>
            Loading notebook canvas...
          </div>
          ) : (
          <div style={{ flex: 1, minHeight: 240 }} />
          )
        ) : !activeNotebookPath ? (
          <div style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: 240,
            margin: `${design.cellGap}px ${design.contentPaddingX}px`,
            borderRadius: design.cornerRadius,
            border: '1px dashed var(--cds-border-subtle)',
            background: 'var(--cds-layer)',
            fontSize: 14,
            fontWeight: 500,
            color: 'var(--cds-text-secondary)',
            textAlign: 'center',
            padding: '0 24px',
          }}>
            {(workspaceTree?.children?.length ?? 0) > 0
              ? 'Select a notebook from the explorer to open it in the browser canvas.'
              : 'No notebooks were found in the launched workspace.'}
          </div>
        ) : (
          <div style={{
            flex: 1, overflowY: 'auto',
            padding: `${design.cellGap}px ${design.contentPaddingX}px 5rem`,
            display: 'flex', flexDirection: 'column',
            gap: `${design.cellGap}px`,
          }}>
            {cells.map((cell, index) => (
              <MemoCellCard
                key={cell.cell_id}
                cell={cell}
                index={index}
                source={drafts[cell.cell_id] ?? cell.source}
                isSelected={selectedSet.has(index)}
                isQueued={queuedSet.has(cell.cell_id)}
                isExecuting={executingSet.has(cell.cell_id)}
                hasFailedExecution={failedSet.has(cell.cell_id) || cellHasErrorOutput(cell)}
                hasPausedExecution={pausedSet.has(cell.cell_id)}
                showRuntimeStatus={hasLiveRuntime}
                runtimeStatus={kernelStatus}
                isMarkdownEditing={editingMarkdownId === cell.cell_id}
                onFocusCell={(nextIndex, nextMode) => focusCell(nextIndex, nextMode)}
                onBlurCell={handleCellBlur}
                onSourceChange={(cellId, value) => {
                  setDrafts((existing) => ({ ...existing, [cellId]: value }));
                  syncLspDraft(cellId, value);
                  requestAnimationFrame(() => autoResize(textareasRef.current.get(cellId) ?? null));
                }}
                requestCompletions={(value, offset) => requestLspCompletions(cell.cell_id, value, offset)}
                onEditorKeyDown={handleEditorKeyDown}
                onRunCell={(nextIndex) => runCell(nextIndex, { advance: false })}
                onRunCellAndAdvance={runCellAndAdvance}
                onEnterMarkdownEdit={(cellId, nextIndex) => {
                  setEditingMarkdownId(cellId);
                  focusCell(nextIndex, 'edit');
                  requestAnimationFrame(() => {
                    const element = textareasRef.current.get(cellId);
                    if (element) {
                      element.focus();
                      moveTextareaCaretToLineEnd(element);
                    }
                  });
                }}
                onOpenExternal={openExternalLink}
                bindTextarea={bindTextarea}
                bindCodeMirror={bindCodeMirror}
                onMoveToAdjacentCell={moveToAdjacentCellFromEditor}
                diagnostics={diagnosticsByCell[cell.cell_id] ?? EMPTY_DIAGNOSTICS}
                onMoveCell={moveCell}
                onDeleteCell={deleteCellAtIndex}
                onInsertBelow={(targetIndex, cellType) => insertCell('below', cellType, targetIndex)}
                compactToolbar={compactToolbar}
                isFirstCell={index === 0}
                isLastCell={index === cells.length - 1}
                completedTime={completedTimes[cell.cell_id]}
                executionStartedAt={executionStartRef.current.get(cell.cell_id) ?? null}
                dark={dark}
              />
            ))}
          </div>
        )}
      </div>
  );

  const browserShell = isBrowserCanvas ? (
    <div
      data-browser-shell="true"
      style={{
        display: 'flex',
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
          onClick={toggleExplorerVisibility}
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
            <button
              type="button"
              aria-label="Collapse Explorer"
              onClick={toggleExplorerVisibility}
              style={{
                width: 28,
                height: 28,
                borderRadius: 6,
                border: 'none',
                background: 'transparent',
                color: 'var(--cds-text-helper)',
                display: 'inline-flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                flexShrink: 0,
              }}
            >
              <ChevronLeft size={16} />
            </button>
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
      <div style={{ flex: 1, minWidth: 0 }}>
        {notebookCanvas}
      </div>
    </div>
  ) : notebookCanvas;

  return (
    <DesignContext.Provider value={design}>
      <Theme
        theme={carbonTheme}
        style={{
          minHeight: '100vh',
          fontFamily: `"${UI_FONT}", 'IBM Plex Sans', system-ui, sans-serif`,
        }}
      >
        {browserShell}
      </Theme>
    </DesignContext.Provider>
  );
}

// ── Mount ────────────────────────────────────────────────

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error('Canvas root element not found');
}

createRoot(rootElement).render(
  <>
    {showInterfaceKit ? <InterfaceKit /> : null}
    {showAgentation ? <PageFeedbackToolbarCSS copyToClipboard /> : null}
    <App />
  </>
);

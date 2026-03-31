import {
  buildActivityPollResult,
} from '../src/shared/notebookActivity';
import { runNotebookCommandFlow } from '../src/shared/notebookCommandFlow';
import {
  buildReplaceSourceOperation,
  buildReplaceSourceOperations,
} from '../src/shared/notebookEditPayload';
import {
  daemonUnavailableRecovery,
  recoveryFromPayload,
  stalePreviewServerRecovery,
  type RecoveryAdvice,
} from '../src/shared/recovery';
import { buildRuntimeSnapshot } from '../src/shared/runtimeSnapshot';
import { DaemonWebSocket } from '../src/shared/wsClient';

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
  index?: number;
};

type WorkspaceTreeNode = {
  kind: 'directory' | 'notebook';
  name: string;
  path: string;
  children?: WorkspaceTreeNode[];
};

type HostMessage = {
  type: string;
  requestId?: string;
  [key: string]: unknown;
};

type HostApi = {
  postMessage: (message: HostMessage) => void;
};

export type StandaloneFeatureFlags = {
  interfaceKit: boolean;
  agentation: boolean;
};

export type StandaloneConfig = {
  notebookPath: string | null;
  features: StandaloneFeatureFlags;
};

function normalizeDraftChanges(raw: unknown): Array<{ cell_id: string; source: string }> {
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

function parseBooleanFlag(raw: string | null, fallback: boolean): boolean {
  if (raw == null || raw.trim() === '') {
    return fallback;
  }
  const normalized = raw.trim().toLowerCase();
  if (['1', 'true', 'yes', 'on'].includes(normalized)) {
    return true;
  }
  if (['0', 'false', 'no', 'off'].includes(normalized)) {
    return false;
  }
  return fallback;
}

export function readStandaloneConfig(): StandaloneConfig | null {
  const params = new URLSearchParams(window.location.search);
  if (parseBooleanFlag(params.get('mock'), false)) {
    return null;
  }
  const notebookPath = params.get('path')?.trim() || null;

  return {
    notebookPath,
    features: {
      interfaceKit: parseBooleanFlag(params.get('interfacekit'), false),
      agentation: parseBooleanFlag(params.get('agentation'), false),
    },
  };
}

export function createStandaloneHost(config: StandaloneConfig): HostApi {
  const clientId = globalThis.crypto?.randomUUID?.() ?? `standalone-${Date.now()}`;
  let currentNotebookPath = config.notebookPath;
  let activityCursor = 0;
  let touchTimer: number | null = null;
  let attached = false;
  let activeCells: NotebookCell[] = [];
  let daemonWs: DaemonWebSocket | null = null;

  const dispatch = (message: HostMessage) => {
    window.dispatchEvent(new MessageEvent('message', { data: message }));
  };

  const describeError = (error: unknown): string => (
    error instanceof Error ? error.message : String(error)
  );

  const logNonBlockingFailure = (scope: string, error: unknown) => {
    console.warn(`[agent-repl] standalone ${scope} failed`, error);
  };

  const requireNotebookPath = (): string => {
    if (!currentNotebookPath) {
      throw new Error('No notebook selected');
    }
    return currentNotebookPath;
  };

  const findFirstNotebookPath = (node: WorkspaceTreeNode | null | undefined): string | null => {
    if (!node) {
      return null;
    }
    if (node.kind === 'notebook') {
      return node.path;
    }
    for (const child of node.children ?? []) {
      const found = findFirstNotebookPath(child);
      if (found) {
        return found;
      }
    }
    return null;
  };

  function inferStandaloneRecovery(url: string, status: number, bodyText: string): RecoveryAdvice | undefined {
    if (
      status === 404
      && url.startsWith('/api/standalone/')
      && bodyText.includes('/api/standalone/')
    ) {
      return stalePreviewServerRecovery();
    }
    if (status >= 500 || bodyText.includes('Failed to fetch')) {
      return daemonUnavailableRecovery();
    }
    return undefined;
  }

  const postJson = async <T>(url: string, body: Record<string, unknown>): Promise<T> => {
    let response: Response;
    try {
      response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (error) {
      const wrapped = error instanceof Error ? error : new Error(String(error));
      (wrapped as Error & { recovery?: RecoveryAdvice }).recovery = daemonUnavailableRecovery();
      throw wrapped;
    }
    const text = await response.text();
    let payload: Record<string, unknown> = {};
    try {
      payload = text ? JSON.parse(text) : {};
    } catch {
      payload = {};
    }
    if (!response.ok) {
      const error = new Error(
        typeof payload?.error === 'string' && payload.error.trim()
          ? payload.error
          : `Request failed with status ${response.status}`,
      ) as Error & { conflict?: boolean; recovery?: RecoveryAdvice };
      error.conflict = Boolean(payload?.conflict);
      error.recovery = recoveryFromPayload(payload) ?? inferStandaloneRecovery(url, response.status, text);
      throw error;
    }
    return payload as T;
  };

  const syncLsp = async () => {
    try {
      const result = await postJson<{
        diagnostics_by_cell?: Record<string, Array<{
          from: number;
          to: number;
          severity: 'error' | 'warning' | 'info' | 'hint';
          message: string;
          source?: string;
        }>>;
        lsp_status?: { state: 'starting' | 'ready' | 'unavailable'; message: string };
      }>('/api/standalone/lsp/sync', {
        client_id: clientId,
        path: requireNotebookPath(),
        cells: activeCells.map((cell, index) => ({
          index,
          cell_id: cell.cell_id,
          cell_type: cell.cell_type,
          source: cell.source,
        })),
      });

      if (result.lsp_status) {
        dispatch({
          type: 'lsp-status',
          state: result.lsp_status.state,
          message: result.lsp_status.message,
        });
      }
      dispatch({
        type: 'lsp-diagnostics',
        diagnostics_by_cell: result.diagnostics_by_cell ?? {},
      });
    } catch (error) {
      logNonBlockingFailure('LSP sync', error);
      dispatch({
        type: 'lsp-status',
        state: 'unavailable',
        message: `Python IDE features unavailable: ${describeError(error)}`,
      });
    }
  };

  const ensureAttached = async () => {
    if (attached) {
      return;
    }
    await postJson('/api/standalone/attach', {
      client_id: clientId,
      path: requireNotebookPath(),
    });
    attached = true;
  };

  const loadContents = async (requestId?: string) => {
    await ensureAttached();
    const result = await postJson<{ cells?: NotebookCell[] }>('/api/standalone/notebook/contents', {
      client_id: clientId,
      path: requireNotebookPath(),
    });
    activeCells = (result.cells ?? []).map((cell, index) => ({ ...cell, index }));
    dispatch({
      type: 'contents',
      requestId,
      path: currentNotebookPath,
      cells: activeCells,
    });
    void syncLsp();
  };

  const loadWorkspaceTree = async (requestId?: string) => {
    const result = await postJson<{
      root?: WorkspaceTreeNode | null;
      workspace_name?: string;
      selected_path?: string | null;
    }>('/api/standalone/workspace-tree', {
      client_id: clientId,
      path: currentNotebookPath,
    });
    dispatch({
      type: 'workspace-tree',
      requestId,
      root: result.root ?? null,
      workspace_name: result.workspace_name ?? '',
      selected_path: result.selected_path ?? currentNotebookPath,
    });
    return result;
  };

  const loadKernels = async (requestId?: string) => {
    const result = await postJson<{
      kernels?: Array<{ id: string; label: string; recommended?: boolean }>;
      preferred_kernel?: { id: string; label: string };
    }>('/api/standalone/kernels', {
      client_id: clientId,
      path: currentNotebookPath,
    });
    dispatch({
      type: 'kernels',
      requestId,
      kernels: result.kernels ?? [],
      preferred_kernel: result.preferred_kernel,
    });
  };

  const loadRuntime = async (requestId?: string) => {
    await ensureAttached();
    const notebookPath = requireNotebookPath();
    const [runtimeResult, statusResult] = await Promise.all([
      postJson<{
        runtime?: {
          busy?: boolean;
          python_path?: string;
          current_execution?: Record<string, unknown> | null;
        } | null;
        runtime_record?: {
          label?: string;
        } | null;
      }>('/api/standalone/notebook/runtime', {
        client_id: clientId,
        path: notebookPath,
      }),
      postJson<{
        running?: Array<Record<string, unknown>>;
        queued?: Array<Record<string, unknown>>;
      }>('/api/standalone/notebook/status', {
        client_id: clientId,
        path: notebookPath,
      }),
    ]);
    const snapshot = buildRuntimeSnapshot({
      ...runtimeResult,
      running: Array.isArray(statusResult.running) ? statusResult.running : undefined,
      queued: Array.isArray(statusResult.queued) ? statusResult.queued : undefined,
    });
    dispatch({
      type: 'runtime',
      requestId,
      path: currentNotebookPath,
      busy: snapshot.busy,
      kernel_label: snapshot.kernel_label,
      current_execution: snapshot.current_execution,
      running_cell_ids: snapshot.running_cell_ids,
      queued_cell_ids: snapshot.queued_cell_ids,
    });
  };

  const handleWsMessage = (msg: any) => {
    if (!attached) return;
    const result = {
      recent_events: [msg],
      runtime: msg.runtime,
      cursor: typeof msg.cursor === 'number' ? msg.cursor : undefined,
    };
    const activityResult = buildActivityPollResult(result, {
      cursorFallback: activityCursor,
      reloadOnSourceUpdates: true,
      inlineSourceUpdates: false,
    });
    if (activityResult.shouldReloadContents) {
      void loadContents();
    }
    const activitySnapshot = activityResult.activityUpdate;
    if (!activitySnapshot) return;

    dispatch({
      type: 'activity-update',
      events: activitySnapshot.events,
      presence: activitySnapshot.presence,
      leases: activitySnapshot.leases,
      runtime: activitySnapshot.runtime,
      cursor: activitySnapshot.cursor,
    });

    if (typeof msg.cursor === 'number') {
      activityCursor = msg.cursor;
    }
  };

  const stopWs = () => {
    if (daemonWs) {
      daemonWs.close();
      daemonWs = null;
    }
    if (touchTimer != null) {
      window.clearInterval(touchTimer);
      touchTimer = null;
    }
  };

  const startWs = () => {
    if (daemonWs || !currentNotebookPath) return;
    // Same-origin connection — the preview server proxies to the daemon.
    const origin = window.location.origin;
    daemonWs = new DaemonWebSocket({
      daemonUrl: origin,
      daemonToken: '', // same-origin; proxy handles auth
      fetchFn: window.fetch.bind(window),
      createSocket: (url: string) => new WebSocket(url) as any,
      onMessage: handleWsMessage,
      onConnect: () => {
        if (currentNotebookPath) {
          daemonWs?.subscribe(currentNotebookPath);
        }
      },
      onDisconnect: () => { /* reconnect is automatic */ },
      onInstanceChange: () => {
        // Daemon restarted — full reload.
        void loadContents();
        void loadRuntime();
      },
    });
    daemonWs.connect();
    if (touchTimer == null) {
      touchTimer = window.setInterval(() => {
        void postJson('/api/standalone/session-touch', {
          client_id: clientId,
          path: currentNotebookPath,
        }).catch(() => undefined);
      }, 15_000);
    }
  };

  const endSession = () => {
    stopWs();
    void fetch('/api/standalone/session-end', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId }),
      keepalive: true,
    }).catch(() => undefined);
  };

  window.addEventListener('beforeunload', endSession);

  return {
    postMessage(message) {
      void (async () => {
        try {
          switch (message.type) {
            case 'webview-ready':
              {
                const workspaceResult = await loadWorkspaceTree(message.requestId).catch((error) => {
                  logNonBlockingFailure('workspace bootstrap', error);
                  return null;
                });
                if (!currentNotebookPath) {
                  const fallbackPath = workspaceResult?.selected_path ?? findFirstNotebookPath(workspaceResult?.root);
                  if (fallbackPath) {
                    currentNotebookPath = fallbackPath;
                    await loadWorkspaceTree().catch((error) => {
                      logNonBlockingFailure('workspace selection sync', error);
                    });
                  }
                }
              }
              if (!currentNotebookPath) {
                dispatch({ type: 'ok', requestId: message.requestId });
                break;
              }
              await ensureAttached();
              startWs();
              void loadRuntime().catch((error) => {
                logNonBlockingFailure('runtime bootstrap', error);
              });
              void loadContents(message.requestId).catch((error) => {
                logNonBlockingFailure('contents bootstrap', error);
              });
              void loadKernels().catch((error) => {
                logNonBlockingFailure('kernel bootstrap', error);
              });
              break;
            case 'load-contents':
              if (!currentNotebookPath) {
                dispatch({ type: 'ok', requestId: message.requestId });
                break;
              }
              await loadContents(message.requestId);
              break;
            case 'get-workspace-tree':
              await loadWorkspaceTree(message.requestId);
              break;
            case 'switch-notebook':
              if (typeof message.path !== 'string' || !message.path.trim()) {
                throw new Error('Missing notebook path');
              }
              stopWs();
              currentNotebookPath = message.path.trim();
              activityCursor = 0;
              activeCells = [];
              dispatch({ type: 'ok', requestId: message.requestId });
              await loadWorkspaceTree();
              await loadContents(message.requestId);
              await loadRuntime();
              startWs();
              break;
            case 'get-kernels':
              await loadKernels(message.requestId);
              break;
            case 'get-runtime':
              if (!currentNotebookPath) {
                dispatch({ type: 'ok', requestId: message.requestId });
                break;
              }
              await loadRuntime(message.requestId);
              break;
            case 'flush-draft':
              {
                const cellId = typeof message.cell_id === 'string' ? message.cell_id : '';
                const source = typeof message.source === 'string' ? message.source : '';
                await postJson('/api/standalone/notebook/edit', {
                  client_id: clientId,
                  path: requireNotebookPath(),
                  operations: [buildReplaceSourceOperation({
                    cell_id: cellId,
                    ...(typeof message.cell_index === 'number' ? { cell_index: message.cell_index } : {}),
                    source,
                  })],
                });
              }
              await loadContents();
              dispatch({ type: 'ok', requestId: message.requestId });
              break;
            case 'save-notebook':
              {
                const changes = normalizeDraftChanges(message.changes);
                if (changes.length === 0) {
                  dispatch({ type: 'ok', requestId: message.requestId });
                  break;
                }
                await postJson('/api/standalone/notebook/edit', {
                  client_id: clientId,
                  path: requireNotebookPath(),
                  operations: buildReplaceSourceOperations(changes),
                });
                await loadContents(message.requestId);
                dispatch({ type: 'ok', requestId: message.requestId });
              }
              break;
            case 'edit':
              await postJson('/api/standalone/notebook/edit', {
                client_id: clientId,
                path: requireNotebookPath(),
                operations: message.operations,
              });
              await loadContents(message.requestId);
              dispatch({ type: 'ok', requestId: message.requestId });
              break;
            case 'execute-cell':
              await ensureAttached();
              console.debug('[queue-debug] host: execute-cell request', {
                cell_id: message.cell_id,
                hasSourceOverride: typeof message.source === 'string',
              });
              if (typeof message.source === 'string') {
                const cellId = typeof message.cell_id === 'string' ? message.cell_id : '';
                await postJson('/api/standalone/notebook/edit', {
                  client_id: clientId,
                  path: requireNotebookPath(),
                  operations: [buildReplaceSourceOperation({
                    cell_id: cellId,
                    ...(typeof message.cell_index === 'number' ? { cell_index: message.cell_index } : {}),
                    source: message.source,
                  })],
                });
              }
              const executeResult = await postJson<{
                status?: string;
                cell_id?: string | null;
                error?: string;
              }>('/api/standalone/notebook/execute-cell', {
                client_id: clientId,
                path: requireNotebookPath(),
                cell_id: message.cell_id,
                ...(typeof message.cell_index === 'number' ? { cell_index: message.cell_index } : {}),
              });
              const resultStatus = typeof executeResult?.status === 'string' ? executeResult.status : 'started';
              const resultCellId = typeof executeResult?.cell_id === 'string' && executeResult.cell_id
                ? executeResult.cell_id
                : message.cell_id;
              if (resultStatus === 'started') {
                console.debug('[queue-debug] host: dispatching execute-started', { cell_id: resultCellId });
                dispatch({
                  type: 'execute-started',
                  requestId: message.requestId,
                  cell_id: resultCellId,
                });
              } else if (resultStatus === 'error') {
                dispatch({
                  type: 'execute-failed',
                  requestId: message.requestId,
                  cell_id: resultCellId,
                  ok: false,
                  message: typeof executeResult?.error === 'string' && executeResult.error.trim()
                    ? executeResult.error
                    : 'Execution failed.',
                });
              } else {
                dispatch({ type: 'ok', requestId: message.requestId });
              }
              void loadRuntime().catch((error) => {
                logNonBlockingFailure('runtime refresh after execute-cell start', error);
              });
              break;
            case 'interrupt-execution':
              await postJson('/api/standalone/notebook/interrupt', {
                client_id: clientId,
                path: requireNotebookPath(),
              });
              dispatch({ type: 'ok', requestId: message.requestId });
              await loadContents();
              await loadRuntime();
              break;
            case 'execute-all':
              await ensureAttached();
              await postJson('/api/standalone/notebook/execute-all-async', {
                client_id: clientId,
                path: requireNotebookPath(),
              });
              dispatch({ type: 'ok', requestId: message.requestId });
              void loadRuntime().catch((error) => {
                logNonBlockingFailure('runtime refresh after execute-all start', error);
              });
              break;
            case 'select-kernel':
              await postJson('/api/standalone/notebook/select-kernel', {
                client_id: clientId,
                path: requireNotebookPath(),
                kernel_id: message.kernel_id,
              });
              dispatch({ type: 'ok', requestId: message.requestId });
              await loadKernels();
              await loadRuntime();
              break;
            case 'restart-kernel':
              await postJson('/api/standalone/notebook/restart', {
                client_id: clientId,
                path: requireNotebookPath(),
              });
              dispatch({ type: 'ok', requestId: message.requestId });
              await loadContents();
              await loadRuntime();
              break;
            case 'restart-and-run-all':
              await ensureAttached();
              await postJson('/api/standalone/notebook/restart-and-run-all-async', {
                client_id: clientId,
                path: requireNotebookPath(),
              });
              dispatch({ type: 'ok', requestId: message.requestId });
              void loadRuntime().catch((error) => {
                logNonBlockingFailure('runtime refresh after restart-and-run-all start', error);
              });
              break;
            case 'lsp-sync-cell': {
              const cellId = typeof message.cell_id === 'string' ? message.cell_id : '';
              const source = typeof message.source === 'string' ? message.source : '';
              activeCells = activeCells.map((cell) => (
                cell.cell_id === cellId ? { ...cell, source } : cell
              ));
              await syncLsp();
              break;
            }
            case 'open-external-link':
              if (typeof message.url === 'string') {
                window.open(message.url, '_blank', 'noopener,noreferrer');
              }
              dispatch({ type: 'ok', requestId: message.requestId });
              break;
            default:
              dispatch({ type: 'ok', requestId: message.requestId });
              break;
          }
        } catch (error) {
          const typedError = error as Error & { conflict?: boolean; recovery?: RecoveryAdvice };
          dispatch({
            type: 'error',
            requestId: message.requestId,
            message: typedError.message,
            conflict: Boolean(typedError.conflict),
            recovery: typedError.recovery,
          });
        }
      })();
    },
  };
}

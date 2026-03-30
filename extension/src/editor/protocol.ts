import type { RecoveryAdvice } from '../shared/recovery';

/**
 * Message protocol between the Canvas Editor WebView and the extension host.
 * All messages carry a `type` discriminator. Requests from the WebView include
 * a `requestId` for response correlation.
 */

// ---------------------------------------------------------------------------
// WebView → Extension Host (requests)
// ---------------------------------------------------------------------------

export type WebViewRequest =
    | LoadContentsRequest
    | EditRequest
    | ExecuteCellRequest
    | InterruptExecutionRequest
    | ExecuteAllRequest
    | SelectKernelRequest
    | RestartKernelRequest
    | RestartAndRunAllRequest
    | GetKernelsRequest
    | GetRuntimeRequest
    | FlushDraftRequest
    | LspSyncCellRequest
    | LspCompletionRequest
    | LspDefinitionRequest
    | OpenExternalLinkRequest;

interface BaseRequest {
    requestId: string;
    path?: string;
}

export interface LoadContentsRequest extends BaseRequest {
    type: 'load-contents';
}

export interface EditRequest extends BaseRequest {
    type: 'edit';
    operations: EditOperation[];
}

export type EditOperation =
    | { op: 'insert'; source: string; cell_type: 'code' | 'markdown'; at_index: number }
    | { op: 'delete'; cell_id: string }
    | { op: 'replace-source'; cell_id: string; source: string }
    | { op: 'change-cell-type'; cell_id: string; cell_type: 'code' | 'markdown'; source?: string }
    | { op: 'move'; cell_id: string; to_index: number };

export interface ExecuteCellRequest extends BaseRequest {
    type: 'execute-cell';
    cell_id: string;
    source?: string;
}

export interface InterruptExecutionRequest extends BaseRequest {
    type: 'interrupt-execution';
}

export interface ExecuteAllRequest extends BaseRequest {
    type: 'execute-all';
}

export interface SelectKernelRequest extends BaseRequest {
    type: 'select-kernel';
    kernel_id: string;
}

export interface RestartKernelRequest extends BaseRequest {
    type: 'restart-kernel';
}

export interface RestartAndRunAllRequest extends BaseRequest {
    type: 'restart-and-run-all';
}

export interface GetKernelsRequest {
    type: 'get-kernels';
    requestId: string;
}

export interface GetRuntimeRequest extends BaseRequest {
    type: 'get-runtime';
}

export interface FlushDraftRequest extends BaseRequest {
    type: 'flush-draft';
    cell_id: string;
    source: string;
}

export interface LspSyncCellRequest extends BaseRequest {
    type: 'lsp-sync-cell';
    cell_id: string;
    source: string;
}

export interface LspCompletionRequest extends BaseRequest {
    type: 'lsp-complete';
    cell_id: string;
    source: string;
    offset: number;
    explicit?: boolean;
    trigger_character?: string;
}

export interface LspDefinitionRequest extends BaseRequest {
    type: 'lsp-definition';
    cell_id: string;
    source: string;
    offset: number;
}

export interface OpenExternalLinkRequest {
    type: 'open-external-link';
    requestId: string;
    url: string;
}

// ---------------------------------------------------------------------------
// Extension Host → WebView (responses and pushes)
// ---------------------------------------------------------------------------

export type ExtensionMessage =
    | ContentsResponse
    | EditResponse
    | ExecuteStartedResponse
    | ExecuteFinishedResponse
    | ExecuteFailedResponse
    | KernelsResponse
    | RuntimeResponse
    | ActivityUpdate
    | LspDiagnosticsMessage
    | LspCompletionMessage
    | LspDefinitionTargetMessage
    | LspStatusMessage
    | ErrorResponse
    | GenericOkResponse;

export interface ContentsResponse {
    type: 'contents';
    requestId?: string;
    path?: string;
    cells: CellData[];
}

export interface CellData {
    index: number;
    cell_id: string;
    cell_type: 'code' | 'markdown' | 'raw';
    source: string;
    outputs: CellOutput[];
    execution_count: number | null;
    display_number: number | null;
    metadata?: Record<string, any>;
}

export interface CellOutput {
    output_type: string;
    name?: string;
    text?: string;
    ename?: string;
    evalue?: string;
    traceback?: string[];
    data?: Record<string, any>;
    metadata?: Record<string, any>;
}

export interface EditResponse {
    type: 'edit-result';
    requestId: string;
    results: Array<{ op: string; changed: boolean; cell_id?: string; cell_count: number }>;
}

export interface ExecuteStartedResponse {
    type: 'execute-started';
    requestId: string;
    execution_id?: string;
    cell_id?: string;
}

export interface ExecuteFinishedResponse {
    type: 'execute-finished';
    requestId: string;
    cell_id?: string;
    ok?: boolean;
}

export interface ExecuteFailedResponse {
    type: 'execute-failed';
    requestId: string;
    cell_id?: string;
    message: string;
}

export interface KernelsResponse {
    type: 'kernels';
    requestId: string;
    kernels: Array<{ id: string; label: string; recommended: boolean }>;
    preferred_kernel?: { id: string; label: string };
}

export interface RuntimeResponse {
    type: 'runtime';
    requestId?: string;
    active?: boolean;
    busy: boolean;
    kernel_label?: string;
    runtime_id?: string;
    kernel_generation?: number | null;
    current_execution?: { cell_id?: string; cell_index?: number } | null;
    running_cell_ids?: string[];
    queued_cell_ids?: string[];
}

export interface LspDiagnosticsMessage {
    type: 'lsp-diagnostics';
    diagnostics_by_cell: Record<string, Array<{
        from: number;
        to: number;
        severity: 'error' | 'warning' | 'info' | 'hint';
        message: string;
        source?: string;
    }>>;
}

export interface LspStatusMessage {
    type: 'lsp-status';
    state: 'starting' | 'ready' | 'unavailable';
    message: string;
}

export interface LspCompletionMessage {
    type: 'lsp-completions';
    requestId: string;
    cell_id: string;
    items: Array<{
        label: string;
        kind?: string;
        detail?: string;
        documentation?: string;
        apply?: string;
        sortText?: string;
        filterText?: string;
    }>;
 }

export interface LspDefinitionTargetMessage {
    type: 'lsp-definition-target';
    requestId?: string;
    cell_id: string;
    from: number;
    to: number;
}

export interface ActivityUpdate {
    type: 'activity-update';
    events: ActivityEvent[];
    presence: PresenceRecord[];
    leases: LeaseRecord[];
    runtime: {
        busy: boolean;
        kernel_label?: string;
        current_execution?: any;
        running_cell_ids?: string[];
        queued_cell_ids?: string[];
    } | null;
    cursor: number;
}

export interface ActivityEvent {
    event_id: string;
    path: string;
    event_type: string;
    detail: string;
    actor: string;
    session_id: string;
    cell_id: string | null;
    cell_index: number | null;
    data: any;
    timestamp: number;
}

export interface PresenceRecord {
    session_id: string;
    activity: string;
    cell_id: string | null;
    cell_index: number | null;
    session: { label: string; actor: string };
}

export interface LeaseRecord {
    lease_id: string;
    session_id: string;
    cell_id: string;
    kind: string;
    expires_at: number;
}

export interface ErrorResponse {
    type: 'error';
    requestId: string;
    message: string;
    conflict?: boolean;
    recovery?: RecoveryAdvice;
}

export interface GenericOkResponse {
    type: 'ok';
    requestId: string;
}

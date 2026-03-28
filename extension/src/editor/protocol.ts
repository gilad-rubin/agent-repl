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
    | ExecuteAllRequest
    | SelectKernelRequest
    | RestartKernelRequest
    | RestartAndRunAllRequest
    | GetKernelsRequest
    | GetRuntimeRequest
    | FlushDraftRequest
    | OpenExternalLinkRequest;

interface BaseRequest {
    requestId: string;
    path: string;
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
    | { op: 'move'; cell_id: string; to_index: number };

export interface ExecuteCellRequest extends BaseRequest {
    type: 'execute-cell';
    cell_id: string;
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
    | KernelsResponse
    | RuntimeResponse
    | ActivityUpdate
    | ErrorResponse
    | GenericOkResponse;

export interface ContentsResponse {
    type: 'contents';
    requestId?: string;
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

export interface KernelsResponse {
    type: 'kernels';
    requestId: string;
    kernels: Array<{ id: string; label: string; recommended: boolean }>;
    preferred_kernel?: { id: string; label: string };
}

export interface RuntimeResponse {
    type: 'runtime';
    requestId?: string;
    busy: boolean;
    kernel_label?: string;
    current_execution?: { cell_id?: string; cell_index?: number } | null;
}

export interface ActivityUpdate {
    type: 'activity-update';
    events: ActivityEvent[];
    presence: PresenceRecord[];
    leases: LeaseRecord[];
    runtime: { busy: boolean; current_execution?: any } | null;
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
}

export interface GenericOkResponse {
    type: 'ok';
    requestId: string;
}

export type RuntimeLike = {
    busy?: boolean;
    python_path?: string;
    environment?: string;
    current_execution?: Record<string, unknown> | null;
    runtime_id?: string;
    kernel_generation?: number | null;
} | null | undefined;

export type RuntimeRecordLike = {
    label?: string;
    runtime_id?: string;
    kernel_generation?: number | null;
} | null | undefined;

export type RuntimeEnvelopeLike = {
    active?: boolean;
    runtime?: RuntimeLike;
    runtime_record?: RuntimeRecordLike;
    current_execution?: Record<string, unknown> | null;
    running?: Array<Record<string, unknown>> | null;
    queued?: Array<Record<string, unknown>> | null;
};

export type RecentEventLike = {
    event_id: string;
    path: string;
    type: string;
    detail: string;
    actor: string;
    session_id: string;
    cell_id: string | null;
    cell_index: number | null;
    data: unknown;
    timestamp: number;
};

export type ActivityEnvelopeLike = RuntimeEnvelopeLike & {
    recent_events?: RecentEventLike[];
    presence?: unknown[];
    leases?: unknown[];
    cursor?: number;
};

export type RuntimeSnapshot = {
    active: boolean;
    busy: boolean;
    kernel_label?: string;
    runtime_id?: string;
    kernel_generation: number | null;
    current_execution: Record<string, unknown> | null;
    running_cell_ids: string[];
    queued_cell_ids: string[];
};

export type ActivityEventSnapshot = {
    event_id: string;
    path: string;
    event_type: string;
    detail: string;
    actor: string;
    session_id: string;
    cell_id: string | null;
    cell_index: number | null;
    data: unknown;
    timestamp: number;
};

export type ActivitySnapshot = {
    events: ActivityEventSnapshot[];
    presence: unknown[];
    leases: unknown[];
    runtime: RuntimeSnapshot | null;
    cursor: number;
};

function basenameish(raw: string | undefined): string | undefined {
    if (typeof raw !== 'string' || raw.trim() === '') {
        return undefined;
    }
    const normalized = raw.replace(/\\/g, '/');
    const segments = normalized.split('/').filter(Boolean);
    return segments.length > 0 ? segments[segments.length - 1] : normalized;
}

export function deriveRuntimeKernelLabel(
    runtime: RuntimeLike,
    runtimeRecord: RuntimeRecordLike,
): string | undefined {
    return runtimeRecord?.label
        ?? basenameish(runtime?.python_path)
        ?? basenameish(runtime?.environment);
}

export function buildRuntimeSnapshot(payload: RuntimeEnvelopeLike): RuntimeSnapshot {
    const runtime = payload.runtime;
    const runtimeRecord = payload.runtime_record;
    const currentExecution = runtime?.current_execution ?? payload.current_execution ?? null;
    const runningCellIds = collectExecutionCellIds(payload.running);
    const queuedCellIds = collectExecutionCellIds(payload.queued);
    const currentExecutionCellId = cellIdFromExecutionLike(currentExecution);
    if (currentExecutionCellId && !runningCellIds.includes(currentExecutionCellId)) {
        runningCellIds.unshift(currentExecutionCellId);
    }
    return {
        active: payload.active ?? Boolean(runtime),
        busy: runtime?.busy ?? false,
        kernel_label: deriveRuntimeKernelLabel(runtime, runtimeRecord),
        runtime_id: runtime?.runtime_id ?? runtimeRecord?.runtime_id,
        kernel_generation: runtime?.kernel_generation ?? runtimeRecord?.kernel_generation ?? null,
        current_execution: currentExecution,
        running_cell_ids: runningCellIds,
        queued_cell_ids: queuedCellIds.filter((cellId) => !runningCellIds.includes(cellId)),
    };
}

function collectExecutionCellIds(entries: Array<Record<string, unknown>> | null | undefined): string[] {
    const cellIds: string[] = [];
    for (const entry of entries ?? []) {
        const cellId = cellIdFromExecutionLike(entry);
        if (cellId && !cellIds.includes(cellId)) {
            cellIds.push(cellId);
        }
    }
    return cellIds;
}

function cellIdFromExecutionLike(entry: Record<string, unknown> | null | undefined): string | null {
    const cellId = entry?.cell_id;
    return typeof cellId === 'string' && cellId.trim() !== '' ? cellId : null;
}

export function mapActivityEvents(events: RecentEventLike[] | null | undefined): ActivityEventSnapshot[] {
    return (events ?? []).map((event) => ({
        event_id: event.event_id,
        path: event.path,
        event_type: event.type,
        detail: event.detail,
        actor: event.actor,
        session_id: event.session_id,
        cell_id: event.cell_id,
        cell_index: event.cell_index,
        data: event.data,
        timestamp: event.timestamp,
    }));
}

export function buildActivitySnapshot(
    payload: ActivityEnvelopeLike,
    options?: {
        cursorFallback?: number;
        includeDetachedRuntime?: boolean;
    },
): ActivitySnapshot {
    const includeDetachedRuntime = options?.includeDetachedRuntime ?? false;
    return {
        events: mapActivityEvents(payload.recent_events),
        presence: payload.presence ?? [],
        leases: payload.leases ?? [],
        runtime: payload.runtime || includeDetachedRuntime ? buildRuntimeSnapshot(payload) : null,
        cursor: payload.cursor ?? options?.cursorFallback ?? 0,
    };
}

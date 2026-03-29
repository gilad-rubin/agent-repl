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
};

export type RuntimeSnapshot = {
    active: boolean;
    busy: boolean;
    kernel_label?: string;
    runtime_id?: string;
    kernel_generation: number | null;
    current_execution: Record<string, unknown> | null;
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
    return {
        active: payload.active ?? Boolean(runtime),
        busy: runtime?.busy ?? false,
        kernel_label: deriveRuntimeKernelLabel(runtime, runtimeRecord),
        runtime_id: runtime?.runtime_id ?? runtimeRecord?.runtime_id,
        kernel_generation: runtime?.kernel_generation ?? runtimeRecord?.kernel_generation ?? null,
        current_execution: runtime?.current_execution ?? payload.current_execution ?? null,
    };
}

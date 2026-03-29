const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    buildActivitySnapshot,
    buildRuntimeSnapshot,
    deriveRuntimeKernelLabel,
} = require(path.resolve(__dirname, '../out/shared/runtimeSnapshot.js'));

test('deriveRuntimeKernelLabel prefers runtime record labels and falls back to basename-like paths', () => {
    assert.equal(
        deriveRuntimeKernelLabel(
            { python_path: '/opt/miniconda3/bin/python3', environment: '/tmp/.venv/bin/python' },
            { label: 'Shared Python' },
        ),
        'Shared Python',
    );
    assert.equal(
        deriveRuntimeKernelLabel(
            { python_path: '/opt/miniconda3/bin/python3' },
            undefined,
        ),
        'python3',
    );
    assert.equal(
        deriveRuntimeKernelLabel(
            { environment: 'C:\\Users\\me\\.venv\\Scripts\\python.exe' },
            undefined,
        ),
        'python.exe',
    );
});

test('buildRuntimeSnapshot keeps runtime and runtime-record metadata aligned', () => {
    assert.deepEqual(
        buildRuntimeSnapshot({
            runtime: {
                busy: true,
                python_path: '/opt/miniconda3/bin/python3',
                current_execution: { cell_id: 'cell-1' },
                runtime_id: 'rt-1',
                kernel_generation: 7,
            },
            runtime_record: {
                label: 'Notebook Python',
                runtime_id: 'rt-record',
                kernel_generation: 6,
            },
        }),
        {
            active: true,
            busy: true,
            kernel_label: 'Notebook Python',
            current_execution: { cell_id: 'cell-1' },
            runtime_id: 'rt-1',
            kernel_generation: 7,
            running_cell_ids: ['cell-1'],
            queued_cell_ids: [],
        },
    );
});

test('buildRuntimeSnapshot falls back to runtime records when no live runtime is attached', () => {
    assert.deepEqual(
        buildRuntimeSnapshot({
            runtime: null,
            runtime_record: {
                label: 'Detached Kernel',
                runtime_id: 'rt-detached',
                kernel_generation: 3,
            },
            current_execution: { cell_id: 'stale-cell' },
        }),
        {
            active: false,
            busy: false,
            kernel_label: 'Detached Kernel',
            current_execution: { cell_id: 'stale-cell' },
            runtime_id: 'rt-detached',
            kernel_generation: 3,
            running_cell_ids: ['stale-cell'],
            queued_cell_ids: [],
        },
    );
});

test('buildRuntimeSnapshot derives queued and running cell ids from server-owned status payloads', () => {
    assert.deepEqual(
        buildRuntimeSnapshot({
            runtime: {
                busy: true,
                current_execution: { cell_id: 'cell-running' },
            },
            queued: [
                { run_id: 'run-2', cell_id: 'cell-queued-1', queue_position: 1 },
                { run_id: 'run-3', cell_id: 'cell-queued-2', queue_position: 2 },
            ],
            running: [
                { run_id: 'run-1', cell_id: 'cell-running' },
            ],
        }),
        {
            active: true,
            busy: true,
            kernel_label: undefined,
            current_execution: { cell_id: 'cell-running' },
            runtime_id: undefined,
            kernel_generation: null,
            running_cell_ids: ['cell-running'],
            queued_cell_ids: ['cell-queued-1', 'cell-queued-2'],
        },
    );
});

test('buildActivitySnapshot maps event payloads and includes detached runtime only when requested', () => {
    const payload = {
        recent_events: [{
            event_id: 'evt-1',
            path: 'notebooks/demo.ipynb',
            type: 'cell-source-updated',
            detail: 'updated',
            actor: 'human',
            session_id: 'sess-1',
            cell_id: 'cell-1',
            cell_index: 0,
            data: { cell: { cell_id: 'cell-1' } },
            timestamp: 123,
        }],
        presence: [{ session_id: 'sess-1' }],
        leases: [{ cell_id: 'cell-1' }],
        runtime: null,
        runtime_record: {
            label: 'Detached Kernel',
            runtime_id: 'rt-1',
            kernel_generation: 4,
        },
    };

    assert.deepEqual(
        buildActivitySnapshot(payload, { cursorFallback: 9 }),
        {
            events: [{
                event_id: 'evt-1',
                path: 'notebooks/demo.ipynb',
                event_type: 'cell-source-updated',
                detail: 'updated',
                actor: 'human',
                session_id: 'sess-1',
                cell_id: 'cell-1',
                cell_index: 0,
                data: { cell: { cell_id: 'cell-1' } },
                timestamp: 123,
            }],
            presence: [{ session_id: 'sess-1' }],
            leases: [{ cell_id: 'cell-1' }],
            runtime: null,
            cursor: 9,
        },
    );

    assert.deepEqual(
        buildActivitySnapshot(payload, {
            cursorFallback: 9,
            includeDetachedRuntime: true,
        }),
        {
            events: [{
                event_id: 'evt-1',
                path: 'notebooks/demo.ipynb',
                event_type: 'cell-source-updated',
                detail: 'updated',
                actor: 'human',
                session_id: 'sess-1',
                cell_id: 'cell-1',
                cell_index: 0,
                data: { cell: { cell_id: 'cell-1' } },
                timestamp: 123,
            }],
            presence: [{ session_id: 'sess-1' }],
            leases: [{ cell_id: 'cell-1' }],
            runtime: {
                active: false,
                busy: false,
                kernel_label: 'Detached Kernel',
                current_execution: null,
                runtime_id: 'rt-1',
                kernel_generation: 4,
                running_cell_ids: [],
                queued_cell_ids: [],
            },
            cursor: 9,
        },
    );
});

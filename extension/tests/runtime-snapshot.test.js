const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
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
        },
    );
});

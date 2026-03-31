const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadQueueModule() {
    const modulePath = path.resolve(__dirname, '../out/execution/queue.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {};
        }
        return originalLoad.call(this, request, parent, isMain);
    };

    delete require.cache[modulePath];
    try {
        return require(modulePath);
    } finally {
        Module._load = originalLoad;
    }
}

const {
    applyJupyterOutput,
    applyNotebookOutput,
    iopubMessageToJupyterOutput,
} = loadQueueModule();

test('applyJupyterOutput replaces display data that shares a display_id', () => {
    const outputs = [
        {
            output_type: 'display_data',
            data: { 'text/plain': '0%' },
            metadata: { foo: 'initial' },
            transient: { display_id: 'progress-1' },
        },
    ];

    const updated = applyJupyterOutput(outputs, {
        output_type: 'update_display_data',
        data: { 'text/plain': '50%' },
        metadata: { foo: 'updated' },
        transient: { display_id: 'progress-1' },
    });

    assert.equal(updated.length, 1);
    assert.deepEqual(updated[0].data, { 'text/plain': '50%' });
    assert.deepEqual(updated[0].metadata, { foo: 'updated' });
    assert.deepEqual(updated[0].transient, { display_id: 'progress-1' });
    assert.equal(updated[0].output_type, 'display_data');
});

test('applyNotebookOutput replaces an existing notebook output with the same display_id', () => {
    const existing = {
        metadata: { transient: { display_id: 'progress-1' } },
        items: [{ mime: 'text/plain', data: Buffer.from('0%') }],
    };
    const untouched = {
        metadata: {},
        items: [{ mime: 'text/plain', data: Buffer.from('other') }],
    };
    const replacement = {
        metadata: { transient: { display_id: 'progress-1' } },
        items: [{ mime: 'text/plain', data: Buffer.from('75%') }],
    };

    const updated = applyNotebookOutput([existing, untouched], replacement);

    assert.equal(updated.length, 2);
    assert.equal(updated[0], replacement);
    assert.equal(updated[1], untouched);
});

test('iopubMessageToJupyterOutput preserves display metadata and clear_output wait state', () => {
    const update = iopubMessageToJupyterOutput({
        header: { msg_type: 'update_display_data' },
        content: {
            data: { 'text/plain': 'step 2' },
            metadata: { foo: 'bar' },
            transient: { display_id: 'progress-1' },
        },
    });
    const clear = iopubMessageToJupyterOutput({
        header: { msg_type: 'clear_output' },
        content: { wait: true },
    });

    assert.deepEqual(update, {
        output_type: 'update_display_data',
        data: { 'text/plain': 'step 2' },
        metadata: { foo: 'bar' },
        transient: { display_id: 'progress-1' },
    });
    assert.deepEqual(clear, {
        output_type: 'clear_output',
        wait: true,
    });
});


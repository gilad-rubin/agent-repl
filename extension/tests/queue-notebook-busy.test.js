const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadQueueModule() {
    const modulePath = path.resolve(__dirname, '../out/execution/queue.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                extensions: { getExtension: () => undefined },
            };
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

test('notebook busy state is now daemon-owned — no local tracking in queue module', () => {
    const queue = loadQueueModule();

    // Local status and queue tracking have been removed.
    // Busy state is derived from daemon WebSocket events via shared/executionState.ts.
    assert.equal(queue.getStatus, undefined);
    assert.equal(queue.startExecution, undefined);
});

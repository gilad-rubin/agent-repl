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

test('execution is now daemon-owned — queue module has no local execution paths', () => {
    const queue = loadQueueModule();

    // Local execution queue functions have been removed.
    // Execution goes through daemon HTTP routes in routes.ts.
    assert.equal(queue.executeCell, undefined);
    assert.equal(queue.startExecution, undefined);
    assert.equal(queue.resetExecutionState, undefined);
});

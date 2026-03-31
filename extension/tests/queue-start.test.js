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

test('startExecution is no longer exported — execution goes through daemon HTTP', () => {
    const queue = loadQueueModule();
    assert.equal(queue.startExecution, undefined);
    assert.equal(queue.executeCell, undefined);
});

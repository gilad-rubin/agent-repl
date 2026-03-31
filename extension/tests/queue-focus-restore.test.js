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

test('queue module exports output helpers and Jupyter API accessors', () => {
    const queue = loadQueueModule();
    assert.equal(typeof queue.iopubMessageToJupyterOutput, 'function');
    assert.equal(typeof queue.applyNotebookOutput, 'function');
    assert.equal(typeof queue.applyJupyterOutput, 'function');
    assert.equal(typeof queue.getJupyterApi, 'function');
    assert.equal(typeof queue.resetJupyterApiCache, 'function');
});

test('queue module no longer exports local queue functions', () => {
    const queue = loadQueueModule();
    assert.equal(queue.executeCell, undefined);
    assert.equal(queue.startExecution, undefined);
    assert.equal(queue.getExecution, undefined);
    assert.equal(queue.getStatus, undefined);
    assert.equal(queue.resetExecutionState, undefined);
    assert.equal(queue.insertAndExecute, undefined);
    assert.equal(queue.startNotebookExecutionAll, undefined);
});

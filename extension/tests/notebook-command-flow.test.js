const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    runNotebookCommandFlow,
} = require(path.resolve(__dirname, '../out/shared/notebookCommandFlow.js'));

test('runNotebookCommandFlow calls onSuccess after a successful run', async () => {
    const calls = [];

    runNotebookCommandFlow({
        run: async () => 'ok',
        onSuccess: async (result) => { calls.push(['success', result]); },
        onError: async (error) => { calls.push(['error', error]); },
    });

    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(calls, [['success', 'ok']]);
});

test('runNotebookCommandFlow lets conflict handlers short-circuit errors', async () => {
    const calls = [];
    const error = new Error('conflict');

    runNotebookCommandFlow({
        run: async () => { throw error; },
        onSuccess: async () => { calls.push(['success']); },
        onConflict: async (received) => {
            calls.push(['conflict', received === error]);
            return true;
        },
        onError: async () => { calls.push(['error']); },
    });

    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(calls, [['conflict', true]]);
});

test('runNotebookCommandFlow calls onError when conflicts are not handled', async () => {
    const calls = [];
    const error = new Error('boom');

    runNotebookCommandFlow({
        run: async () => { throw error; },
        onSuccess: async () => { calls.push(['success']); },
        onConflict: async () => false,
        onError: async (received) => { calls.push(['error', received === error]); },
    });

    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(calls, [['error', true]]);
});

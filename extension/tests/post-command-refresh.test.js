const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { postCommandRefreshSpec } = require(
    path.resolve(__dirname, '../out/shared/postCommandRefresh.js'),
);

test('execute-cell ok refreshes both contents and runtime', () => {
    const spec = postCommandRefreshSpec('execute-cell', 'ok');
    assert.deepEqual(spec, { loadContents: true, loadRuntime: true });
});

test('execute-cell started refreshes runtime only', () => {
    const spec = postCommandRefreshSpec('execute-cell', 'started');
    assert.deepEqual(spec, { loadContents: false, loadRuntime: true });
});

test('execute-cell queued refreshes runtime only', () => {
    const spec = postCommandRefreshSpec('execute-cell', 'queued');
    assert.deepEqual(spec, { loadContents: false, loadRuntime: true });
});

test('execute-cell error refreshes runtime only', () => {
    const spec = postCommandRefreshSpec('execute-cell', 'error');
    assert.deepEqual(spec, { loadContents: false, loadRuntime: true });
});

test('execute-all refreshes both contents and runtime', () => {
    assert.deepEqual(postCommandRefreshSpec('execute-all'), {
        loadContents: true,
        loadRuntime: true,
    });
});

test('restart-and-run-all refreshes both contents and runtime', () => {
    assert.deepEqual(postCommandRefreshSpec('restart-and-run-all'), {
        loadContents: true,
        loadRuntime: true,
    });
});

test('restart-kernel refreshes both contents and runtime', () => {
    assert.deepEqual(postCommandRefreshSpec('restart-kernel'), {
        loadContents: true,
        loadRuntime: true,
    });
});

test('interrupt-execution refreshes both contents and runtime', () => {
    assert.deepEqual(postCommandRefreshSpec('interrupt-execution'), {
        loadContents: true,
        loadRuntime: true,
    });
});

test('select-kernel refreshes runtime only', () => {
    assert.deepEqual(postCommandRefreshSpec('select-kernel'), {
        loadContents: false,
        loadRuntime: true,
    });
});

test('flush-draft refreshes contents only', () => {
    assert.deepEqual(postCommandRefreshSpec('flush-draft'), {
        loadContents: true,
        loadRuntime: false,
    });
});

test('save-notebook refreshes contents only', () => {
    assert.deepEqual(postCommandRefreshSpec('save-notebook'), {
        loadContents: true,
        loadRuntime: false,
    });
});

test('edit refreshes contents only', () => {
    assert.deepEqual(postCommandRefreshSpec('edit'), {
        loadContents: true,
        loadRuntime: false,
    });
});

test('unknown command returns no refresh', () => {
    assert.deepEqual(postCommandRefreshSpec('unknown-command'), {
        loadContents: false,
        loadRuntime: false,
    });
});

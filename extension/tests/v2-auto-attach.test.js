const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadV2Module(workspaceFolders = []) {
    const modulePath = path.resolve(__dirname, '../out/v2.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return {
                workspace: { workspaceFolders },
                env: { appName: 'VS Code' },
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

test('v2CliPlans prefers uv run when a pyproject exists in the workspace root', () => {
    const originalExistsSync = fs.existsSync;
    fs.existsSync = (target) => target === '/workspace/pyproject.toml';

    try {
        const { v2CliPlans } = loadV2Module();
        const plans = v2CliPlans('/workspace', { get: () => undefined });
        assert.deepEqual(plans[0], { command: 'uv', args: ['run', 'agent-repl'], cwd: '/workspace' });
        assert.deepEqual(plans[1], { command: 'agent-repl', args: [], cwd: '/workspace' });
    } finally {
        fs.existsSync = originalExistsSync;
    }
});

test('v2CliPlans prefers configured and workspace-local launchers before PATH fallbacks', () => {
    const originalExistsSync = fs.existsSync;
    fs.existsSync = (target) => (
        target === '/workspace/.venv/bin/agent-repl' ||
        target === '/workspace/pyproject.toml'
    );

    try {
        const { v2CliPlans } = loadV2Module();
        const plans = v2CliPlans('/workspace', { get: () => '/custom/agent-repl' });
        assert.deepEqual(plans[0], { command: '/custom/agent-repl', args: [], cwd: '/workspace' });
        assert.deepEqual(plans[1], { command: '/workspace/.venv/bin/agent-repl', args: [], cwd: '/workspace' });
        assert.deepEqual(plans[2], { command: 'uv', args: ['run', 'agent-repl'], cwd: '/workspace' });
        assert.deepEqual(plans[3], { command: 'agent-repl', args: [], cwd: '/workspace' });
    } finally {
        fs.existsSync = originalExistsSync;
    }
});

test('primaryWorkspaceRoot returns the first workspace folder path', () => {
    const { primaryWorkspaceRoot } = loadV2Module([{ uri: { fsPath: '/workspace' } }]);
    assert.equal(primaryWorkspaceRoot(), '/workspace');
});

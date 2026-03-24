const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const fs = require('node:fs');

function loadRoutesModule() {
    const modulePath = path.resolve(__dirname, '../out/routes.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return { workspace: { workspaceFolders: [] } };
        }
        if (request.endsWith('/server')) {
            return {};
        }
        if (request.endsWith('/notebook/resolver')) {
            return {};
        }
        if (request.endsWith('/notebook/operations')) {
            return {};
        }
        if (request.endsWith('/notebook/identity')) {
            return {};
        }
        if (request.endsWith('/notebook/outputs')) {
            return {};
        }
        if (request.endsWith('/execution/queue')) {
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
    discoverKernels,
    findKernelRecord,
    kernelSelectionGuidance,
} = loadRoutesModule();

test('discoverKernels reuses the matching kernelspec as the preferred workspace kernel', () => {
    const originalEnv = process.env.JUPYTER_PATH;
    const originalExistsSync = fs.existsSync;
    const originalReaddirSync = fs.readdirSync;
    const originalReadFileSync = fs.readFileSync;
    const fakeRoot = path.join('/tmp', 'agent-repl-test');
    const kernelsDir = path.join(fakeRoot, 'kernels');
    const specDir = path.join(kernelsDir, 'subtext-venv');
    const specFile = path.join(specDir, 'kernel.json');
    const workspacePython = path.join('/workspace', '.venv', 'bin', 'python');

    process.env.JUPYTER_PATH = fakeRoot;
    fs.existsSync = (target) => (
        target === kernelsDir ||
        target === specFile ||
        target === workspacePython
    );
    fs.readdirSync = (target) => {
        if (target !== kernelsDir) {
            throw new Error(`Unexpected dir: ${target}`);
        }
        return [{ name: 'subtext-venv', isDirectory: () => true }];
    };
    fs.readFileSync = (target) => {
        if (target !== specFile) {
            throw new Error(`Unexpected file: ${target}`);
        }
        return JSON.stringify({
            argv: [workspacePython],
            display_name: 'subtext (.venv)',
            language: 'python',
        });
    };

    try {
        const discovery = discoverKernels('/workspace');
        assert.equal(discovery.kernels.length, 1);
        assert.equal(discovery.preferred_kernel?.id, 'subtext-venv');
        assert.equal(discovery.preferred_kernel?.type, 'kernelspec');
        assert.equal(discovery.preferred_kernel?.recommended, true);
    } finally {
        if (originalEnv === undefined) {
            delete process.env.JUPYTER_PATH;
        } else {
            process.env.JUPYTER_PATH = originalEnv;
        }
        fs.existsSync = originalExistsSync;
        fs.readdirSync = originalReaddirSync;
        fs.readFileSync = originalReadFileSync;
    }
});

test('findKernelRecord prefers exact ids before same-path matches', () => {
    const discovery = {
        workspace: '/workspace',
        workspace_venv_python: '/workspace/.venv/bin/python',
        preferred_kernel: null,
        kernels: [
            {
                id: 'subtext-venv',
                label: 'subtext (.venv)',
                type: 'kernelspec',
                python: '/workspace/.venv/bin/python',
                kernelspec_name: 'subtext-venv',
                kernelspec_display_name: 'subtext (.venv)',
                source: '/kernels/subtext-venv',
                recommended: true,
            },
            {
                id: '/workspace/.venv/bin/python',
                label: 'subtext (.venv)',
                type: 'workspace-venv',
                python: '/workspace/.venv/bin/python',
                kernelspec_name: 'subtext-venv',
                kernelspec_display_name: 'subtext (.venv)',
                source: '/workspace/.venv/bin/python',
                recommended: true,
            },
        ],
    };

    assert.equal(
        findKernelRecord(discovery, '/workspace/.venv/bin/python')?.type,
        'workspace-venv',
    );
    assert.equal(
        findKernelRecord(discovery, 'subtext-venv')?.type,
        'kernelspec',
    );
});

test('kernelSelectionGuidance uses progressive disclosure and an actionable retry command', () => {
    const discovery = {
        workspace: '/workspace',
        workspace_venv_python: '/workspace/.venv/bin/python',
        preferred_kernel: {
            id: 'subtext-venv',
            label: 'subtext (.venv)',
            type: 'kernelspec',
            python: '/workspace/.venv/bin/python',
            kernelspec_name: 'subtext-venv',
            kernelspec_display_name: 'subtext (.venv)',
            source: '/kernels/subtext-venv',
            recommended: true,
        },
        kernels: [
            {
                id: 'subtext-venv',
                label: 'subtext (.venv)',
                type: 'kernelspec',
                python: '/workspace/.venv/bin/python',
                kernelspec_name: 'subtext-venv',
                kernelspec_display_name: 'subtext (.venv)',
                source: '/kernels/subtext-venv',
                recommended: true,
            },
            {
                id: 'python3',
                label: 'Python 3 (ipykernel)',
                type: 'kernelspec',
                python: 'python',
                kernelspec_name: 'python3',
                kernelspec_display_name: 'Python 3 (ipykernel)',
                source: '/kernels/python3',
                recommended: false,
            },
        ],
    };

    const guidance = kernelSelectionGuidance('notebooks/demo.ipynb', discovery, 'failed');
    assert.deepEqual(guidance.available_kernel_names, ['subtext (.venv)', 'Python 3 (ipykernel)']);
    assert.equal(guidance.available_kernel_count, 2);
    assert.equal(guidance.list_kernels_command, 'agent-repl kernels');
    assert.equal(
        guidance.open_picker_command,
        "agent-repl select-kernel 'notebooks/demo.ipynb' --interactive",
    );
    assert.equal(
        guidance.recommended_kernel_command,
        "agent-repl select-kernel 'notebooks/demo.ipynb' --kernel-id 'subtext-venv'",
    );
    assert.equal(
        guidance.select_kernel_command,
        "agent-repl select-kernel 'notebooks/demo.ipynb'",
    );
    assert.match(guidance.next_step, /workspace-preferred kernel/);
    assert.match(guidance.next_step, /--interactive/);
    assert.ok(!('available_kernels' in guidance));
});

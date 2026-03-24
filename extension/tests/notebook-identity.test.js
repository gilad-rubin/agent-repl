const test = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');

function loadIdentity(vscode) {
    const modulePath = path.resolve(__dirname, '../out/notebook/identity.js');
    const originalLoad = Module._load;
    Module._load = function patchedLoad(request, parent, isMain) {
        if (request === 'vscode') {
            return vscode;
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

test('resolveCell accepts index-* fallback ids for live notebooks', () => {
    const identity = loadIdentity({});
    const doc = {
        cellCount: 2,
        cellAt(index) {
            return {
                metadata: {
                    custom: {
                        'agent-repl': {
                            cell_id: `uuid-${index}`,
                        },
                    },
                },
            };
        },
    };

    assert.equal(identity.resolveCell(doc, { cell_id: 'index-1' }), 1);
});

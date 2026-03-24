const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

function newestVsix(extensionRoot) {
    const candidates = fs.readdirSync(extensionRoot)
        .filter((name) => name.startsWith('agent-repl-') && name.endsWith('.vsix'))
        .map((name) => {
            const filePath = path.join(extensionRoot, name);
            return { filePath, mtimeMs: fs.statSync(filePath).mtimeMs };
        })
        .sort((a, b) => b.mtimeMs - a.mtimeMs);
    if (candidates.length === 0) {
        throw new Error('No agent-repl-*.vsix artifact found. Run `npm run package` first.');
    }
    return candidates[0].filePath;
}

function unzip(args, cwd) {
    return childProcess.execFileSync('unzip', args, {
        cwd,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
    });
}

function main() {
    const extensionRoot = path.resolve(__dirname, '..');
    const vsixPath = process.argv[2] ? path.resolve(process.argv[2]) : newestVsix(extensionRoot);
    const listing = unzip(['-Z1', vsixPath], extensionRoot);
    assert.match(listing, /extension\/out\/extension\.js/);
    assert.match(listing, /extension\/out\/v2\.js/);
    assert.match(listing, /extension\/package\.json/);

    const manifestText = unzip(['-p', vsixPath, 'extension/package.json'], extensionRoot);
    const manifest = JSON.parse(manifestText);
    const properties = manifest?.contributes?.configuration?.properties ?? {};
    assert.ok(properties['agent-repl.sessionAutoAttach'], 'expected packaged manifest to include agent-repl.sessionAutoAttach');
    assert.ok(properties['agent-repl.cliCommand'], 'expected packaged manifest to include agent-repl.cliCommand');
    assert.equal(properties['agent-repl.v2AutoAttach'], undefined, 'did not expect packaged manifest to expose agent-repl.v2AutoAttach');
    assert.equal(properties['agent-repl.cliPath'], undefined, 'did not expect packaged manifest to expose agent-repl.cliPath');

    process.stdout.write(`Verified packaged auto-attach artifact: ${vsixPath}\n`);
}

main();

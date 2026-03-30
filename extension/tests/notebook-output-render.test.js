const test = require('node:test');
const assert = require('node:assert/strict');

const {
    getNotebookRichOutputRenderSpec,
    normalizeNotebookMimeText,
    stringifyNotebookJson,
} = require('../out/shared/notebookOutputRender.js');

test('rich output prefers html over plain text for dataframe-like bundles', () => {
    const spec = getNotebookRichOutputRenderSpec({
        output_type: 'display_data',
        data: {
            'text/plain': 'alpha  1',
            'text/html': '<table><tr><td>alpha</td><td>1</td></tr></table>',
        },
    });

    assert.deepEqual(spec, {
        kind: 'html',
        mime: 'text/html',
        value: '<table><tr><td>alpha</td><td>1</td></tr></table>',
    });
});

test('rich output prefers markdown bundles over plain text placeholders', () => {
    const spec = getNotebookRichOutputRenderSpec({
        output_type: 'display_data',
        data: {
            'text/plain': '<IPython.core.display.Markdown object>',
            'text/markdown': '## Summary\n\n| key | value |\n| --- | --- |\n| a | 1 |',
        },
    });

    assert.equal(spec?.kind, 'markdown');
    assert.equal(spec?.mime, 'text/markdown');
    assert.match(spec?.value ?? '', /^## Summary/);
});

test('rich output prefers application/json over plain text reprs', () => {
    const spec = getNotebookRichOutputRenderSpec({
        output_type: 'display_data',
        data: {
            'text/plain': "{'alpha': 1}",
            'application/json': { alpha: 1, items: ['x', 'y'] },
        },
    });

    assert.equal(spec?.kind, 'json');
    assert.equal(spec?.mime, 'application/json');
    assert.match(spec?.value ?? '', /"items": \[/);
});

test('normalizeNotebookMimeText joins notebook string arrays', () => {
    assert.equal(
        normalizeNotebookMimeText(['hello', '\n', 'world']),
        'hello\nworld',
    );
});

test('stringifyNotebookJson normalizes JSON strings for rendering', () => {
    assert.equal(
        stringifyNotebookJson('{"alpha":1}'),
        '{\n  "alpha": 1\n}',
    );
});

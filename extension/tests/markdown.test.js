const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { normalizeMarkdownSource } = require(path.resolve(__dirname, '../out/shared/markdown.js'));

test('normalizeMarkdownSource returns strings unchanged', () => {
    assert.equal(normalizeMarkdownSource('# Title'), '# Title');
});

test('normalizeMarkdownSource joins nbformat-style source arrays', () => {
    assert.equal(
        normalizeMarkdownSource(['# Title\n', 'body']),
        '# Title\nbody',
    );
});

test('normalizeMarkdownSource guards against nullish values', () => {
    assert.equal(normalizeMarkdownSource(undefined), '');
    assert.equal(normalizeMarkdownSource(null), '');
});

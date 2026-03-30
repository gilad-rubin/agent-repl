const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    DEFAULT_COMPLETION_MIN_IDENTIFIER_CHARS,
    DEFAULT_COMPLETION_TYPING_DELAY_MS,
    IDENTIFIER_COMPLETION_PATTERN,
    IDENTIFIER_COMPLETION_VALID_FOR,
    shouldRequestCompletion,
} = require(path.resolve(__dirname, '../out/shared/completion.js'));

test('shouldRequestCompletion ignores single-character implicit identifiers', () => {
    assert.equal(
        shouldRequestCompletion({ typedText: 'a' }),
        false,
    );
});

test('shouldRequestCompletion opens for multi-character identifiers', () => {
    assert.equal(
        shouldRequestCompletion({ typedText: 'ab' }),
        true,
    );
    assert.equal(DEFAULT_COMPLETION_MIN_IDENTIFIER_CHARS, 2);
});

test('shouldRequestCompletion always allows explicit completion and dot triggers', () => {
    assert.equal(
        shouldRequestCompletion({ explicit: true }),
        true,
    );
    assert.equal(
        shouldRequestCompletion({ triggerCharacter: '.' }),
        true,
    );
});

test('completion patterns cover identifier prefixes and incremental filtering', () => {
    assert.ok(IDENTIFIER_COMPLETION_PATTERN.test('alpha_2'));
    assert.ok(IDENTIFIER_COMPLETION_VALID_FOR.test('alpha_2'));
    assert.ok(IDENTIFIER_COMPLETION_VALID_FOR.test(''));
    assert.equal(DEFAULT_COMPLETION_TYPING_DELAY_MS, 180);
});

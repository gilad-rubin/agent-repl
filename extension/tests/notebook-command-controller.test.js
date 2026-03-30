const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    decideNotebookCommandKeyAction,
} = require(path.resolve(__dirname, '../out/shared/notebookCommandController.js'));

function createContext(overrides = {}) {
    return {
        mode: 'command',
        focusedIndex: 0,
        cellCount: 3,
        focusedPendingCell: false,
        ...overrides,
    };
}

function createEvent(overrides = {}) {
    return {
        key: '',
        shiftKey: false,
        metaKey: false,
        ctrlKey: false,
        defaultPrevented: false,
        isInteractive: false,
        ...overrides,
    };
}

test('command-mode b inserts a code cell below and keeps command mode', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'b' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [
        { type: 'insert-cell', where: 'below', nextMode: 'command' },
    ]);
});

test('command-mode a inserts a code cell above and keeps command mode', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'a' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [
        { type: 'insert-cell', where: 'above', nextMode: 'command' },
    ]);
});

test('cmd/ctrl-b is ignored so the browser shell can reserve it for sidebar toggle', () => {
    const metaDecision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'b', metaKey: true }),
        0,
        1_000,
    );
    const ctrlDecision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'b', ctrlKey: true }),
        0,
        1_000,
    );

    assert.equal(metaDecision.preventDefault, false);
    assert.equal(ctrlDecision.preventDefault, false);
    assert.deepEqual(metaDecision.actions, []);
    assert.deepEqual(ctrlDecision.actions, []);
});

test('command-mode m converts the selected cell to markdown', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'm' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [{ type: 'change-cell-type', cellType: 'markdown' }]);
});

test('command-mode y converts the selected cell to code', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'y' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [{ type: 'change-cell-type', cellType: 'code' }]);
});

test('enter upgrades a pending inserted cell into edit mode even when the pending index overlaps an existing cell slot', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 2, cellCount: 3, focusedPendingCell: true }),
        createEvent({ key: 'Enter' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [{ type: 'activate-pending-edit' }]);
});

test('shift-enter runs the focused cell and advances once', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 1, cellCount: 2 }),
        createEvent({ key: 'Enter', shiftKey: true }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [{ type: 'run-and-advance', index: 1 }]);
});

test('cmd/ctrl-enter runs the focused cell and advances once', () => {
    const metaDecision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 1, cellCount: 2 }),
        createEvent({ key: 'Enter', metaKey: true }),
        0,
        1_000,
    );
    const ctrlDecision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 1, cellCount: 2 }),
        createEvent({ key: 'Enter', ctrlKey: true }),
        0,
        1_000,
    );

    assert.equal(metaDecision.preventDefault, true);
    assert.equal(ctrlDecision.preventDefault, true);
    assert.deepEqual(metaDecision.actions, [{ type: 'run-and-advance', index: 1 }]);
    assert.deepEqual(ctrlDecision.actions, [{ type: 'run-and-advance', index: 1 }]);
});

test('arrow keys move focus in command mode', () => {
    const upDecision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 2, cellCount: 4 }),
        createEvent({ key: 'ArrowUp' }),
        0,
        1_000,
    );
    const downDecision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 1, cellCount: 4 }),
        createEvent({ key: 'ArrowDown' }),
        0,
        1_000,
    );

    assert.equal(upDecision.preventDefault, true);
    assert.deepEqual(upDecision.actions, [{ type: 'move-focus', delta: -1 }]);
    assert.equal(downDecision.preventDefault, true);
    assert.deepEqual(downDecision.actions, [{ type: 'move-focus', delta: 1 }]);
});

test('shift-arrow extends the current selection in command mode', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext({ focusedIndex: 1, cellCount: 4 }),
        createEvent({ key: 'ArrowDown', shiftKey: true }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [{ type: 'extend-selection', delta: 1 }]);
});

test('escape returns the notebook to command mode without preventing the event', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'Escape' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, false);
    assert.deepEqual(decision.actions, [{ type: 'set-command-mode' }]);
});

test('interactive editor events are ignored by the command controller', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'Enter', shiftKey: true, isInteractive: true }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, false);
    assert.deepEqual(decision.actions, []);
});

test('edit-mode events are ignored by the command controller', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext({ mode: 'edit' }),
        createEvent({ key: 'ArrowDown' }),
        0,
        1_000,
    );

    assert.equal(decision.preventDefault, false);
    assert.deepEqual(decision.actions, []);
});

test('double-d deletes selected cells and resets the timer', () => {
    const firstPress = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'd' }),
        0,
        1_000,
    );
    const secondPress = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'd' }),
        firstPress.nextLastDPressAt,
        1_300,
    );

    assert.equal(firstPress.preventDefault, true);
    assert.deepEqual(firstPress.actions, []);
    assert.equal(firstPress.nextLastDPressAt, 1_000);
    assert.deepEqual(secondPress.actions, [{ type: 'delete-selected' }]);
    assert.equal(secondPress.nextLastDPressAt, 0);
});

test('command-mode z requests notebook-level undo', () => {
    const decision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'z' }),
        1_000,
        1_300,
    );

    assert.equal(decision.preventDefault, true);
    assert.deepEqual(decision.actions, [{ type: 'undo-notebook' }]);
    assert.equal(decision.nextLastDPressAt, 0);
});

test('cmd/ctrl-z is ignored so editor-native undo can keep ownership', () => {
    const metaDecision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'z', metaKey: true }),
        0,
        1_000,
    );
    const ctrlDecision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'z', ctrlKey: true }),
        0,
        1_000,
    );

    assert.equal(metaDecision.preventDefault, false);
    assert.equal(ctrlDecision.preventDefault, false);
    assert.deepEqual(metaDecision.actions, []);
    assert.deepEqual(ctrlDecision.actions, []);
});

test('cmd/ctrl-a selects every cell instead of inserting above', () => {
    const metaDecision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'a', metaKey: true }),
        0,
        1_000,
    );
    const ctrlDecision = decideNotebookCommandKeyAction(
        createContext(),
        createEvent({ key: 'a', ctrlKey: true }),
        0,
        1_000,
    );

    assert.deepEqual(metaDecision.actions, [{ type: 'select-all' }]);
    assert.deepEqual(ctrlDecision.actions, [{ type: 'select-all' }]);
});

export type NotebookMode = 'command' | 'edit';

export type NotebookCommandKeyEvent = {
    key: string;
    shiftKey: boolean;
    metaKey: boolean;
    ctrlKey: boolean;
    defaultPrevented: boolean;
    isInteractive: boolean;
};

export type NotebookCommandContext = {
    mode: NotebookMode;
    focusedIndex: number;
    cellCount: number;
    focusedPendingCell: boolean;
};

export type NotebookCommandAction =
    | { type: 'select-all' }
    | { type: 'insert-cell'; where: 'above' | 'below'; nextMode: 'command' }
    | { type: 'change-cell-type'; cellType: 'code' | 'markdown' }
    | { type: 'delete-selected' }
    | { type: 'undo-notebook' }
    | { type: 'activate-pending-edit' }
    | { type: 'enter-edit'; index: number }
    | { type: 'run-and-advance'; index: number }
    | { type: 'move-focus'; delta: number }
    | { type: 'extend-selection'; delta: number }
    | { type: 'set-command-mode' };

export type NotebookCommandDecision = {
    preventDefault: boolean;
    actions: NotebookCommandAction[];
    nextLastDPressAt: number;
};

const NOOP_DECISION: NotebookCommandDecision = {
    preventDefault: false,
    actions: [],
    nextLastDPressAt: 0,
};

function hasFocusedCell(context: NotebookCommandContext): boolean {
    return context.focusedIndex >= 0 && context.focusedIndex < context.cellCount;
}

export function decideNotebookCommandKeyAction(
    context: NotebookCommandContext,
    event: NotebookCommandKeyEvent,
    lastDPressAt: number,
    now: number,
): NotebookCommandDecision {
    if (event.defaultPrevented || context.mode === 'edit' || event.isInteractive) {
        return {
            ...NOOP_DECISION,
            nextLastDPressAt: lastDPressAt,
        };
    }

    switch (event.key) {
        case 'a':
            if (event.metaKey || event.ctrlKey) {
                return {
                    preventDefault: true,
                    actions: [{ type: 'select-all' }],
                    nextLastDPressAt: lastDPressAt,
                };
            }
            return {
                preventDefault: true,
                actions: [{ type: 'insert-cell', where: 'above', nextMode: 'command' }],
                nextLastDPressAt: lastDPressAt,
            };
        case 'b':
            if (event.metaKey || event.ctrlKey) {
                return {
                    ...NOOP_DECISION,
                    nextLastDPressAt: lastDPressAt,
                };
            }
            return {
                preventDefault: true,
                actions: [{ type: 'insert-cell', where: 'below', nextMode: 'command' }],
                nextLastDPressAt: lastDPressAt,
            };
        case 'm':
            if (event.metaKey || event.ctrlKey) {
                return {
                    ...NOOP_DECISION,
                    nextLastDPressAt: lastDPressAt,
                };
            }
            return {
                preventDefault: true,
                actions: [{ type: 'change-cell-type', cellType: 'markdown' }],
                nextLastDPressAt: lastDPressAt,
            };
        case 'y':
            if (event.metaKey || event.ctrlKey) {
                return {
                    ...NOOP_DECISION,
                    nextLastDPressAt: lastDPressAt,
                };
            }
            return {
                preventDefault: true,
                actions: [{ type: 'change-cell-type', cellType: 'code' }],
                nextLastDPressAt: lastDPressAt,
            };
        case 'd':
            return now - lastDPressAt < 500
                ? {
                    preventDefault: true,
                    actions: [{ type: 'delete-selected' }],
                    nextLastDPressAt: 0,
                }
                : {
                    preventDefault: true,
                    actions: [],
                    nextLastDPressAt: now,
                };
        case 'z':
            if (event.metaKey || event.ctrlKey) {
                return {
                    ...NOOP_DECISION,
                    nextLastDPressAt: lastDPressAt,
                };
            }
            return {
                preventDefault: true,
                actions: [{ type: 'undo-notebook' }],
                nextLastDPressAt: 0,
            };
        case 'Backspace':
        case 'Delete':
            return {
                preventDefault: true,
                actions: [{ type: 'delete-selected' }],
                nextLastDPressAt: 0,
            };
        case 'Enter':
            if (context.focusedPendingCell) {
                return {
                    preventDefault: true,
                    actions: [{ type: 'activate-pending-edit' }],
                    nextLastDPressAt: lastDPressAt,
                };
            }
            if (!hasFocusedCell(context)) {
                return {
                    preventDefault: false,
                    actions: [],
                    nextLastDPressAt: lastDPressAt,
                };
            }
            return {
                preventDefault: true,
                actions: [
                    (event.shiftKey || event.metaKey || event.ctrlKey)
                        ? { type: 'run-and-advance', index: context.focusedIndex }
                        : { type: 'enter-edit', index: context.focusedIndex },
                ],
                nextLastDPressAt: lastDPressAt,
            };
        case 'ArrowUp':
            return {
                preventDefault: true,
                actions: [{ type: event.shiftKey ? 'extend-selection' : 'move-focus', delta: -1 }],
                nextLastDPressAt: lastDPressAt,
            };
        case 'ArrowDown':
            return {
                preventDefault: true,
                actions: [{ type: event.shiftKey ? 'extend-selection' : 'move-focus', delta: 1 }],
                nextLastDPressAt: lastDPressAt,
            };
        case 'Escape':
            return {
                preventDefault: false,
                actions: [{ type: 'set-command-mode' }],
                nextLastDPressAt: lastDPressAt,
            };
        default:
            return {
                ...NOOP_DECISION,
                nextLastDPressAt: lastDPressAt,
            };
    }
}

# VS Code Jupyter Parity Checklist

This canvas does not need to copy the VS Code Jupyter extension internals, but it does need to match the user-facing notebook contract closely enough that muscle memory still works.

## Command vs Edit Mode

- Command mode owns notebook-level shortcuts such as `a`, `b`, `dd`, `Enter`, and `Shift+Enter`.
- Edit mode owns editor-originated keys such as text selection, caret movement, and CodeMirror shortcuts.
- A single physical keypress must never be handled by both layers.
- Document-level handlers must ignore events that originate inside CodeMirror or any other interactive control.

## Command-Mode Behaviors

- `Enter` enters edit mode for the focused cell.
- `Shift+Enter` runs the focused code cell and advances exactly once.
- `b` inserts a code cell below in command mode.
- `a` inserts a code cell above in command mode.
- `Cmd/Ctrl+A` selects all cells instead of inserting above.
- `dd` deletes the selected or focused cells.
- `ArrowUp` and `ArrowDown` move focus.
- `Shift+ArrowUp` and `Shift+ArrowDown` extend the selection.

## Edit-Mode Behaviors

- `Escape` leaves edit mode and returns to command mode.
- `Shift+Enter` runs the cell from the editor and advances exactly once.
- Running the last code cell with `Shift+Enter` inserts a new code cell below and opens it in edit mode.
- Text selection, dragging, and double-click selection must remain inside the editor and must not be intercepted by notebook command handlers.

## Runtime Status Behaviors

- `Queued`, `Running`, `Completed`, and error states should come from live execution in the current kernel session.
- Saved notebook metadata must not rehydrate as fake live `Completed` status on load.
- Idle runtime state should clear stale queued or running markers when explicit completion messages were missed.

## Regression Coverage

- Unit-test the pure command controller for keyboard routing decisions.
- Keep regression tests for:
  - `b` then `Enter` promoting the new cell into edit mode
  - `Shift+Enter` executing once
  - editor-originated `Shift+Enter` not leaking into command mode
  - empty new notebooks opening as blank notebooks
  - stale `Running` state clearing when the runtime becomes idle

This checklist is the default acceptance bar for notebook interaction changes in the canvas.

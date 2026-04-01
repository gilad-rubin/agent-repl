# JupyterLab Keyboard Shortcuts — Complete Mapping

Comprehensive 1:1 mapping of JupyterLab 4.5+ notebook keyboard shortcuts and their implementation status in agent-repl.

Source: `@jupyterlab/notebook-extension`, `@jupyterlab/codemirror-extension`, `@jupyterlab/mainmenu-extension`, `@jupyterlab/shortcuts-extension` schema files.

`Accel` = `Cmd` on macOS, `Ctrl` on Windows/Linux.

---

## Command Mode

Active when a cell is selected but not being edited (blue left border).

### Cell Navigation

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `↓` | `notebook:move-cursor-down` | Select cell below | ✅ |
| `J` | `notebook:move-cursor-down` | Select cell below (vim) | ✅ |
| `↑` | `notebook:move-cursor-up` | Select cell above | ✅ |
| `K` | `notebook:move-cursor-up` | Select cell above (vim) | ✅ |
| `←` | `notebook:move-cursor-heading-above-or-collapse` | Heading above / collapse | ✅ |
| `→` | `notebook:move-cursor-heading-below-or-expand` | Heading below / expand | ✅ |

### Cell Selection (Multi-Select)

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Shift ↑` | `notebook:extend-marked-cells-above` | Extend selection up | ✅ |
| `Shift K` | `notebook:extend-marked-cells-above` | Extend selection up (vim) | ✅ |
| `Shift ↓` | `notebook:extend-marked-cells-below` | Extend selection down | ✅ |
| `Shift J` | `notebook:extend-marked-cells-below` | Extend selection down (vim) | ✅ |
| `Shift Home` | `notebook:extend-marked-cells-top` | Extend selection to first cell | ✅ |
| `Shift End` | `notebook:extend-marked-cells-bottom` | Extend selection to last cell | ✅ |
| `Accel A` | `notebook:select-all` | Select all cells | ✅ |

### Cell Type Conversion

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Y` | `notebook:change-cell-to-code` | Convert to Code | ✅ |
| `M` | `notebook:change-cell-to-markdown` | Convert to Markdown | ✅ |
| `R` | `notebook:change-cell-to-raw` | Convert to Raw | ✅ |
| `1` | `notebook:change-cell-to-heading-1` | Convert to Heading 1 | ✅ |
| `2` | `notebook:change-cell-to-heading-2` | Convert to Heading 2 | ✅ |
| `3` | `notebook:change-cell-to-heading-3` | Convert to Heading 3 | ✅ |
| `4` | `notebook:change-cell-to-heading-4` | Convert to Heading 4 | ✅ |
| `5` | `notebook:change-cell-to-heading-5` | Convert to Heading 5 | ✅ |
| `6` | `notebook:change-cell-to-heading-6` | Convert to Heading 6 | ✅ |

### Cell Insert / Delete

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `A` | `notebook:insert-cell-above` | Insert cell above | ✅ |
| `B` | `notebook:insert-cell-below` | Insert cell below | ✅ |
| `Shift A` | `notebook:insert-heading-above` | Insert heading above (same level) | ✅ |
| `Shift B` | `notebook:insert-heading-below` | Insert heading below (same level) | ✅ |
| `D, D` | `notebook:delete-cell` | Delete selected cell(s) | ✅ |

### Clipboard

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `C` | `notebook:copy-cell` | Copy cell(s) | ✅ |
| `X` | `notebook:cut-cell` | Cut cell(s) | ✅ |
| `V` | `notebook:paste-cell-below` | Paste cell(s) below | ✅ |

### Cell Merge

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Shift M` | `notebook:merge-cells` | Merge selected cells | ✅ |
| `Ctrl Backspace` | `notebook:merge-cell-above` | Merge with cell above | ✅ |
| `Ctrl Shift M` | `notebook:merge-cell-below` | Merge with cell below | ✅ |

### Cell Movement

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Ctrl Shift ↑` | `notebook:move-cell-up` | Move cell(s) up | ✅ |
| `Ctrl Shift ↓` | `notebook:move-cell-down` | Move cell(s) down | ✅ |

### Heading Collapse/Expand

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Ctrl Shift ←` | `notebook:collapse-all-headings` | Collapse all headings | ✅ |
| `Ctrl Shift →` | `notebook:expand-all-headings` | Expand all headings | ✅ |

### Undo / Redo (Cell-Level)

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Z` | `notebook:undo-cell-action` | Undo cell operation | ✅ |
| `Shift Z` | `notebook:redo-cell-action` | Redo cell operation | ✅ |

### Cell Execution

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Accel Enter` | `notebook:run-cell` | Run cell, stay on current | ✅ |
| `Alt Enter` | `notebook:run-cell-and-insert-below` | Run cell, insert below | ✅ |

### Display Toggles

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Shift L` | `viewmenu:line-numbering` | Toggle line numbers | ✅ |
| `Shift R` | `notebook:toggle-render-side-by-side-current` | Toggle side-by-side rendering | ✅ |

### Mode Switching

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Enter` | `notebook:enter-edit-mode` | Enter edit mode | ✅ |

### Kernel Operations

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `I, I` | `kernelmenu:interrupt` | Interrupt kernel | ✅ |
| `0, 0` | `kernelmenu:restart` | Restart kernel | ✅ |

---

## Edit Mode

Active when editing cell content (green left border, blinking cursor).

### Mode Switching

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Escape` | `notebook:enter-command-mode` | Enter command mode | ✅ |
| `Ctrl M` | `notebook:enter-command-mode` | Enter command mode (alt) | ✅ |

### Cell Execution

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Shift Enter` | `notebook:run-cell-and-select-next` | Run cell, advance | ✅ |
| `Accel Enter` | `notebook:run-cell` | Run cell, stay | ✅ |
| `Alt Enter` | `notebook:run-cell-and-insert-below` | Run cell, insert below | ✅ |

### Cell Splitting

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Ctrl Shift -` | `notebook:split-cell-at-cursor` | Split cell at cursor | ✅ |

### CodeMirror Editor (built-in via CM keymaps)

These are handled by CodeMirror extensions already loaded (`defaultKeymap`, `searchKeymap`, `historyKeymap`, `foldKeymap`, `completionKeymap`, `closeBracketsKeymap`):

| Keys | Action | Status |
|---|---|---|
| `Accel Z` | Undo text | ✅ (CM historyKeymap) |
| `Accel Shift Z` | Redo text | ✅ (CM historyKeymap) |
| `Accel F` | Find | ✅ (CM searchKeymap) |
| `Accel /` | Toggle line comment | ✅ (CM defaultKeymap) |
| `Tab` | Indent / accept completion | ✅ (CM + custom) |
| `Shift Tab` | Dedent | ✅ (CM defaultKeymap) |
| `↑` at doc start | Move to cell above | ✅ (custom cellKeymap) |
| `↓` at doc end | Move to cell below | ✅ (custom cellKeymap) |
| `F12` | Go to definition | ✅ (custom cellKeymap) |

---

## Global Shortcuts

| Keys | JupyterLab Command | Action | Status |
|---|---|---|---|
| `Accel S` | `docmanager:save` | Save | ✅ |

---

## Not Applicable (JupyterLab App-Level)

These JupyterLab shortcuts control the full IDE shell, not the notebook surface. They are either handled by VS Code natively or not relevant in the webview context:

- `Accel Shift C` — Command palette (VS Code owns this)
- `Accel H` — Find and replace (VS Code / CM owns this)
- `Accel G` / `Accel Shift G` — Find next/prev (CM searchKeymap)
- `Accel B` — Toggle left sidebar (mapped to explorer toggle)
- `Accel J` — Toggle right sidebar (VS Code owns this)
- `Ctrl Shift ]` / `[` — Tab navigation (VS Code owns this)
- `Alt W` — Close activity (VS Code owns this)
- `F11` — Fullscreen (OS-level)
- `Accel ,` — Settings (VS Code owns this)
- `Accel Shift L` — Launcher (N/A)
- File browser shortcuts — (N/A, separate explorer)
- Debugger shortcuts (`F9`, `F10`, `F11`, `Shift F11`) — (N/A, VS Code debugger owns this)
- `Alt ↑` / `Alt ↓` — History navigation (needs kernel history session, deferred)
- `Shift Tab` — Tooltip/introspection (needs kernel inspect protocol, deferred)
- Inline completer shortcuts (`Alt ]`, `Alt [`, `Alt \`, `Alt End`) — (deferred, requires inline completer infrastructure)

---

## Implementation Notes

### Registration Architecture

Shortcuts are registered in `extension/webview-src/jupyterlab-preview.tsx` using:
1. `CommandRegistry` from `@lumino/commands` (same as JupyterLab)
2. `commands.addCommand(id, { execute })` for command registration
3. `commands.addKeyBinding({ command, keys, selector })` for key bindings
4. Direct dispatch in `handleDocumentKeyDown` for performance-critical paths

### NotebookActions Used

All notebook operations use `NotebookActions` from `@jupyterlab/notebook` — the same methods JupyterLab uses internally. This ensures 1:1 behavioral fidelity.

### Filter: `shouldHandleNotebookShortcut`

This function gates which keys reach notebook command dispatch. It must be updated when adding new shortcuts.

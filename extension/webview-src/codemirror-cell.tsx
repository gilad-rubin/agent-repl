import React, { useEffect, useRef } from 'react';
import {
  EditorView,
  keymap,
  ViewUpdate,
  highlightActiveLine,
  highlightActiveLineGutter,
  drawSelection,
  dropCursor,
  rectangularSelection,
  crosshairCursor,
} from '@codemirror/view';
import { EditorState, Extension, Prec } from '@codemirror/state';
import { python } from '@codemirror/lang-python';
import { defaultKeymap, history, historyKeymap, indentMore } from '@codemirror/commands';
import {
  syntaxHighlighting,
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  foldGutter,
  foldKeymap,
} from '@codemirror/language';
import {
  autocompletion,
  Completion,
  CompletionContext,
  CompletionResult,
  acceptCompletion,
  completionKeymap,
  completionStatus,
  closeBrackets,
  closeBracketsKeymap,
} from '@codemirror/autocomplete';
import {
  Diagnostic,
  linter,
  setDiagnostics,
} from '@codemirror/lint';
import {
  highlightSelectionMatches,
  searchKeymap,
} from '@codemirror/search';
import { indentationMarkers } from '@replit/codemirror-indentation-markers';
import { tags } from '@lezer/highlight';
import {
  DEFAULT_COMPLETION_TYPING_DELAY_MS,
  IDENTIFIER_COMPLETION_PATTERN,
  IDENTIFIER_COMPLETION_VALID_FOR,
  shouldRequestCompletion,
} from '../src/shared/completion';

// ── Cursor Dark highlight theme ──────────────────────────

const cursorDarkHighlight = HighlightStyle.define([
  { tag: tags.keyword, color: '#83d6c5', fontWeight: '600' },
  { tag: tags.string, color: '#c582bf' },
  { tag: tags.comment, color: '#505050', fontStyle: 'italic' },
  { tag: tags.number, color: '#efb080' },
  { tag: tags.bool, color: '#efb080' },
  { tag: [tags.function(tags.variableName), tags.function(tags.definition(tags.variableName))], color: '#aa9bf5' },
  { tag: tags.typeName, color: '#aa9bf5' },
  { tag: tags.className, color: '#aa9bf5' },
  { tag: tags.definition(tags.variableName), color: '#d8dee9' },
  { tag: tags.variableName, color: '#d8dee9' },
  { tag: tags.propertyName, color: '#85c1fc' },
  { tag: tags.operator, color: '#83d6c5' },
  { tag: tags.self, color: '#83d6c5' },
  { tag: tags.special(tags.string), color: '#c582bf' },
]);

const cursorLightHighlight = HighlightStyle.define([
  { tag: tags.keyword, color: '#0369a1', fontWeight: '600' },
  { tag: tags.string, color: '#9333ea' },
  { tag: tags.comment, color: '#a8a29e', fontStyle: 'italic' },
  { tag: tags.number, color: '#c2410c' },
  { tag: tags.bool, color: '#c2410c' },
  { tag: [tags.function(tags.variableName), tags.function(tags.definition(tags.variableName))], color: '#6d28d9' },
  { tag: tags.typeName, color: '#6d28d9' },
  { tag: tags.className, color: '#6d28d9' },
  { tag: tags.definition(tags.variableName), color: '#1c1917' },
  { tag: tags.variableName, color: '#1c1917' },
  { tag: tags.propertyName, color: '#0369a1' },
  { tag: tags.operator, color: '#0369a1' },
  { tag: tags.self, color: '#0369a1' },
  { tag: tags.special(tags.string), color: '#9333ea' },
]);

// ── Editor theme — matches VS Code notebook cell style ───

function buildEditorTheme(opts: {
  fontFamily: string;
  fontSize: number;
  lineHeight: number;
  topPadding: number;
  bottomPadding: number;
  dark: boolean;
}) {
  const cursor = opts.dark ? '#d8dee9' : '#1c1917';
  const selection = opts.dark ? 'rgba(217,119,6,0.18)' : 'rgba(217,119,6,0.24)';
  const activeLine = opts.dark ? '#242526' : 'rgba(0,0,0,0.03)';
  const matchingBracket = opts.dark ? 'rgba(217,119,6,0.25)' : 'rgba(217,119,6,0.35)';
  const selectionMatch = opts.dark ? 'rgba(217,119,6,0.12)' : 'rgba(217,119,6,0.18)';
  // Gutter colors
  const foldColor = opts.dark ? '#838383' : '#a8a29e';
  const gutterColor = opts.dark ? '#4C4D4D' : '#a8a29e';

  return EditorView.theme({
    // Editor root — transparent, inherits cell background
    '&': {
      fontSize: `${opts.fontSize}px`,
      fontFamily: opts.fontFamily,
      backgroundColor: 'transparent',
    },
    '.cm-scroller': { overflow: 'auto', cursor: 'text' },
    '.cm-content': {
      fontFamily: opts.fontFamily,
      lineHeight: `${opts.lineHeight}`,
      padding: `${opts.topPadding}px 0 ${opts.bottomPadding}px 0`,
      caretColor: cursor,
      fontVariantLigatures: 'none',
      fontFeatureSettings: '"liga" 0, "calt" 0',
    },
    // Left padding on each line for breathing room
    '.cm-line': {
      padding: '0 8px 0 4px',
      fontFamily: opts.fontFamily,
    },

    // Cursor & selection
    '&.cm-focused .cm-cursor': { borderLeftColor: cursor },
    '&.cm-focused .cm-selectionBackground, .cm-selectionBackground': {
      backgroundColor: `${selection} !important`,
    },
    '&.cm-focused': { outline: 'none' },
    '.cm-matchingBracket': { backgroundColor: matchingBracket, outline: 'none' },
    '.cm-selectionMatch': { backgroundColor: selectionMatch },

    // Active line highlight — only when focused (edit mode)
    '.cm-activeLine': { backgroundColor: 'transparent' },
    '&.cm-focused .cm-activeLine': { backgroundColor: activeLine },
    '.cm-activeLineGutter': { backgroundColor: 'transparent' },
    '&.cm-focused .cm-activeLineGutter': { backgroundColor: activeLine },

    // Gutter — no line numbers, just fold arrows (VS Code notebook style)
    '.cm-gutters': {
      backgroundColor: 'transparent',
      border: 'none',
      color: gutterColor,
      fontFamily: opts.fontFamily,
    },
    // Fold gutter — sized to match toolbar icons
    '.cm-foldGutter': { width: '16px' },
    '.cm-foldGutter .cm-gutterElement': {
      color: foldColor,
      cursor: 'pointer',
      padding: '0',
      textAlign: 'center',
      fontSize: '14px',
      lineHeight: `${opts.lineHeight}`,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    },

    // Fold placeholder (the ... when collapsed)
    '.cm-foldPlaceholder': {
      backgroundColor: 'transparent',
      border: 'none',
      color: gutterColor,
      padding: '0 4px',
    },

    // Autocomplete popup
    '.cm-tooltip-autocomplete': {
      backgroundColor: opts.dark ? '#19191A' : '#ffffff',
      border: `1px solid ${opts.dark ? '#3a3a3a' : '#e5e5e5'}`,
      borderRadius: '4px',
      fontSize: `${opts.fontSize}px`,
      fontFamily: opts.fontFamily,
    },
    '.cm-tooltip-autocomplete > ul > li': { padding: '2px 8px' },
    '.cm-tooltip-autocomplete > ul > li[aria-selected]': {
      backgroundColor: opts.dark ? 'rgba(217,119,6,0.15)' : 'rgba(217,119,6,0.2)',
      color: opts.dark ? '#d8dee9' : '#1c1917',
    },

    // Search panel
    '.cm-panels': {
      backgroundColor: opts.dark ? '#19191A' : '#f5f5f4',
      borderBottom: `1px solid ${opts.dark ? '#3a3a3a' : '#e5e5e5'}`,
    },
    '.cm-searchMatch': {
      backgroundColor: opts.dark ? 'rgba(217,119,6,0.2)' : 'rgba(217,119,6,0.3)',
      borderRadius: '2px',
    },
    '.cm-searchMatch-selected': {
      backgroundColor: opts.dark ? 'rgba(217,119,6,0.35)' : 'rgba(217,119,6,0.45)',
    },
  }, { dark: opts.dark });
}

// ── Build the full extension set ─────────────────────────

function buildExtensions(opts: {
  fontSize: number;
  lineHeight: number;
  topPadding: number;
  bottomPadding: number;
  dark: boolean;
  callbacksRef: React.RefObject<typeof defaultCallbacks>;
  externalUpdateRef: React.RefObject<boolean>;
  requestCompletionsRef: React.RefObject<(
    source: string,
    offset: number,
    options?: CompletionRequestOptions,
  ) => Promise<CompletionOptionData[]>>;
  requestDefinitionRef: React.RefObject<DefinitionRequestHandler>;
  onMoveToAdjacentCellRef: React.RefObject<((direction: 'up' | 'down') => boolean) | undefined>;
}): Extension[] {
  const cellKeymap = keymap.of([
    { key: 'Shift-Enter', run: () => { opts.callbacksRef.current.onRunCell(); return true; } },
    { key: 'Mod-Enter', run: () => { opts.callbacksRef.current.onRunCell(); return true; } },
    {
      key: 'F12',
      run: (view) => {
        void opts.requestDefinitionRef.current(
          view.state.doc.toString(),
          view.state.selection.main.head,
        );
        return true;
      },
    },
    { key: 'Escape', run: () => { opts.callbacksRef.current.onEscape(); return true; } },
    {
      key: 'ArrowUp',
      run: (view) => {
        if (!selectionAtDocBoundary(view.state, 'start')) {
          return false;
        }
        return opts.onMoveToAdjacentCellRef.current?.('up') ?? false;
      },
    },
    {
      key: 'ArrowDown',
      run: (view) => {
        if (!selectionAtDocBoundary(view.state, 'end')) {
          return false;
        }
        return opts.onMoveToAdjacentCellRef.current?.('down') ?? false;
      },
    },
  ]);

  const completionSource = async (context: CompletionContext): Promise<CompletionResult | null> => {
    const before = context.matchBefore(IDENTIFIER_COMPLETION_PATTERN);
    const charBefore = context.pos > 0 ? context.state.sliceDoc(context.pos - 1, context.pos) : '';
    const typedText = before?.text ?? '';
    const shouldQuery = shouldRequestCompletion({
      explicit: context.explicit,
      typedText,
      triggerCharacter: charBefore === '.' ? '.' : undefined,
    });

    if (!shouldQuery) {
      return null;
    }

    const items = await opts.requestCompletionsRef.current(context.state.doc.toString(), context.pos, {
      explicit: context.explicit,
      triggerCharacter: charBefore === '.' ? '.' : undefined,
    });
    if (items.length === 0) {
      return null;
    }

    return {
      from: before?.from ?? context.pos,
      validFor: IDENTIFIER_COMPLETION_VALID_FOR,
      options: items.map((item) => ({
        label: item.label,
        type: item.kind,
        detail: item.detail,
        info: item.documentation,
        apply: item.apply,
      })),
    };
  };

  const updateListener = EditorView.updateListener.of((update: ViewUpdate) => {
    if (update.docChanged && !opts.externalUpdateRef.current) {
      opts.callbacksRef.current.onSourceChange(update.state.doc.toString());
    }
    if (update.selectionSet) {
      const { main } = update.state.selection;
      opts.callbacksRef.current.onSelectionChange?.({
        anchor: main.anchor,
        head: main.head,
      });
    }
    if (update.focusChanged) {
      if (update.view.hasFocus) opts.callbacksRef.current.onFocus();
      else opts.callbacksRef.current.onBlur(update.state.doc.toString());
    }
  });

  const definitionHandlers = EditorView.domEventHandlers({
    mousedown(event, view) {
      if (!isDefinitionModifierEvent(event) || event.button !== 0) {
        return false;
      }
      const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
      if (pos == null || !positionIsNavigable(view.state, pos)) {
        return false;
      }
      event.preventDefault();
      void opts.requestDefinitionRef.current(view.state.doc.toString(), pos);
      return true;
    },
    mousemove(event, view) {
      const pos = view.posAtCoords({ x: event.clientX, y: event.clientY });
      const shouldShowPointer = pos != null
        && isDefinitionModifierEvent(event)
        && positionIsNavigable(view.state, pos);
      view.contentDOM.style.cursor = shouldShowPointer ? 'pointer' : '';
      return false;
    },
    mouseleave(_event, view) {
      view.contentDOM.style.cursor = '';
      return false;
    },
  });

  // CSP nonce — required so CodeMirror's dynamic <style> tags aren't blocked
  const nonce = document.body.dataset.nonce ?? '';
  const tabKeymap = keymap.of([
    {
      key: 'Tab',
      run: (view) => {
        if (completionStatus(view.state) === 'active') {
          const accepted = acceptCompletion(view);
          if (accepted) {
            return true;
          }
        }
        if (!selectionStartsInLeadingWhitespace(view.state)) {
          return true;
        }
        return indentMore(view);
      },
    },
  ]);

  return [
    EditorView.cspNonce.of(nonce),
    Prec.highest(cellKeymap),
    Prec.high(tabKeymap),

    // Language
    python(),

    // Editing
    history(),
    bracketMatching(),
    closeBrackets(),
    indentOnInput(),
    EditorView.lineWrapping,
    EditorState.allowMultipleSelections.of(true),

    // Gutter — fold only, no line numbers (VS Code notebook style)
    foldGutter({
      markerDOM: (open: boolean) => {
        const el = document.createElement('span');
        el.style.cssText = 'display:inline-flex;align-items:center;justify-content:center;transition:transform 120ms;';
        el.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 11 3 6 3.7 5.3 8 9.6 12.3 5.3 13 6z"/></svg>';
        if (!open) el.style.transform = 'rotate(-90deg)';
        return el;
      },
    }),
    highlightActiveLine(),
    highlightActiveLineGutter(),

    // Selection & cursor
    drawSelection(),
    dropCursor(),
    rectangularSelection(),
    crosshairCursor(),
    highlightSelectionMatches(),

    // Autocomplete
    autocompletion({
      override: [completionSource],
      activateOnTypingDelay: DEFAULT_COMPLETION_TYPING_DELAY_MS,
      filterStrict: true,
      selectOnOpen: false,
    }),

    // Diagnostics are pushed in from the extension host.
    linter(null),

    // Indent guides
    indentationMarkers({
      colors: {
        light: 'rgba(0,0,0,0.10)',
        dark: 'rgba(255,255,255,0.10)',
        activeLight: 'rgba(0,0,0,0.18)',
        activeDark: 'rgba(255,255,255,0.18)',
      },
    }),

    // Keymaps
    keymap.of([
      ...closeBracketsKeymap,
      ...defaultKeymap,
      ...searchKeymap,
      ...historyKeymap,
      ...foldKeymap,
      ...completionKeymap,
    ]),

    // Appearance
    buildEditorTheme({
      fontFamily: '"IBM Plex Mono", "SF Mono", Monaco, Menlo, Consolas, monospace',
      fontSize: opts.fontSize,
      lineHeight: opts.lineHeight,
      topPadding: opts.topPadding,
      bottomPadding: opts.bottomPadding,
      dark: opts.dark,
    }),
    syntaxHighlighting(opts.dark ? cursorDarkHighlight : cursorLightHighlight),
    EditorView.editable.of(true),

    updateListener,
    definitionHandlers,
  ];
}

function isDefinitionModifierEvent(event: MouseEvent): boolean {
  const isMac = navigator.platform.toLowerCase().includes('mac');
  return isMac ? event.metaKey : event.ctrlKey;
}

function positionIsNavigable(state: EditorState, pos: number): boolean {
  return Boolean(state.wordAt(pos) ?? state.wordAt(Math.max(0, pos - 1)));
}

function selectionStartsInLeadingWhitespace(state: EditorState): boolean {
  return state.selection.ranges.every((range) => {
    const line = state.doc.lineAt(range.from);
    const linePrefix = line.text.slice(0, Math.max(0, range.from - line.from));
    return linePrefix.trim().length === 0;
  });
}

function selectionAtDocBoundary(state: EditorState, boundary: 'start' | 'end'): boolean {
  return state.selection.ranges.every((range) => {
    if (!range.empty) {
      return false;
    }
    return boundary === 'start' ? range.head === 0 : range.head === state.doc.length;
  });
}

// ── Component ────────────────────────────────────────────

export type CodeMirrorCellHandle = {
  focus: () => void;
  focusAtLineEnd: () => void;
  focusAtBoundary: (boundary: 'start' | 'end') => void;
  selectRange: (from: number, to?: number) => void;
  blur: () => void;
  getView: () => EditorView | null;
};

type CompletionOptionData = {
  label: string;
  kind?: string;
  detail?: string;
  documentation?: string;
  apply?: string;
  sortText?: string;
  filterText?: string;
};

type CompletionRequestOptions = {
  explicit?: boolean;
  triggerCharacter?: string;
};

type DefinitionRequestHandler = (source: string, offset: number) => void | Promise<void>;

export type CodeMirrorCellProps = {
  source: string;
  diagnostics: Diagnostic[];
  onSourceChange: (value: string) => void;
  requestCompletions: (
    source: string,
    offset: number,
    options?: CompletionRequestOptions,
  ) => Promise<CompletionOptionData[]>;
  requestDefinition: DefinitionRequestHandler;
  onFocus: () => void;
  onBlur: (value: string) => void;
  onSelectionChange?: (selection: { anchor: number; head: number }) => void;
  onRunCell: () => void;
  onEscape: () => void;
  fontSize: number;
  lineHeight: number;
  topPadding: number;
  bottomPadding: number;
  dark: boolean;
  bindHandle: (handle: CodeMirrorCellHandle | null) => void;
  onMoveToAdjacentCell?: (direction: 'up' | 'down') => boolean;
};

const defaultCallbacks = {
  onSourceChange: (_v: string) => {},
  onFocus: () => {},
  onBlur: (_v: string) => {},
  onSelectionChange: (_selection: { anchor: number; head: number }) => {},
  onRunCell: () => {},
  onEscape: () => {},
};

export function CodeMirrorCell({
  source,
  diagnostics,
  onSourceChange,
  requestCompletions,
  requestDefinition,
  onFocus,
  onBlur,
  onSelectionChange,
  onRunCell,
  onEscape,
  fontSize,
  lineHeight,
  topPadding,
  bottomPadding,
  dark,
  bindHandle,
  onMoveToAdjacentCell,
}: CodeMirrorCellProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const externalUpdateRef = useRef(false);
  const callbacksRef = useRef({
    onSourceChange,
    onFocus,
    onBlur,
    onSelectionChange: onSelectionChange ?? defaultCallbacks.onSelectionChange,
    onRunCell,
    onEscape,
  });
  callbacksRef.current = {
    onSourceChange,
    onFocus,
    onBlur,
    onSelectionChange: onSelectionChange ?? defaultCallbacks.onSelectionChange,
    onRunCell,
    onEscape,
  };
  const requestCompletionsRef = useRef(requestCompletions);
  requestCompletionsRef.current = requestCompletions;
  const requestDefinitionRef = useRef(requestDefinition);
  requestDefinitionRef.current = requestDefinition;
  const onMoveToAdjacentCellRef = useRef(onMoveToAdjacentCell);
  onMoveToAdjacentCellRef.current = onMoveToAdjacentCell;

  useEffect(() => {
    if (!containerRef.current) return;

    const view = new EditorView({
      state: EditorState.create({
        doc: source,
        extensions: buildExtensions({
          fontSize, lineHeight, topPadding, bottomPadding, dark,
          callbacksRef, externalUpdateRef, requestCompletionsRef, requestDefinitionRef, onMoveToAdjacentCellRef,
        }),
      }),
      parent: containerRef.current,
    });

    const focusAtBoundary = (boundary: 'start' | 'end') => {
      const position = boundary === 'start' ? 0 : view.state.doc.length;
      view.dispatch({
        selection: {
          anchor: position,
          head: position,
        },
      });
      view.focus();
    };

    viewRef.current = view;
    bindHandle({
      focus: () => view.focus(),
      focusAtLineEnd: () => {
        const { main } = view.state.selection;
        const line = view.state.doc.lineAt(main.head);
        view.dispatch({
          selection: {
            anchor: line.to,
            head: line.to,
          },
        });
        view.focus();
      },
      focusAtBoundary,
      selectRange: (from: number, to = from) => {
        const anchor = Math.max(0, Math.min(from, view.state.doc.length));
        const head = Math.max(0, Math.min(to, view.state.doc.length));
        view.dispatch({
          selection: {
            anchor,
            head,
          },
          scrollIntoView: true,
        });
        view.focus();
      },
      blur: () => {
        (view.contentDOM as HTMLElement | null)?.blur?.();
      },
      getView: () => view,
    });

    return () => {
      bindHandle(null);
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync external source changes
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    if (view.state.doc.toString() === source) return;

    externalUpdateRef.current = true;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: source },
    });
    externalUpdateRef.current = false;
  }, [source]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch(setDiagnostics(view.state, diagnostics));
  }, [diagnostics]);

  // Reconfigure on theme/font change
  const prevConfig = useRef({ dark, fontSize, lineHeight, topPadding, bottomPadding });
  useEffect(() => {
    const prev = prevConfig.current;
    const changed = prev.dark !== dark || prev.fontSize !== fontSize ||
      prev.lineHeight !== lineHeight || prev.topPadding !== topPadding ||
      prev.bottomPadding !== bottomPadding;
    prevConfig.current = { dark, fontSize, lineHeight, topPadding, bottomPadding };
    if (!changed) return;

    const view = viewRef.current;
    if (!view) return;

    view.setState(EditorState.create({
      doc: view.state.doc.toString(),
      selection: view.state.selection,
        extensions: buildExtensions({
          fontSize, lineHeight, topPadding, bottomPadding, dark,
          callbacksRef, externalUpdateRef, requestCompletionsRef, requestDefinitionRef, onMoveToAdjacentCellRef,
        }),
      }));
  }, [dark, fontSize, lineHeight, topPadding, bottomPadding]);

  return <div ref={containerRef} style={{ flex: 1, minWidth: 0 }} />;
}

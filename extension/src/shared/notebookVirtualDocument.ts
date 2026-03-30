/**
 * Shared notebook-to-virtual-document mapping.
 *
 * Combines notebook code cells into a single Python document for LSP
 * analysis, with bidirectional position mapping between the virtual
 * document and individual cell offsets. Both the VS Code extension and
 * standalone browser LSP should use these helpers.
 */

export type CellSnapshot = {
    index: number;
    cell_id: string;
    cell_type: 'code' | 'markdown' | 'raw';
    source: string;
};

export type VirtualCellSegment = {
    cell_id: string;
    index: number;
    contentFrom: number;
    contentTo: number;
    source: string;
    lineStarts: number[];
};

export type VirtualDocument = {
    text: string;
    lineStarts: number[];
    codeCells: VirtualCellSegment[];
    version: number;
};

export type CellDiagnostic = {
    from: number;
    to: number;
    severity: 'error' | 'warning' | 'info' | 'hint';
    message: string;
    source?: string;
};

export type DiagnosticsByCell = Record<string, CellDiagnostic[]>;

export function computeLineStarts(text: string): number[] {
    const starts = [0];
    for (let i = 0; i < text.length; i += 1) {
        if (text.charCodeAt(i) === 10) {
            starts.push(i + 1);
        }
    }
    return starts;
}

function clamp(value: number, min: number, max: number): number {
    return Math.max(min, Math.min(max, value));
}

export function offsetToPosition(
    lineStarts: number[],
    textLength: number,
    offset: number,
): { line: number; character: number } {
    const clampedOffset = clamp(offset, 0, textLength);
    let low = 0;
    let high = lineStarts.length - 1;
    while (low < high) {
        const mid = (low + high + 1) >> 1;
        if (lineStarts[mid] <= clampedOffset) {
            low = mid;
        } else {
            high = mid - 1;
        }
    }
    return { line: low, character: clampedOffset - lineStarts[low] };
}

export function positionToOffset(
    lineStarts: number[],
    textLength: number,
    position: { line: number; character: number },
): number {
    const line = clamp(position.line, 0, lineStarts.length - 1);
    const lineStart = lineStarts[line];
    const nextLineStart = line + 1 < lineStarts.length ? lineStarts[line + 1] : textLength;
    return clamp(lineStart + position.character, lineStart, nextLineStart);
}

/**
 * Build a virtual Python document from notebook code cells.
 * Each cell is prefixed with a `# %% [agent-repl cell ...]` header.
 */
export function buildVirtualDocument(
    cells: readonly CellSnapshot[],
    version: number,
): VirtualDocument {
    const parts: string[] = [];
    const codeCells: VirtualCellSegment[] = [];

    for (const cell of cells) {
        if (cell.cell_type !== 'code') {
            continue;
        }
        const header = `# %% [agent-repl cell ${cell.cell_id}]\n`;
        parts.push(header);
        const contentFrom = parts.join('').length;
        parts.push(cell.source);
        const contentTo = contentFrom + cell.source.length;
        parts.push('\n\n');

        codeCells.push({
            cell_id: cell.cell_id,
            index: cell.index,
            contentFrom,
            contentTo,
            source: cell.source,
            lineStarts: computeLineStarts(cell.source),
        });
    }

    const text = parts.join('');
    return {
        text,
        lineStarts: computeLineStarts(text),
        codeCells,
        version,
    };
}

/**
 * Map LSP diagnostics from virtual document positions back to individual cells.
 */
export function mapDiagnosticsToCells(
    virtualDocument: VirtualDocument,
    diagnostics: Array<{
        range: {
            start: { line: number; character: number };
            end: { line: number; character: number };
        };
        severity?: number;
        message: string;
        source?: string;
    }>,
): DiagnosticsByCell {
    const SEVERITY_MAP: Record<number, CellDiagnostic['severity']> = {
        1: 'error',
        2: 'warning',
        3: 'info',
        4: 'hint',
    };

    const result: DiagnosticsByCell = {};
    for (const cell of virtualDocument.codeCells) {
        result[cell.cell_id] = [];
    }

    for (const diag of diagnostics) {
        const startOffset = positionToOffset(
            virtualDocument.lineStarts,
            virtualDocument.text.length,
            diag.range.start,
        );
        const endOffset = positionToOffset(
            virtualDocument.lineStarts,
            virtualDocument.text.length,
            diag.range.end,
        );

        const segment = virtualDocument.codeCells.find((c) => (
            startOffset >= c.contentFrom && startOffset < c.contentTo
        ));
        if (!segment) {
            continue;
        }

        const cellFrom = startOffset - segment.contentFrom;
        const cellTo = Math.min(endOffset - segment.contentFrom, segment.source.length);

        if (!result[segment.cell_id]) {
            result[segment.cell_id] = [];
        }
        result[segment.cell_id].push({
            from: cellFrom,
            to: cellTo,
            severity: SEVERITY_MAP[diag.severity ?? 3] ?? 'info',
            message: diag.message,
            source: diag.source,
        });
    }

    return result;
}

/**
 * Convert a cell-relative offset to a virtual document offset for
 * LSP requests (e.g., completions).
 */
export function cellOffsetToVirtualOffset(
    virtualDocument: VirtualDocument,
    cellId: string,
    cellOffset: number,
): number | null {
    const segment = virtualDocument.codeCells.find((c) => c.cell_id === cellId);
    if (!segment) {
        return null;
    }
    return segment.contentFrom + clamp(cellOffset, 0, segment.source.length);
}

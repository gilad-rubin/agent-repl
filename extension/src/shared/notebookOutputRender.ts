export type NotebookOutputLike = {
    output_type: string;
    data?: Record<string, any> | null;
};

export type NotebookRichOutputKind =
    | 'html'
    | 'markdown'
    | 'svg'
    | 'image'
    | 'json'
    | 'text';

export type NotebookRichOutputRenderSpec = {
    kind: NotebookRichOutputKind;
    mime: string;
    value: string;
};

const RASTER_IMAGE_MIMES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'] as const;

export function normalizeNotebookMimeText(value: unknown): string {
    if (typeof value === 'string') {
        return value;
    }
    if (Array.isArray(value)) {
        return value.map((entry) => normalizeNotebookMimeText(entry)).join('');
    }
    if (value == null) {
        return '';
    }
    if (typeof value === 'object') {
        return safeJsonStringify(value);
    }
    return String(value);
}

export function stringifyNotebookJson(value: unknown): string {
    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) {
            return '';
        }
        try {
            return safeJsonStringify(JSON.parse(trimmed));
        } catch {
            return value;
        }
    }
    if (value == null) {
        return '';
    }
    return safeJsonStringify(value);
}

export function getNotebookRichOutputRenderSpec(
    output: NotebookOutputLike,
): NotebookRichOutputRenderSpec | null {
    if (
        output.output_type !== 'execute_result'
        && output.output_type !== 'display_data'
        && output.output_type !== 'update_display_data'
    ) {
        return null;
    }

    const data = output.data && typeof output.data === 'object'
        ? output.data
        : {};

    const html = normalizeNotebookMimeText(data['text/html']);
    if (html.trim()) {
        return { kind: 'html', mime: 'text/html', value: html };
    }

    const markdown = normalizeNotebookMimeText(data['text/markdown']);
    if (markdown.trim()) {
        return { kind: 'markdown', mime: 'text/markdown', value: markdown };
    }

    const svg = normalizeNotebookMimeText(data['image/svg+xml']);
    if (svg.trim()) {
        return { kind: 'svg', mime: 'image/svg+xml', value: svg };
    }

    for (const mime of RASTER_IMAGE_MIMES) {
        const image = normalizeNotebookMimeText(data[mime]).replace(/\s+/g, '');
        if (image) {
            return { kind: 'image', mime, value: image };
        }
    }

    for (const mime of findJsonMimeKeys(data)) {
        const json = stringifyNotebookJson(data[mime]);
        if (json.trim()) {
            return { kind: 'json', mime, value: json };
        }
    }

    const plainText = normalizeNotebookMimeText(data['text/plain']);
    if (plainText.length > 0) {
        return { kind: 'text', mime: 'text/plain', value: plainText };
    }

    for (const mime of Object.keys(data)) {
        if (mime === 'text/plain' || !mime.startsWith('text/')) {
            continue;
        }
        const text = normalizeNotebookMimeText(data[mime]);
        if (text.length > 0) {
            return { kind: 'text', mime, value: text };
        }
    }

    const fallbackMime = Object.keys(data)[0];
    if (!fallbackMime) {
        return null;
    }
    const fallback = normalizeNotebookMimeText(data[fallbackMime]);
    if (!fallback.length) {
        return null;
    }
    return { kind: 'text', mime: fallbackMime, value: fallback };
}

function findJsonMimeKeys(data: Record<string, any>): string[] {
    const keys = Object.keys(data);
    const preferred = keys.filter((mime) => mime === 'application/json' || mime.endsWith('+json'));
    const secondary = keys.filter((mime) => mime !== 'application/json' && mime.endsWith('/json'));
    return [...preferred, ...secondary];
}

function safeJsonStringify(value: unknown): string {
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}

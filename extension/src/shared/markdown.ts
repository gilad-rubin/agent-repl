export function normalizeMarkdownSource(source: unknown): string {
    if (typeof source === 'string') {
        return source;
    }
    if (Array.isArray(source)) {
        return source
            .map((part) => (typeof part === 'string' ? part : ''))
            .join('');
    }
    return '';
}

export const DEFAULT_COMPLETION_MIN_IDENTIFIER_CHARS = 2;
export const DEFAULT_COMPLETION_TYPING_DELAY_MS = 180;
export const IDENTIFIER_COMPLETION_PATTERN = /[A-Za-z_][A-Za-z0-9_]*$/;
export const IDENTIFIER_COMPLETION_VALID_FOR = /^[A-Za-z0-9_]*$/;

type CompletionTriggerDecision = {
    explicit?: boolean;
    typedText?: string;
    triggerCharacter?: string;
};

export function shouldRequestCompletion({
    explicit = false,
    typedText = '',
    triggerCharacter,
}: CompletionTriggerDecision): boolean {
    if (explicit) {
        return true;
    }
    if (triggerCharacter === '.') {
        return true;
    }

    return typedText.length >= DEFAULT_COMPLETION_MIN_IDENTIFIER_CHARS;
}

export type RecoveryAction = {
    kind: string;
    label: string;
};

export type RecoveryCommand = {
    label: string;
    value: string;
};

export type RecoveryAdvice = {
    reason: string;
    summary: string;
    suggestions?: string[];
    commands?: RecoveryCommand[];
    actions?: RecoveryAction[];
};

export function recoveryFromPayload(payload: any): RecoveryAdvice | undefined {
    if (!payload || typeof payload !== 'object' || !payload.recovery || typeof payload.recovery !== 'object') {
        return undefined;
    }
    const recovery = payload.recovery as RecoveryAdvice;
    if (typeof recovery.reason !== 'string' || typeof recovery.summary !== 'string') {
        return undefined;
    }
    return recovery;
}

export function stalePreviewServerRecovery(): RecoveryAdvice {
    return {
        reason: 'stale-preview-server',
        summary: 'This browser preview server is responding on the current port, but it is missing the notebook API routes required by the current canvas.',
        suggestions: [
            'Restart the browser preview server for this workspace, then refresh the page.',
            'If the current port is occupied by an older preview process, open the preview on a fresh port instead.',
        ],
        commands: [
            { label: 'Restart preview', value: 'cd extension && npm run preview:webview' },
            { label: 'Fresh preview port', value: 'uv run agent-repl browse --port 4176' },
        ],
    };
}

export function daemonUnavailableRecovery(): RecoveryAdvice {
    return {
        reason: 'daemon-unavailable',
        summary: 'The notebook surface could not reach a healthy Agent REPL backend for this workspace.',
        suggestions: [
            'Reload the bridge or browser preview, then retry the notebook action.',
            'If the problem persists, restart the workspace daemon before retrying.',
        ],
        commands: [
            { label: 'Reload bridge', value: 'uv run agent-repl reload --pretty' },
            { label: 'Check daemon', value: 'uv run agent-repl core status --pretty' },
        ],
        actions: [{ kind: 'refresh-notebook', label: 'Refresh notebook' }],
    };
}

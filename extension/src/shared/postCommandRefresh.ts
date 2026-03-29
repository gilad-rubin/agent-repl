/**
 * Expected post-command refresh behavior. Both the VS Code editor proxy
 * and the standalone browser host should converge on these policies so
 * notebook state stays consistent across surfaces after every command.
 */
export type PostCommandRefreshSpec = {
    loadContents: boolean;
    loadRuntime: boolean;
};

/**
 * Return the expected refresh behavior after a notebook command completes
 * successfully. The command string should match the canvas message types
 * (e.g. 'restart-and-run-all', 'execute-cell', 'interrupt-execution').
 *
 * For execute-cell, the refresh depends on the result status:
 * - 'ok': contents + runtime (synchronous execution completed)
 * - 'started'/'queued'/'error': runtime only (async execution in progress)
 */
export function postCommandRefreshSpec(
    command: string,
    executeStatus?: string,
): PostCommandRefreshSpec {
    switch (command) {
        case 'execute-cell':
            return executeStatus === 'ok'
                ? { loadContents: true, loadRuntime: true }
                : { loadContents: false, loadRuntime: true };
        case 'execute-all':
        case 'restart-and-run-all':
        case 'restart-kernel':
        case 'interrupt-execution':
            return { loadContents: true, loadRuntime: true };
        case 'select-kernel':
            return { loadContents: false, loadRuntime: true };
        case 'flush-draft':
        case 'save-notebook':
        case 'edit':
            return { loadContents: true, loadRuntime: false };
        default:
            return { loadContents: false, loadRuntime: false };
    }
}

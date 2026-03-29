export type NotebookCommandFlowOptions<T> = {
    run: () => Promise<T>;
    onSuccess: (result: T) => void | Promise<void>;
    onError: (error: unknown) => void | Promise<void>;
    onConflict?: (error: unknown) => boolean | Promise<boolean>;
};

export function runNotebookCommandFlow<T>(options: NotebookCommandFlowOptions<T>): void {
    void options.run()
        .then((result) => options.onSuccess(result))
        .catch(async (error) => {
            if (options.onConflict) {
                const handled = await options.onConflict(error);
                if (handled) {
                    return;
                }
            }
            await options.onError(error);
        });
}

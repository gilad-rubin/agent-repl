/**
 * Shared WebSocket client for daemon push-based sync.
 *
 * Used by both the extension host (proxy.ts via Node WebSocket)
 * and the browser host (standalone-host.ts via native WebSocket).
 *
 * Callers provide a `createSocket` factory so each environment can
 * supply its own WebSocket implementation.
 */

// -- Minimal WebSocket abstraction ------------------------------------------
// Both the DOM WebSocket and Node's undici/ws WebSocket share this shape.

export interface SocketLike {
    readonly readyState: number;
    send(data: string): void;
    close(code?: number, reason?: string): void;
    onopen: ((ev: any) => void) | null;
    onmessage: ((ev: { data: any }) => void) | null;
    onclose: ((ev: { code: number; reason: string }) => void) | null;
    onerror: ((ev: any) => void) | null;
}

export const SOCKET_OPEN = 1;

export type ConnectionState = 'disconnected' | 'connecting' | 'connected';

export interface DaemonWebSocketOptions {
    /** Base daemon URL, e.g. "http://127.0.0.1:12345". */
    daemonUrl: string;
    /** Token for fetching the WS nonce via HTTP. */
    daemonToken: string;
    /** Factory to create a WebSocket for a given URL string. */
    createSocket: (url: string) => SocketLike;
    /** Fetch implementation — allows Node callers to pass their own. */
    fetchFn: typeof fetch;
    /** Called on every inbound message (already JSON-parsed). */
    onMessage: (msg: any) => void;
    /** Called when connection is established and hello received. */
    onConnect: () => void;
    /** Called when connection drops. */
    onDisconnect: () => void;
    /** Called when the daemon instance ID changed (daemon restarted). */
    onInstanceChange: (newInstanceId: any) => void;
    /**
     * Called when the cursor is stale (too many events missed).
     * The caller should do a full state rehydrate.
     * If not provided, `onInstanceChange` is fired as a fallback.
     */
    onStaleCursor?: () => void;
}

// -- Backoff helpers --------------------------------------------------------

const BACKOFF_BASE_MS = 500;
const BACKOFF_MAX_MS = 30_000;

function backoffDelay(attempt: number): number {
    const exponential = Math.min(BACKOFF_BASE_MS * 2 ** attempt, BACKOFF_MAX_MS);
    const jitter = Math.random() * exponential * 0.3;
    return exponential + jitter;
}

// -- Main class -------------------------------------------------------------

export class DaemonWebSocket {
    private _state: ConnectionState = 'disconnected';
    private _socket: SocketLike | null = null;
    private _instanceId: any = null;
    private _lastCursor = 0;
    private _attempt = 0;
    private _reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private _closed = false;
    private _subscriptions = new Set<string>();
    /** Cells currently executing — used to emit synthetic failures on disconnect. */
    private _executingCells = new Map<string, { path: string; cellId: string }>();

    private readonly opts: DaemonWebSocketOptions;

    constructor(opts: DaemonWebSocketOptions) {
        this.opts = opts;
    }

    // -- Public API ---------------------------------------------------------

    get state(): ConnectionState {
        return this._state;
    }

    get lastCursor(): number {
        return this._lastCursor;
    }

    /** Initiate the connection. Reconnects automatically on drop. */
    connect(): void {
        if (this._closed) return;
        if (this._state !== 'disconnected') return;
        this._doConnect();
    }

    /**
     * Mark a cell as executing. If the connection drops while this cell
     * is still tracked, a synthetic failure event is emitted via onMessage.
     */
    trackExecutingCell(path: string, cellId: string): void {
        this._executingCells.set(cellId, { path, cellId });
    }

    /** Mark a cell as no longer executing (completed or failed normally). */
    untrackExecutingCell(cellId: string): void {
        this._executingCells.delete(cellId);
    }

    /** Subscribe to activity events for a notebook path. */
    subscribe(path: string): void {
        this._subscriptions.add(path);
        this._sendJson({ subscribe: true, path });
    }

    /** Unsubscribe from a notebook path. */
    unsubscribe(path: string): void {
        this._subscriptions.delete(path);
        this._sendJson({ unsubscribe: true, path });
    }

    /** Permanently close — no reconnect. */
    close(): void {
        this._closed = true;
        this._clearReconnect();
        if (this._socket) {
            try { this._socket.close(1000, 'client shutdown'); } catch { /* ignore */ }
            this._socket = null;
        }
        this._state = 'disconnected';
    }

    // -- Connection lifecycle -----------------------------------------------

    private async _doConnect(): Promise<void> {
        if (this._closed) return;
        this._state = 'connecting';

        let nonce: string;
        try {
            nonce = await this._fetchNonce();
        } catch {
            this._scheduleReconnect();
            return;
        }

        const wsBase = this.opts.daemonUrl.replace(/^http/, 'ws');
        const params = new URLSearchParams({ nonce });
        if (this._lastCursor > 0) {
            params.set('last_cursor', String(this._lastCursor));
        }
        const url = `${wsBase}/ws?${params}`;

        let socket: SocketLike;
        try {
            socket = this.opts.createSocket(url);
        } catch {
            this._scheduleReconnect();
            return;
        }
        this._socket = socket;

        socket.onopen = () => {
            // Wait for hello frame before marking connected.
        };

        socket.onmessage = (ev) => {
            let data: any;
            try {
                data = typeof ev.data === 'string' ? JSON.parse(ev.data) : ev.data;
            } catch {
                return;
            }
            this._handleMessage(data);
        };

        socket.onclose = () => {
            const wasConnected = this._state === 'connected';
            this._socket = null;
            this._state = 'disconnected';

            // Emit synthetic failure events for any in-flight cells.
            if (wasConnected && this._executingCells.size > 0) {
                for (const [cellId, info] of this._executingCells) {
                    this.opts.onMessage({
                        type: 'execution',
                        path: info.path,
                        cell_id: cellId,
                        status: 'failed',
                        error: 'Connection lost during execution',
                        synthetic: true,
                    });
                }
                this._executingCells.clear();
            }

            if (wasConnected) {
                this.opts.onDisconnect();
            }
            if (!this._closed) {
                this._scheduleReconnect();
            }
        };

        socket.onerror = () => {
            // onclose will fire after onerror — reconnect handled there.
        };
    }

    private _handleMessage(data: any): void {
        if (data.type === 'hello') {
            this._handleHello(data);
            return;
        }

        // Track cursor from every envelope that carries one.
        if (typeof data.cursor === 'number') {
            this._lastCursor = data.cursor;
        }

        this.opts.onMessage(data);
    }

    private _handleHello(hello: any): void {
        const newInstanceId = hello.instance;
        const instanceChanged = this._instanceId !== null
            && JSON.stringify(newInstanceId) !== JSON.stringify(this._instanceId);
        const stale = hello.stale === true;

        this._instanceId = newInstanceId;
        this._state = 'connected';
        this._attempt = 0;

        // Process replayed events.
        const replay: any[] = hello.replay ?? [];
        for (const event of replay) {
            if (typeof event.cursor === 'number') {
                this._lastCursor = event.cursor;
            }
            this.opts.onMessage(event);
        }

        // Re-subscribe after reconnect.
        for (const path of this._subscriptions) {
            this._sendJson({ subscribe: true, path });
        }

        // Signal callers that need full rehydrate.
        if (instanceChanged) {
            this.opts.onInstanceChange(newInstanceId);
        } else if (stale) {
            // Cursor evicted — too many events missed. Full rehydrate needed.
            if (this.opts.onStaleCursor) {
                this.opts.onStaleCursor();
            } else {
                // Fallback: treat like instance change so caller reloads.
                this.opts.onInstanceChange(newInstanceId);
            }
        }

        this.opts.onConnect();
    }

    // -- Transport helpers --------------------------------------------------

    private _sendJson(obj: any): void {
        if (this._socket && this._socket.readyState === SOCKET_OPEN) {
            try {
                this._socket.send(JSON.stringify(obj));
            } catch { /* connection may be closing */ }
        }
    }

    private async _fetchNonce(): Promise<string> {
        const url = `${this.opts.daemonUrl}/api/ws-nonce`;
        const resp = await this.opts.fetchFn(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                Authorization: `token ${this.opts.daemonToken}`,
            },
        });
        if (!resp.ok) {
            throw new Error(`Nonce fetch failed: ${resp.status}`);
        }
        const body = await resp.json() as { nonce?: string };
        if (typeof body.nonce !== 'string') {
            throw new Error('Invalid nonce response');
        }
        return body.nonce;
    }

    // -- Reconnect ----------------------------------------------------------

    private _scheduleReconnect(): void {
        if (this._closed || this._reconnectTimer) return;
        this._state = 'disconnected';
        const delay = backoffDelay(this._attempt);
        this._attempt++;
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            this._doConnect();
        }, delay);
    }

    private _clearReconnect(): void {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
    }
}

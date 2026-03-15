import * as http from 'http';
import * as vscode from 'vscode';

const MAX_BODY = 10 * 1024 * 1024;

export type RouteHandler = (body: any, query: URLSearchParams) => Promise<any>;
export type Routes = Record<string, RouteHandler>;

export class BridgeServer implements vscode.Disposable {
    private server: http.Server | undefined;
    private _port = 0;
    private token: string;
    private routes: Routes;

    constructor(token: string, routes: Routes) {
        this.token = token;
        this.routes = routes;
    }

    get port(): number { return this._port; }

    /** Dynamically add a route after construction. */
    addRoute(key: string, handler: RouteHandler): void {
        this.routes[key] = handler;
    }

    /** Replace all routes (used by hot-reload). */
    setRoutes(routes: Routes): void {
        this.routes = routes;
    }

    /** Get a route handler by key. */
    getRoute(key: string): RouteHandler | undefined {
        return this.routes[key];
    }

    async start(port = 0): Promise<number> {
        return new Promise((resolve, reject) => {
            this.server = http.createServer((req, res) => this.handle(req, res));
            this.server.listen(port, '127.0.0.1', () => {
                const addr = this.server!.address();
                if (addr && typeof addr === 'object') { this._port = addr.port; }
                resolve(this._port);
            });
            this.server.on('error', reject);
        });
    }

    stop(): void { this.server?.close(); this.server = undefined; this._port = 0; }
    dispose(): void { this.stop(); }

    private async handle(req: http.IncomingMessage, res: http.ServerResponse): Promise<void> {
        res.setHeader('Content-Type', 'application/json');

        if ((req.headers.authorization ?? '') !== `token ${this.token}`) {
            res.statusCode = 401;
            res.end(JSON.stringify({ error: 'Unauthorized' }));
            return;
        }

        const url = new URL(req.url ?? '/', `http://localhost:${this._port}`);
        const handler = this.routes[`${req.method} ${url.pathname}`];
        if (!handler) {
            res.statusCode = 404;
            res.end(JSON.stringify({ error: `No route: ${req.method} ${url.pathname}` }));
            return;
        }

        try {
            const body = req.method !== 'GET' ? await readBody(req) : undefined;
            const result = await handler(body, url.searchParams);
            res.statusCode = 200;
            res.end(JSON.stringify(result));
        } catch (err: any) {
            res.statusCode = err.statusCode ?? 500;
            res.end(JSON.stringify({ error: err.message ?? 'Internal error' }));
        }
    }
}

function readBody(req: http.IncomingMessage): Promise<any> {
    return new Promise((resolve, reject) => {
        const chunks: Buffer[] = [];
        let size = 0;
        req.on('data', (c: Buffer) => {
            size += c.length;
            if (size > MAX_BODY) { req.destroy(); reject(new Error('Body too large')); return; }
            chunks.push(c);
        });
        req.on('end', () => {
            try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : undefined); }
            catch { reject(new Error('Invalid JSON')); }
        });
        req.on('error', reject);
    });
}

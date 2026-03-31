const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const {
    DaemonWebSocket,
    SOCKET_OPEN,
} = require(path.resolve(__dirname, '../out/shared/wsClient.js'));

// -- Helpers: mock socket and fetch -----------------------------------------

function createMockSocket() {
    const socket = {
        readyState: SOCKET_OPEN,
        sent: [],
        onopen: null,
        onmessage: null,
        onclose: null,
        onerror: null,
        send(data) { socket.sent.push(JSON.parse(data)); },
        close() { socket.readyState = 3; },
        // Test helpers
        simulateOpen() { if (socket.onopen) socket.onopen({}); },
        simulateMessage(data) {
            if (socket.onmessage) socket.onmessage({ data: JSON.stringify(data) });
        },
        simulateClose(code = 1000, reason = '') {
            socket.readyState = 3;
            if (socket.onclose) socket.onclose({ code, reason });
        },
    };
    return socket;
}

function createMockFetch(nonce = 'test-nonce-123') {
    return async () => ({
        ok: true,
        status: 200,
        json: async () => ({ nonce }),
    });
}

// -- Tests ------------------------------------------------------------------

test('connect fetches nonce and opens socket with correct URL', async () => {
    let createdUrl = null;
    const socket = createMockSocket();

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok-abc',
        createSocket: (url) => { createdUrl = url; return socket; },
        fetchFn: createMockFetch('my-nonce'),
        onMessage: () => {},
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    // Let the async connect resolve.
    await new Promise((r) => setTimeout(r, 20));

    assert.ok(createdUrl, 'socket should be created');
    assert.ok(createdUrl.startsWith('ws://127.0.0.1:9999/ws?'), 'URL should use ws:// and /ws path');
    assert.ok(createdUrl.includes('nonce=my-nonce'), 'URL should contain nonce');
    ws.close();
});

test('hello frame sets connected state and calls onConnect', async () => {
    const socket = createMockSocket();
    let connected = false;

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => socket,
        fetchFn: createMockFetch(),
        onMessage: () => {},
        onConnect: () => { connected = true; },
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(ws.state, 'connecting');

    socket.simulateMessage({ type: 'hello', instance: { id: 'inst-1' }, replay: [] });
    assert.equal(ws.state, 'connected');
    assert.ok(connected, 'onConnect should be called');
    ws.close();
});

test('instance ID change triggers onInstanceChange', async () => {
    const sockets = [];
    let instanceChanged = false;
    let newId = null;

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => {
            const s = createMockSocket();
            sockets.push(s);
            return s;
        },
        fetchFn: createMockFetch(),
        onMessage: () => {},
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: (id) => { instanceChanged = true; newId = id; },
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));

    // First connection — sets instance ID.
    sockets[0].simulateMessage({ type: 'hello', instance: { id: 'inst-1' }, replay: [] });
    assert.equal(instanceChanged, false, 'first connect should not trigger instance change');

    // Simulate disconnect + reconnect with different instance.
    sockets[0].simulateClose();
    const deadline = Date.now() + 2000;
    while (sockets.length < 2 && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 50));
    }
    assert.ok(sockets.length >= 2, 'should have reconnected');
    sockets[sockets.length - 1].simulateMessage({ type: 'hello', instance: { id: 'inst-2' }, replay: [] });
    assert.ok(instanceChanged, 'instance change should trigger');
    assert.deepEqual(newId, { id: 'inst-2' });
    ws.close();
});

test('subscribe sends correct JSON message', async () => {
    const socket = createMockSocket();

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => socket,
        fetchFn: createMockFetch(),
        onMessage: () => {},
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));
    socket.simulateMessage({ type: 'hello', instance: { id: 'i' }, replay: [] });

    ws.subscribe('demo.ipynb');
    assert.ok(
        socket.sent.some((m) => m.subscribe === true && m.path === 'demo.ipynb'),
        'should send subscribe message',
    );

    ws.unsubscribe('demo.ipynb');
    assert.ok(
        socket.sent.some((m) => m.unsubscribe === true && m.path === 'demo.ipynb'),
        'should send unsubscribe message',
    );
    ws.close();
});

test('resubscribes on reconnect', async () => {
    const sockets = [];

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => {
            const s = createMockSocket();
            sockets.push(s);
            return s;
        },
        fetchFn: createMockFetch(),
        onMessage: () => {},
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));
    sockets[0].simulateMessage({ type: 'hello', instance: { id: 'i' }, replay: [] });

    ws.subscribe('test.ipynb');
    sockets[0].sent.length = 0; // clear sent

    // Disconnect and wait for reconnect (backoff ~500ms + jitter + async nonce fetch).
    sockets[0].simulateClose();
    // Poll until a new socket appears or timeout.
    const deadline = Date.now() + 2000;
    while (sockets.length < 2 && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, 50));
    }
    assert.ok(sockets.length >= 2, 'should have reconnected');

    const reconnected = sockets[sockets.length - 1];
    reconnected.simulateMessage({ type: 'hello', instance: { id: 'i' }, replay: [] });

    assert.ok(
        reconnected.sent.some((m) => m.subscribe === true && m.path === 'test.ipynb'),
        'should resubscribe after reconnect',
    );
    ws.close();
});

test('messages are dispatched through onMessage', async () => {
    const socket = createMockSocket();
    const received = [];

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => socket,
        fetchFn: createMockFetch(),
        onMessage: (msg) => { received.push(msg); },
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));
    socket.simulateMessage({ type: 'hello', instance: { id: 'i' }, replay: [] });

    socket.simulateMessage({ type: 'activity', cursor: 5, path: 'nb.ipynb' });
    socket.simulateMessage({ type: 'execution', cursor: 6, path: 'nb.ipynb' });

    assert.equal(received.length, 2);
    assert.equal(received[0].type, 'activity');
    assert.equal(received[0].cursor, 5);
    assert.equal(received[1].type, 'execution');
    assert.equal(ws.lastCursor, 6);
    ws.close();
});

test('replay events from hello are dispatched', async () => {
    const socket = createMockSocket();
    const received = [];

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => socket,
        fetchFn: createMockFetch(),
        onMessage: (msg) => { received.push(msg); },
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));
    socket.simulateMessage({
        type: 'hello',
        instance: { id: 'i' },
        replay: [
            { type: 'activity', cursor: 3, path: 'a.ipynb' },
            { type: 'activity', cursor: 4, path: 'a.ipynb' },
        ],
    });

    assert.equal(received.length, 2, 'replay events should be dispatched');
    assert.equal(ws.lastCursor, 4, 'cursor should track to last replayed event');
    ws.close();
});

test('close prevents reconnect', async () => {
    const sockets = [];

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => {
            const s = createMockSocket();
            sockets.push(s);
            return s;
        },
        fetchFn: createMockFetch(),
        onMessage: () => {},
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    await new Promise((r) => setTimeout(r, 20));
    sockets[0].simulateMessage({ type: 'hello', instance: { id: 'i' }, replay: [] });

    ws.close();
    assert.equal(ws.state, 'disconnected');

    // Wait longer than backoff to verify no reconnect.
    await new Promise((r) => setTimeout(r, 700));
    assert.equal(sockets.length, 1, 'should not create new sockets after close');
});

test('nonce fetch failure triggers reconnect', async () => {
    let fetchCount = 0;
    const sockets = [];

    const ws = new DaemonWebSocket({
        daemonUrl: 'http://127.0.0.1:9999',
        daemonToken: 'tok',
        createSocket: () => {
            const s = createMockSocket();
            sockets.push(s);
            return s;
        },
        fetchFn: async () => {
            fetchCount++;
            if (fetchCount === 1) {
                return { ok: false, status: 500, json: async () => ({}) };
            }
            return { ok: true, status: 200, json: async () => ({ nonce: 'n' }) };
        },
        onMessage: () => {},
        onConnect: () => {},
        onDisconnect: () => {},
        onInstanceChange: () => {},
    });

    ws.connect();
    // First attempt fails nonce fetch.
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(sockets.length, 0, 'no socket created on nonce failure');

    // Wait for backoff retry.
    await new Promise((r) => setTimeout(r, 700));
    assert.ok(fetchCount >= 2, 'should retry nonce fetch');
    assert.equal(sockets.length, 1, 'socket created on successful retry');
    ws.close();
});

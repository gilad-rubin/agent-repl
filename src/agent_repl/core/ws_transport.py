"""WebSocket transport manager for push-based client sync."""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket, WebSocketDisconnect


# Nonce lifetime: 30 seconds, single-use.
_NONCE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class _Nonce:
    value: str
    created_at: float


@dataclass
class _Connection:
    ws: WebSocket
    subscriptions: set[str] = field(default_factory=set)
    last_cursor: int = 0


class WebSocketTransport:
    """Manages WebSocket connections, subscriptions, and event push."""

    def __init__(self, *, instance_id: dict[str, Any]) -> None:
        # instance_id is sent in the hello frame so clients can detect daemon restarts
        self._instance_id = instance_id
        # nonce_value -> _Nonce
        self._nonces: dict[str, _Nonce] = {}
        # monotonic event cursor
        self._cursor: int = 0
        # cursor -> event payload (bounded ring)
        self._event_log: dict[int, dict[str, Any]] = {}
        self._max_event_log = 500
        # active connections keyed by id(ws)
        self._connections: dict[int, _Connection] = {}

    # ------------------------------------------------------------------
    # Nonce management
    # ------------------------------------------------------------------

    def create_nonce(self) -> str:
        """Create a short-lived single-use nonce for WS upgrade auth."""
        self._purge_expired_nonces()
        nonce = secrets.token_urlsafe(32)
        self._nonces[nonce] = _Nonce(value=nonce, created_at=time.time())
        return nonce

    def redeem_nonce(self, nonce: str) -> bool:
        """Validate and consume a nonce. Returns True if valid."""
        self._purge_expired_nonces()
        record = self._nonces.pop(nonce, None)
        if record is None:
            return False
        if time.time() - record.created_at > _NONCE_TTL_SECONDS:
            return False
        return True

    def _purge_expired_nonces(self) -> None:
        now = time.time()
        expired = [k for k, v in self._nonces.items() if now - v.created_at > _NONCE_TTL_SECONDS]
        for k in expired:
            del self._nonces[k]

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def accept(self, ws: WebSocket, *, last_cursor: int | None = None) -> None:
        """Accept a WebSocket connection and send the hello frame."""
        await ws.accept()
        conn = _Connection(ws=ws, last_cursor=last_cursor or 0)
        self._connections[id(ws)] = conn
        await self._send_hello(conn)

    async def _send_hello(self, conn: _Connection) -> None:
        """Send hello frame, replaying missed events if cursor is still valid."""
        hello: dict[str, Any] = {
            "type": "hello",
            "instance": self._instance_id,
        }
        # If client sent a cursor and it's from this instance, replay missed events
        if conn.last_cursor > 0 and self._cursor_valid(conn.last_cursor):
            missed = self._events_since(conn.last_cursor)
            hello["replay"] = missed
            if missed:
                conn.last_cursor = missed[-1]["cursor"]
        else:
            # Fresh connection or stale cursor — no replay
            hello["replay"] = []
        await conn.ws.send_json(hello)

    def _cursor_valid(self, cursor: int) -> bool:
        if not self._event_log:
            return cursor == 0 or cursor == self._cursor
        min_cursor = min(self._event_log)
        return cursor >= min_cursor - 1

    def _events_since(self, cursor: int) -> list[dict[str, Any]]:
        return [
            self._event_log[c]
            for c in sorted(self._event_log)
            if c > cursor
        ]

    def disconnect(self, ws: WebSocket) -> None:
        """Remove a connection and its subscriptions."""
        self._connections.pop(id(ws), None)

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe(self, ws: WebSocket, path: str) -> None:
        conn = self._connections.get(id(ws))
        if conn:
            conn.subscriptions.add(path)

    def unsubscribe(self, ws: WebSocket, path: str) -> None:
        conn = self._connections.get(id(ws))
        if conn:
            conn.subscriptions.discard(path)

    # ------------------------------------------------------------------
    # Client message handling
    # ------------------------------------------------------------------

    async def handle_client_message(self, ws: WebSocket, data: dict[str, Any]) -> None:
        """Dispatch an inbound client message."""
        action = data.get("subscribe") or data.get("unsubscribe")
        if data.get("subscribe"):
            self.subscribe(ws, data["path"])
        elif data.get("unsubscribe"):
            self.unsubscribe(ws, data["path"])

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    def _next_cursor(self) -> int:
        self._cursor += 1
        # Trim event log if too large
        if len(self._event_log) >= self._max_event_log:
            oldest = min(self._event_log)
            del self._event_log[oldest]
        return self._cursor

    async def broadcast_activity(self, event_payload: dict[str, Any]) -> None:
        """Push an activity event to all connections subscribed to the event's path."""
        cursor = self._next_cursor()
        envelope = {
            "type": "activity",
            "cursor": cursor,
            **event_payload,
        }
        self._event_log[cursor] = envelope
        path = event_payload.get("path", "")
        await self._send_to_subscribers(path, envelope)

    async def broadcast_execution(self, event_payload: dict[str, Any]) -> None:
        """Push an execution event to subscribers."""
        cursor = self._next_cursor()
        envelope = {
            "type": "execution",
            "cursor": cursor,
            **event_payload,
        }
        self._event_log[cursor] = envelope
        path = event_payload.get("path", "")
        await self._send_to_subscribers(path, envelope)

    async def broadcast_presence(self, event_payload: dict[str, Any]) -> None:
        """Push a presence event to subscribers."""
        cursor = self._next_cursor()
        envelope = {
            "type": "presence",
            "cursor": cursor,
            **event_payload,
        }
        self._event_log[cursor] = envelope
        path = event_payload.get("path", "")
        await self._send_to_subscribers(path, envelope)

    async def _send_to_subscribers(self, path: str, envelope: dict[str, Any]) -> None:
        dead: list[int] = []
        for conn_id, conn in self._connections.items():
            if path in conn.subscriptions:
                try:
                    await conn.ws.send_json(envelope)
                    conn.last_cursor = envelope["cursor"]
                except Exception:
                    dead.append(conn_id)
        for conn_id in dead:
            self._connections.pop(conn_id, None)

    # ------------------------------------------------------------------
    # Sync bridge — schedule broadcast from non-async callers
    # ------------------------------------------------------------------

    def fire_activity(self, event_payload: dict[str, Any]) -> None:
        """Schedule an activity broadcast from sync code (fire-and-forget)."""
        self._fire(self.broadcast_activity(event_payload))

    def fire_execution(self, event_payload: dict[str, Any]) -> None:
        """Schedule an execution broadcast from sync code (fire-and-forget)."""
        self._fire(self.broadcast_execution(event_payload))

    def fire_presence(self, event_payload: dict[str, Any]) -> None:
        """Schedule a presence broadcast from sync code (fire-and-forget)."""
        self._fire(self.broadcast_presence(event_payload))

    def _fire(self, coro: Any) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(coro)
        except RuntimeError:
            # No running loop — silently discard (e.g. in tests or sync-only contexts)
            coro.close()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    @property
    def current_cursor(self) -> int:
        return self._cursor

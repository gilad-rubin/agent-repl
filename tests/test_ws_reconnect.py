"""Tests for WebSocket reconnect and recovery hardening."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from agent_repl.core.ws_transport import WebSocketTransport


def _make_transport(**kwargs):
    return WebSocketTransport(
        instance_id={"pid": 100, "started_at": 1000.0},
        **kwargs,
    )


def _mock_ws():
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.receive_json = AsyncMock()
    return ws


class _AsyncTestCase(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)


class TestSameInstanceReconnectReplay(_AsyncTestCase):
    """Verify that a client reconnecting to the same daemon gets missed events."""

    def test_reconnect_replays_missed_events(self):
        t = _make_transport()

        # Client 1 connects and subscribes
        ws1 = _mock_ws()
        self.run_async(t.accept(ws1))
        t.subscribe(ws1, "nb.ipynb")

        # Broadcast 3 events
        for i in range(3):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )
        cursor_after_all = t.current_cursor

        # Client disconnects after receiving ev0 (cursor 1)
        cursor_after_ev0 = 1

        # Simulate reconnect with cursor from after ev0
        ws2 = _mock_ws()
        self.run_async(t.accept(ws2, last_cursor=cursor_after_ev0))
        hello = ws2.send_json.call_args[0][0]

        self.assertEqual(hello["type"], "hello")
        self.assertFalse(hello["stale"])
        # Should replay ev1 and ev2 (cursors 2 and 3)
        self.assertEqual(len(hello["replay"]), 2)
        self.assertEqual(hello["replay"][0]["event_type"], "ev1")
        self.assertEqual(hello["replay"][1]["event_type"], "ev2")

    def test_reconnect_with_current_cursor_replays_nothing(self):
        t = _make_transport()
        ws1 = _mock_ws()
        self.run_async(t.accept(ws1))
        t.subscribe(ws1, "nb.ipynb")

        self.run_async(
            t.broadcast_activity({"path": "nb.ipynb", "event_type": "ev0"})
        )
        current = t.current_cursor

        # Reconnect with the latest cursor — nothing to replay
        ws2 = _mock_ws()
        self.run_async(t.accept(ws2, last_cursor=current))
        hello = ws2.send_json.call_args[0][0]
        self.assertFalse(hello["stale"])
        self.assertEqual(hello["replay"], [])

    def test_fresh_connection_no_replay_not_stale(self):
        t = _make_transport()
        # Broadcast some events before client connects
        self.run_async(
            t.broadcast_activity({"path": "nb.ipynb", "event_type": "ev0"})
        )

        ws = _mock_ws()
        self.run_async(t.accept(ws))  # no last_cursor
        hello = ws.send_json.call_args[0][0]
        self.assertFalse(hello["stale"])
        self.assertEqual(hello["replay"], [])


class TestNewInstanceDetection(_AsyncTestCase):
    """Verify that clients detect daemon restart via instance ID change."""

    def test_different_instance_id_in_hello(self):
        # Instance A
        t_a = WebSocketTransport(instance_id={"pid": 100, "started_at": 1000.0})
        self.run_async(
            t_a.broadcast_activity({"path": "nb.ipynb", "event_type": "old"})
        )
        cursor_from_a = t_a.current_cursor

        # Instance B (daemon restarted)
        t_b = WebSocketTransport(instance_id={"pid": 200, "started_at": 2000.0})

        ws = _mock_ws()
        self.run_async(t_b.accept(ws, last_cursor=cursor_from_a))
        hello = ws.send_json.call_args[0][0]

        # Different instance — client should detect the change
        self.assertEqual(hello["instance"]["pid"], 200)
        self.assertEqual(hello["instance"]["started_at"], 2000.0)
        # Cursor from instance A doesn't exist in B — empty replay
        self.assertEqual(hello["replay"], [])

    def test_same_instance_preserves_id(self):
        t = _make_transport()
        ws1 = _mock_ws()
        self.run_async(t.accept(ws1))
        hello1 = ws1.send_json.call_args[0][0]

        ws2 = _mock_ws()
        self.run_async(t.accept(ws2))
        hello2 = ws2.send_json.call_args[0][0]

        self.assertEqual(hello1["instance"], hello2["instance"])


class TestStaleCursorHandling(_AsyncTestCase):
    """Verify stale cursor detection when event log has been exhausted."""

    def test_stale_cursor_marked_in_hello(self):
        t = _make_transport()
        t._max_event_log = 3  # small buffer for testing

        # Fill and overflow the event log
        for i in range(10):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )
        # Cursor 1 has been evicted (only cursors 8, 9, 10 remain)

        ws = _mock_ws()
        self.run_async(t.accept(ws, last_cursor=1))
        hello = ws.send_json.call_args[0][0]

        self.assertTrue(hello["stale"])
        self.assertEqual(hello["replay"], [])

    def test_valid_cursor_not_marked_stale(self):
        t = _make_transport()
        t._max_event_log = 3

        for i in range(3):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )
        # Cursor 1 is still in the log (cursors 1, 2, 3)

        ws = _mock_ws()
        self.run_async(t.accept(ws, last_cursor=1))
        hello = ws.send_json.call_args[0][0]

        self.assertFalse(hello["stale"])
        # Should replay events after cursor 1
        self.assertEqual(len(hello["replay"]), 2)

    def test_zero_cursor_is_never_stale(self):
        t = _make_transport()
        t._max_event_log = 2

        for i in range(5):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )

        ws = _mock_ws()
        self.run_async(t.accept(ws, last_cursor=0))
        hello = ws.send_json.call_args[0][0]

        # Cursor 0 = fresh connection, not stale
        self.assertFalse(hello["stale"])
        self.assertEqual(hello["replay"], [])


class TestEventLogBounds(_AsyncTestCase):
    """Verify the bounded event log evicts old entries correctly."""

    def test_event_log_respects_max_size(self):
        t = _make_transport()
        t._max_event_log = 5

        for i in range(20):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )

        self.assertLessEqual(len(t._event_log), 5)
        # Newest cursors should be present
        self.assertIn(t.current_cursor, t._event_log)

    def test_oldest_events_evicted_first(self):
        t = _make_transport()
        t._max_event_log = 3

        for i in range(6):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )

        # Only last 3 cursors should remain: 4, 5, 6
        self.assertEqual(sorted(t._event_log.keys()), [4, 5, 6])

    def test_replay_only_returns_events_after_cursor(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        t.subscribe(ws, "nb.ipynb")

        for i in range(5):
            self.run_async(
                t.broadcast_activity({"path": "nb.ipynb", "event_type": f"ev{i}"})
            )

        # Reconnect with cursor 3 — should replay cursors 4 and 5
        ws2 = _mock_ws()
        self.run_async(t.accept(ws2, last_cursor=3))
        hello = ws2.send_json.call_args[0][0]
        self.assertEqual(len(hello["replay"]), 2)
        self.assertEqual(hello["replay"][0]["cursor"], 4)
        self.assertEqual(hello["replay"][1]["cursor"], 5)


class TestReconnectWithSubscriptions(_AsyncTestCase):
    """Verify subscriptions are independent of reconnect."""

    def test_new_connection_has_no_subscriptions(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws, last_cursor=0))

        # Broadcast without subscription — should not receive
        self.run_async(
            t.broadcast_activity({"path": "nb.ipynb", "event_type": "test"})
        )
        # Only hello sent
        self.assertEqual(ws.send_json.await_count, 1)

    def test_subscriptions_survive_on_transport_side(self):
        """Transport-side subscriptions are per-connection, not per-cursor.
        Client must re-subscribe after reconnect (handled by wsClient.ts)."""
        t = _make_transport()

        ws1 = _mock_ws()
        self.run_async(t.accept(ws1))
        t.subscribe(ws1, "nb.ipynb")

        # ws1 disconnects
        t.disconnect(ws1)

        # ws2 reconnects — no subscriptions carried over
        ws2 = _mock_ws()
        self.run_async(t.accept(ws2, last_cursor=0))

        self.run_async(
            t.broadcast_activity({"path": "nb.ipynb", "event_type": "test"})
        )
        # ws2 only got hello, not the event
        self.assertEqual(ws2.send_json.await_count, 1)


if __name__ == "__main__":
    unittest.main()

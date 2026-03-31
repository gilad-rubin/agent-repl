"""Tests for WebSocket transport manager."""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agent_repl.core.ws_transport import WebSocketTransport, _NONCE_TTL_SECONDS


def _make_transport(**kwargs):
    return WebSocketTransport(
        instance_id={"pid": 123, "started_at": 1000.0},
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
    """Base class that provides a fresh event loop per test."""

    def setUp(self):
        self.loop = asyncio.new_event_loop()

    def tearDown(self):
        self.loop.close()

    def run_async(self, coro):
        return self.loop.run_until_complete(coro)


class TestNonceAuth(unittest.TestCase):
    def test_create_and_redeem_nonce(self):
        t = _make_transport()
        nonce = t.create_nonce()
        self.assertTrue(t.redeem_nonce(nonce))

    def test_nonce_single_use(self):
        t = _make_transport()
        nonce = t.create_nonce()
        self.assertTrue(t.redeem_nonce(nonce))
        self.assertFalse(t.redeem_nonce(nonce))

    def test_unknown_nonce_rejected(self):
        t = _make_transport()
        self.assertFalse(t.redeem_nonce("bogus"))

    def test_expired_nonce_rejected(self):
        t = _make_transport()
        nonce = t.create_nonce()
        with patch("agent_repl.core.ws_transport.time") as mock_time:
            mock_time.time.return_value = time.time() + _NONCE_TTL_SECONDS + 1
            self.assertFalse(t.redeem_nonce(nonce))


class TestConnectionLifecycle(_AsyncTestCase):
    def test_accept_and_hello(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        ws.accept.assert_awaited_once()
        ws.send_json.assert_awaited_once()
        hello = ws.send_json.call_args[0][0]
        self.assertEqual(hello["type"], "hello")
        self.assertEqual(hello["instance"]["pid"], 123)
        self.assertEqual(hello["replay"], [])
        self.assertEqual(t.connection_count, 1)

    def test_disconnect_removes_connection(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        self.assertEqual(t.connection_count, 1)
        t.disconnect(ws)
        self.assertEqual(t.connection_count, 0)

    def test_disconnect_unknown_ws_is_noop(self):
        t = _make_transport()
        t.disconnect(_mock_ws())  # should not raise


class TestSubscriptions(_AsyncTestCase):
    def test_subscribe_and_receive_activity(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        t.subscribe(ws, "demo.ipynb")
        self.run_async(
            t.broadcast_activity({"path": "demo.ipynb", "event_type": "cell-executed"})
        )
        # hello + activity
        self.assertEqual(ws.send_json.await_count, 2)
        envelope = ws.send_json.call_args_list[1][0][0]
        self.assertEqual(envelope["type"], "activity")
        self.assertEqual(envelope["event_type"], "cell-executed")
        self.assertIn("cursor", envelope)

    def test_unsubscribed_client_gets_nothing(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        # not subscribed to anything
        self.run_async(
            t.broadcast_activity({"path": "demo.ipynb", "event_type": "test"})
        )
        # only hello
        self.assertEqual(ws.send_json.await_count, 1)

    def test_subscription_filtering(self):
        t = _make_transport()
        ws_a = _mock_ws()
        ws_b = _mock_ws()
        self.run_async(t.accept(ws_a))
        self.run_async(t.accept(ws_b))
        t.subscribe(ws_a, "a.ipynb")
        t.subscribe(ws_b, "b.ipynb")
        self.run_async(
            t.broadcast_activity({"path": "a.ipynb", "event_type": "test"})
        )
        # ws_a: hello + event = 2 calls; ws_b: hello only = 1 call
        self.assertEqual(ws_a.send_json.await_count, 2)
        self.assertEqual(ws_b.send_json.await_count, 1)

    def test_unsubscribe_stops_events(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        t.subscribe(ws, "demo.ipynb")
        t.unsubscribe(ws, "demo.ipynb")
        self.run_async(
            t.broadcast_activity({"path": "demo.ipynb", "event_type": "test"})
        )
        # only hello
        self.assertEqual(ws.send_json.await_count, 1)


class TestCursorReplay(_AsyncTestCase):
    def test_replay_missed_events_on_reconnect(self):
        t = _make_transport()

        # First connection receives an event
        ws1 = _mock_ws()
        self.run_async(t.accept(ws1))
        t.subscribe(ws1, "demo.ipynb")
        self.run_async(
            t.broadcast_activity({"path": "demo.ipynb", "event_type": "ev1"})
        )
        cursor_after_ev1 = t.current_cursor

        # Broadcast another event
        self.run_async(
            t.broadcast_activity({"path": "demo.ipynb", "event_type": "ev2"})
        )

        # New connection with last_cursor from before ev2
        ws2 = _mock_ws()
        self.run_async(t.accept(ws2, last_cursor=cursor_after_ev1))
        hello = ws2.send_json.call_args[0][0]
        self.assertEqual(hello["type"], "hello")
        # Should replay ev2 (cursor 2)
        self.assertEqual(len(hello["replay"]), 1)
        self.assertEqual(hello["replay"][0]["event_type"], "ev2")

    def test_stale_cursor_gets_empty_replay(self):
        t = _make_transport()
        t._max_event_log = 2

        # Fill the event log past capacity to evict old cursors
        for i in range(5):
            self.run_async(
                t.broadcast_activity({"path": "x.ipynb", "event_type": f"ev{i}"})
            )

        ws = _mock_ws()
        self.run_async(t.accept(ws, last_cursor=1))
        hello = ws.send_json.call_args[0][0]
        # Cursor 1 has been evicted — no replay
        self.assertEqual(hello["replay"], [])


class TestInstanceIdChange(_AsyncTestCase):
    def test_different_instance_sends_fresh_hello(self):
        t = _make_transport()

        # Broadcast an event to create cursor 1
        self.run_async(
            t.broadcast_activity({"path": "x.ipynb", "event_type": "old"})
        )

        # Simulate daemon restart with a new transport (different instance)
        t2 = WebSocketTransport(
            instance_id={"pid": 999, "started_at": 2000.0},
        )
        ws = _mock_ws()
        self.run_async(t2.accept(ws, last_cursor=1))
        hello = ws.send_json.call_args[0][0]
        self.assertEqual(hello["instance"]["pid"], 999)
        # Cursor 1 doesn't exist in the new instance — empty replay
        self.assertEqual(hello["replay"], [])


class TestExecutionAndPresenceBroadcast(_AsyncTestCase):
    def test_execution_event_broadcast(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        t.subscribe(ws, "nb.ipynb")
        self.run_async(
            t.broadcast_execution({"path": "nb.ipynb", "status": "started"})
        )
        envelope = ws.send_json.call_args[0][0]
        self.assertEqual(envelope["type"], "execution")
        self.assertEqual(envelope["status"], "started")

    def test_presence_event_broadcast(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        t.subscribe(ws, "nb.ipynb")
        self.run_async(
            t.broadcast_presence({"path": "nb.ipynb", "user": "alice"})
        )
        envelope = ws.send_json.call_args[0][0]
        self.assertEqual(envelope["type"], "presence")
        self.assertEqual(envelope["user"], "alice")


class TestClientMessages(_AsyncTestCase):
    def test_subscribe_via_message(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        self.run_async(
            t.handle_client_message(ws, {"subscribe": "notebook", "path": "x.ipynb"})
        )
        # Now broadcast — should reach this client
        self.run_async(
            t.broadcast_activity({"path": "x.ipynb", "event_type": "test"})
        )
        # hello + activity
        self.assertEqual(ws.send_json.await_count, 2)

    def test_unsubscribe_via_message(self):
        t = _make_transport()
        ws = _mock_ws()
        self.run_async(t.accept(ws))
        t.subscribe(ws, "x.ipynb")
        self.run_async(
            t.handle_client_message(ws, {"unsubscribe": "notebook", "path": "x.ipynb"})
        )
        self.run_async(
            t.broadcast_activity({"path": "x.ipynb", "event_type": "test"})
        )
        # hello only
        self.assertEqual(ws.send_json.await_count, 1)


class TestDeadConnectionCleanup(_AsyncTestCase):
    def test_dead_connection_removed_on_broadcast(self):
        t = _make_transport()
        ws = _mock_ws()
        ws.send_json = AsyncMock(side_effect=[None, Exception("dead")])
        self.run_async(t.accept(ws))
        t.subscribe(ws, "x.ipynb")
        self.assertEqual(t.connection_count, 1)
        self.run_async(
            t.broadcast_activity({"path": "x.ipynb", "event_type": "boom"})
        )
        self.assertEqual(t.connection_count, 0)


if __name__ == "__main__":
    unittest.main()

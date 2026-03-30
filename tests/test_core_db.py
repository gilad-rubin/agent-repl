"""Tests for SQLite operational persistence."""
from __future__ import annotations

import os
import tempfile
import unittest

from agent_repl.core.db import (
    DB_FILENAME,
    load_all,
    open_db,
    persist_all,
)


class TestOpenDb(unittest.TestCase):
    def test_creates_database_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            db_path = os.path.join(tmpdir, ".agent-repl", DB_FILENAME)
            self.assertTrue(os.path.exists(db_path))
            conn.close()

    def test_creates_gitignore_entry_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            gitignore_path = os.path.join(tmpdir, ".gitignore")
            with open(gitignore_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), ".agent-repl/\n")
            conn.close()

    def test_appends_gitignore_entry_without_clobbering_existing_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gitignore_path = os.path.join(tmpdir, ".gitignore")
            with open(gitignore_path, "w", encoding="utf-8") as handle:
                handle.write(".venv/\n__pycache__/")

            conn = open_db(tmpdir)

            with open(gitignore_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), ".venv/\n__pycache__/\n.agent-repl/\n")
            conn.close()

    def test_existing_gitignore_entry_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            gitignore_path = os.path.join(tmpdir, ".gitignore")
            with open(gitignore_path, "w", encoding="utf-8") as handle:
                handle.write(".venv/\n/.agent-repl/\n")

            conn = open_db(tmpdir)

            with open(gitignore_path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), ".venv/\n/.agent-repl/\n")
            conn.close()

    def test_wal_mode_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode, "wal")
            conn.close()

    def test_expected_tables_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertTrue({
                "sessions",
                "documents",
                "branches",
                "runtimes",
                "runs",
                "activity",
                "executions",
            }.issubset(tables))
            conn.close()

    def test_reopen_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn1 = open_db(tmpdir)
            conn1.close()
            conn2 = open_db(tmpdir)
            tables = {
                row[0]
                for row in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            self.assertIn("executions", tables)
            conn2.close()


class TestPersistAndLoad(unittest.TestCase):
    def test_round_trip_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            persist_all(conn, sessions=[{
                "session_id": "s1",
                "actor": "human",
                "client": "cli",
                "label": None,
                "status": "attached",
                "capabilities": ["edit", "execute"],
                "resume_count": 0,
                "created_at": 1.0,
                "last_seen_at": 2.0,
            }], documents=[], branches=[], runtimes=[], runs=[], activity=[])

            data = load_all(conn)
            self.assertEqual(len(data["sessions"]), 1)
            s = data["sessions"][0]
            self.assertEqual(s["session_id"], "s1")
            self.assertEqual(s["capabilities"], ["edit", "execute"])
            conn.close()

    def test_round_trip_documents(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            persist_all(conn, sessions=[], documents=[{
                "document_id": "d1",
                "path": "/a/b.ipynb",
                "relative_path": "b.ipynb",
                "file_format": "notebook",
                "sync_state": "in-sync",
                "bound_snapshot": {"hash": "abc"},
                "observed_snapshot": None,
                "created_at": 1.0,
                "updated_at": 2.0,
            }], branches=[], runtimes=[], runs=[], activity=[])

            data = load_all(conn)
            self.assertEqual(len(data["documents"]), 1)
            d = data["documents"][0]
            self.assertEqual(d["bound_snapshot"], {"hash": "abc"})
            self.assertIsNone(d["observed_snapshot"])
            conn.close()

    def test_round_trip_activity_with_data(self):
        import time as _time
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            persist_all(conn, sessions=[], documents=[], branches=[],
                        runtimes=[], runs=[], activity=[{
                "event_id": "e1",
                "path": "demo.ipynb",
                "type": "execution-started",
                "detail": "Executing cell 1",
                "actor": "human",
                "session_id": "s1",
                "runtime_id": "r1",
                "cell_id": "c1",
                "cell_index": 0,
                "data": {"output": [1, 2, 3]},
                "timestamp": _time.time(),
            }])

            data = load_all(conn)
            self.assertEqual(len(data["activity"]), 1)
            e = data["activity"][0]
            self.assertEqual(e["data"], {"output": [1, 2, 3]})
            conn.close()

    def test_upsert_updates_existing_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            session = {
                "session_id": "s1", "actor": "human", "client": "cli",
                "label": None, "status": "attached", "capabilities": [],
                "resume_count": 0, "created_at": 1.0, "last_seen_at": 2.0,
            }
            persist_all(conn, sessions=[session], documents=[], branches=[],
                        runtimes=[], runs=[], activity=[])
            session["status"] = "detached"
            persist_all(conn, sessions=[session], documents=[], branches=[],
                        runtimes=[], runs=[], activity=[])

            data = load_all(conn)
            self.assertEqual(len(data["sessions"]), 1)
            self.assertEqual(data["sessions"][0]["status"], "detached")
            conn.close()

    def test_replace_removes_missing_session_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            session = {
                "session_id": "s1", "actor": "human", "client": "cli",
                "label": None, "status": "attached", "capabilities": [],
                "resume_count": 0, "created_at": 1.0, "last_seen_at": 2.0,
            }
            persist_all(conn, sessions=[session], documents=[], branches=[],
                        runtimes=[], runs=[], activity=[], executions=[])
            persist_all(conn, sessions=[], documents=[], branches=[],
                        runtimes=[], runs=[], activity=[], executions=[])

            data = load_all(conn)
            self.assertEqual(data["sessions"], [])
            conn.close()


class TestActivityTTL(unittest.TestCase):
    def _make_event(self, event_id: str, timestamp: float) -> dict:
        return {
            "event_id": event_id,
            "path": "demo.ipynb",
            "type": "test-event",
            "detail": "Test",
            "actor": "human",
            "session_id": "s1",
            "runtime_id": None,
            "cell_id": None,
            "cell_index": None,
            "data": None,
            "timestamp": timestamp,
        }

    def test_old_activity_records_are_pruned_on_persist(self):
        import time
        from agent_repl.core.db import ACTIVITY_TTL_SECONDS

        now = time.time()
        old_ts = now - ACTIVITY_TTL_SECONDS - 3600  # 8 days ago
        recent_ts = now - 3600  # 1 hour ago

        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[],
                activity=[
                    self._make_event("old-1", old_ts),
                    self._make_event("recent-1", recent_ts),
                ],
            )

            data = load_all(conn)
            self.assertEqual(len(data["activity"]), 1)
            self.assertEqual(data["activity"][0]["event_id"], "recent-1")
            conn.close()

    def test_all_recent_records_survive_ttl_prune(self):
        import time

        now = time.time()
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[],
                activity=[
                    self._make_event("e1", now - 100),
                    self._make_event("e2", now - 50),
                ],
            )

            data = load_all(conn)
            self.assertEqual(len(data["activity"]), 2)
            conn.close()

class TestExecutionsPersistence(unittest.TestCase):
    def test_round_trip_executions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            execution = {
                "execution_id": "exec-1",
                "status": "completed",
                "path": "demo.ipynb",
                "runtime_id": "rt-1",
                "cell_id": "c1",
                "cell_index": 0,
                "source_preview": "print('hello')",
                "owner": "human",
                "session_id": "s1",
                "operation": "execute-cell",
                "outputs": [{"text": "hello"}],
                "execution_count": 1,
                "error": None,
                "created_at": 1.0,
                "updated_at": 2.0,
            }
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[], activity=[],
                executions=[execution],
            )

            data = load_all(conn)
            self.assertEqual(len(data["executions"]), 1)
            e = data["executions"][0]
            self.assertEqual(e["execution_id"], "exec-1")
            self.assertEqual(e["status"], "completed")
            self.assertEqual(e["outputs"], [{"text": "hello"}])
            self.assertEqual(e["execution_count"], 1)
            self.assertIsNone(e["error"])
            conn.close()

    def test_upsert_updates_execution_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            execution = {
                "execution_id": "exec-1",
                "status": "running",
                "path": "demo.ipynb",
                "runtime_id": "rt-1",
                "cell_id": "c1",
                "cell_index": 0,
                "source_preview": "x = 1",
                "owner": "agent",
                "session_id": None,
                "operation": "execute-cell",
                "created_at": 1.0,
                "updated_at": 1.0,
            }
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[], activity=[],
                executions=[execution],
            )
            execution["status"] = "completed"
            execution["outputs"] = [{"text": "1"}]
            execution["execution_count"] = 1
            execution["updated_at"] = 2.0
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[], activity=[],
                executions=[execution],
            )

            data = load_all(conn)
            self.assertEqual(len(data["executions"]), 1)
            self.assertEqual(data["executions"][0]["status"], "completed")
            conn.close()

    def test_replace_removes_missing_execution_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            execution = {
                "execution_id": "exec-1",
                "status": "completed",
                "path": "demo.ipynb",
                "runtime_id": "rt-1",
                "cell_id": "c1",
                "cell_index": 0,
                "source_preview": "print('hello')",
                "owner": "human",
                "session_id": "s1",
                "operation": "execute-cell",
                "outputs": [{"text": "hello"}],
                "execution_count": 1,
                "error": None,
                "created_at": 1.0,
                "updated_at": 2.0,
            }
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[], activity=[],
                executions=[execution],
            )
            persist_all(
                conn, sessions=[], documents=[], branches=[],
                runtimes=[], runs=[], activity=[],
                executions=[],
            )

            data = load_all(conn)
            self.assertEqual(data["executions"], [])
            conn.close()

if __name__ == "__main__":
    unittest.main()

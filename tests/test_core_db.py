"""Tests for SQLite operational persistence."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from agent_repl.core.db import (
    DB_FILENAME,
    load_all,
    migrate_from_json,
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

    def test_wal_mode_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertEqual(mode, "wal")
            conn.close()

    def test_schema_version_is_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            self.assertIsNotNone(row)
            self.assertGreater(row["version"], 0)
            conn.close()

    def test_reopen_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn1 = open_db(tmpdir)
            conn1.close()
            conn2 = open_db(tmpdir)
            row = conn2.execute("SELECT version FROM schema_version").fetchone()
            self.assertIsNotNone(row)
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


class TestMigrateFromJson(unittest.TestCase):
    def test_imports_json_state_and_renames_to_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = os.path.join(tmpdir, ".agent-repl", "core-state.json")
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            json_state = {
                "sessions": [{
                    "session_id": "s1", "actor": "agent", "client": "mcp",
                    "label": None, "status": "attached", "capabilities": [],
                    "resume_count": 1, "created_at": 1.0, "last_seen_at": 3.0,
                }],
                "documents": [],
                "branches": [],
                "runtimes": [],
                "runs": [],
                "activity": [],
            }
            with open(json_path, "w") as f:
                json.dump(json_state, f)

            conn = open_db(tmpdir)
            migrated = migrate_from_json(conn, json_path)
            self.assertTrue(migrated)
            self.assertFalse(os.path.exists(json_path))
            self.assertTrue(os.path.exists(json_path + ".backup"))

            data = load_all(conn)
            self.assertEqual(len(data["sessions"]), 1)
            self.assertEqual(data["sessions"][0]["session_id"], "s1")
            conn.close()

    def test_returns_false_when_no_json_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            migrated = migrate_from_json(conn, "/nonexistent/path.json")
            self.assertFalse(migrated)
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

    def test_schema_migration_from_v1_adds_executions_table(self):
        """Simulate a v1 database and verify migration adds executions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a v1 database manually
            import sqlite3
            db_dir = os.path.join(tmpdir, ".agent-repl")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "core-state.db")
            raw = sqlite3.connect(db_path)
            raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
            raw.execute("INSERT INTO schema_version (version) VALUES (1)")
            # Create minimal v1 tables so open_db doesn't fail
            raw.executescript("""
                CREATE TABLE sessions (session_id TEXT PRIMARY KEY);
                CREATE TABLE documents (document_id TEXT PRIMARY KEY);
                CREATE TABLE branches (branch_id TEXT PRIMARY KEY);
                CREATE TABLE runtimes (runtime_id TEXT PRIMARY KEY);
                CREATE TABLE runs (run_id TEXT PRIMARY KEY);
                CREATE TABLE activity (event_id TEXT PRIMARY KEY);
            """)
            raw.commit()
            raw.close()

            conn = open_db(tmpdir)
            # executions table should exist after migration
            tables = [
                row[0] for row in
                conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            ]
            self.assertIn("executions", tables)
            version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            self.assertEqual(version, 2)
            conn.close()


class TestPersistNoJsonFallback(unittest.TestCase):
    """persist() with _db set must NOT write to state_file."""

    def test_persist_does_not_write_json_state_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = open_db(tmpdir)
            state_file = os.path.join(tmpdir, ".agent-repl", "core-state.json")

            # Build a minimal CoreState-like object to call persist()
            from agent_repl.core.server import CoreState
            state = CoreState(
                workspace_root=tmpdir,
                runtime_dir=tmpdir,
                token="test-token",
                pid=1,
                started_at=1.0,
                state_file=state_file,
            )
            state._db = conn

            state.persist()

            self.assertFalse(
                os.path.exists(state_file),
                "persist() should not write JSON state file when _db is set",
            )
            conn.close()


if __name__ == "__main__":
    unittest.main()

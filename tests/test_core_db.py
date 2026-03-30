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
                "timestamp": 1.5,
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

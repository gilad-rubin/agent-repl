"""SQLite operational persistence for the core daemon."""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

SCHEMA_VERSION = 1
DB_FILENAME = "core-state.db"


def open_db(workspace_root: str) -> sqlite3.Connection:
    """Open (or create) the operational database under the workspace."""
    db_dir = os.path.join(workspace_root, ".agent-repl")
    os.makedirs(db_dir, exist_ok=True)
    db_path = os.path.join(db_dir, DB_FILENAME)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        )
    """)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        _create_tables(conn)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        conn.commit()
    elif row["version"] < SCHEMA_VERSION:
        _migrate(conn, row["version"])


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            actor TEXT NOT NULL,
            client TEXT NOT NULL,
            label TEXT,
            status TEXT NOT NULL,
            capabilities TEXT NOT NULL,
            resume_count INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            last_seen_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            file_format TEXT NOT NULL,
            sync_state TEXT NOT NULL,
            bound_snapshot TEXT,
            observed_snapshot TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_documents_relative_path
            ON documents(relative_path);

        CREATE TABLE IF NOT EXISTS branches (
            branch_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            owner_session_id TEXT,
            parent_branch_id TEXT,
            title TEXT,
            purpose TEXT,
            status TEXT NOT NULL,
            review_status TEXT,
            review_requested_by_session_id TEXT,
            review_requested_at REAL,
            review_resolved_by_session_id TEXT,
            review_resolved_at REAL,
            review_resolution TEXT,
            review_note TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_branches_document_id
            ON branches(document_id);

        CREATE TABLE IF NOT EXISTS runtimes (
            runtime_id TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            label TEXT,
            environment TEXT,
            status TEXT NOT NULL,
            health TEXT NOT NULL,
            kernel_generation INTEGER NOT NULL DEFAULT 0,
            document_path TEXT,
            branch_id TEXT,
            expires_at REAL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runtimes_status
            ON runtimes(status);

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            runtime_id TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_ref TEXT NOT NULL,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            queue_position INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_runs_runtime_id ON runs(runtime_id);
        CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

        CREATE TABLE IF NOT EXISTS activity (
            event_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            type TEXT NOT NULL,
            detail TEXT NOT NULL,
            actor TEXT,
            session_id TEXT,
            runtime_id TEXT,
            cell_id TEXT,
            cell_index INTEGER,
            data TEXT,
            timestamp REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_activity_timestamp
            ON activity(timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_path
            ON activity(path);
    """)


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    # Future migrations go here as elif chains.
    conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    conn.commit()


# -----------------------------------------------------------------------
# Bulk persistence — mirrors the current persist() pattern
# -----------------------------------------------------------------------

def persist_all(
    conn: sqlite3.Connection,
    *,
    sessions: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    branches: list[dict[str, Any]],
    runtimes: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    activity: list[dict[str, Any]],
) -> None:
    """Write all operational state to SQLite in a single transaction."""
    with conn:
        _upsert_sessions(conn, sessions)
        _upsert_documents(conn, documents)
        _upsert_branches(conn, branches)
        _upsert_runtimes(conn, runtimes)
        _upsert_runs(conn, runs)
        _replace_activity(conn, activity)


def load_all(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Read all operational state from SQLite."""
    return {
        "sessions": _load_rows(conn, "sessions"),
        "documents": _load_rows(conn, "documents"),
        "branches": _load_rows(conn, "branches"),
        "runtimes": _load_rows(conn, "runtimes"),
        "runs": _load_rows(conn, "runs"),
        "activity": _load_rows(conn, "activity", order_by="timestamp ASC"),
    }


# -----------------------------------------------------------------------
# Private helpers
# -----------------------------------------------------------------------

def _load_rows(
    conn: sqlite3.Connection,
    table: str,
    *,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {table}"  # noqa: S608 — table name is hardcoded above
    if order_by:
        sql += f" ORDER BY {order_by}"
    rows = conn.execute(sql).fetchall()
    result = []
    for row in rows:
        record = dict(row)
        _deserialize_json_fields(table, record)
        result.append(record)
    return result


def _deserialize_json_fields(table: str, record: dict[str, Any]) -> None:
    """Parse JSON-encoded text columns back to Python objects."""
    if table == "sessions" and isinstance(record.get("capabilities"), str):
        try:
            record["capabilities"] = json.loads(record["capabilities"])
        except (ValueError, TypeError):
            record["capabilities"] = []
    if table == "documents":
        for field in ("bound_snapshot", "observed_snapshot"):
            if isinstance(record.get(field), str):
                try:
                    record[field] = json.loads(record[field])
                except (ValueError, TypeError):
                    record[field] = None
    if table == "activity" and isinstance(record.get("data"), str):
        try:
            record["data"] = json.loads(record["data"])
        except (ValueError, TypeError):
            record["data"] = None


def _upsert_sessions(conn: sqlite3.Connection, sessions: list[dict[str, Any]]) -> None:
    for s in sessions:
        conn.execute("""
            INSERT OR REPLACE INTO sessions
                (session_id, actor, client, label, status, capabilities,
                 resume_count, created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            s["session_id"], s["actor"], s["client"], s.get("label"),
            s["status"], json.dumps(s.get("capabilities", [])),
            s.get("resume_count", 0), s["created_at"], s["last_seen_at"],
        ))


def _upsert_documents(conn: sqlite3.Connection, documents: list[dict[str, Any]]) -> None:
    for d in documents:
        conn.execute("""
            INSERT OR REPLACE INTO documents
                (document_id, path, relative_path, file_format, sync_state,
                 bound_snapshot, observed_snapshot, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d["document_id"], d["path"], d["relative_path"],
            d["file_format"], d["sync_state"],
            json.dumps(d.get("bound_snapshot")) if d.get("bound_snapshot") else None,
            json.dumps(d.get("observed_snapshot")) if d.get("observed_snapshot") else None,
            d["created_at"], d["updated_at"],
        ))


def _upsert_branches(conn: sqlite3.Connection, branches: list[dict[str, Any]]) -> None:
    for b in branches:
        conn.execute("""
            INSERT OR REPLACE INTO branches
                (branch_id, document_id, owner_session_id, parent_branch_id,
                 title, purpose, status, review_status,
                 review_requested_by_session_id, review_requested_at,
                 review_resolved_by_session_id, review_resolved_at,
                 review_resolution, review_note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            b["branch_id"], b["document_id"], b.get("owner_session_id"),
            b.get("parent_branch_id"), b.get("title"), b.get("purpose"),
            b["status"], b.get("review_status"),
            b.get("review_requested_by_session_id"), b.get("review_requested_at"),
            b.get("review_resolved_by_session_id"), b.get("review_resolved_at"),
            b.get("review_resolution"), b.get("review_note"),
            b["created_at"], b["updated_at"],
        ))


def _upsert_runtimes(conn: sqlite3.Connection, runtimes: list[dict[str, Any]]) -> None:
    for r in runtimes:
        conn.execute("""
            INSERT OR REPLACE INTO runtimes
                (runtime_id, mode, label, environment, status, health,
                 kernel_generation, document_path, branch_id,
                 expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["runtime_id"], r["mode"], r.get("label"), r.get("environment"),
            r["status"], r["health"], r.get("kernel_generation", 0),
            r.get("document_path"), r.get("branch_id"),
            r.get("expires_at"), r["created_at"], r["updated_at"],
        ))


def _upsert_runs(conn: sqlite3.Connection, runs: list[dict[str, Any]]) -> None:
    for r in runs:
        conn.execute("""
            INSERT OR REPLACE INTO runs
                (run_id, runtime_id, target_type, target_ref, kind,
                 status, queue_position, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            r["run_id"], r["runtime_id"], r["target_type"], r["target_ref"],
            r["kind"], r["status"], r.get("queue_position"),
            r["created_at"], r["updated_at"],
        ))


def _replace_activity(conn: sqlite3.Connection, activity: list[dict[str, Any]]) -> None:
    """Replace activity events: delete all, then insert fresh batch."""
    conn.execute("DELETE FROM activity")
    for a in activity:
        conn.execute("""
            INSERT INTO activity
                (event_id, path, type, detail, actor, session_id,
                 runtime_id, cell_id, cell_index, data, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            a["event_id"], a["path"], a["type"], a["detail"],
            a.get("actor"), a.get("session_id"), a.get("runtime_id"),
            a.get("cell_id"), a.get("cell_index"),
            json.dumps(a["data"]) if a.get("data") else None,
            a["timestamp"],
        ))


def migrate_from_json(conn: sqlite3.Connection, json_path: str) -> bool:
    """Import state from an existing JSON state file, if present.

    Returns True if migration occurred, False if no JSON file was found.
    """
    if not os.path.exists(json_path):
        return False
    try:
        with open(json_path) as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(data, dict):
        return False

    persist_all(
        conn,
        sessions=data.get("sessions", []),
        documents=data.get("documents", []),
        branches=data.get("branches", []),
        runtimes=data.get("runtimes", []),
        runs=data.get("runs", []),
        activity=data.get("activity", []),
    )

    # Rename the old JSON file as a backup
    backup_path = json_path + ".backup"
    try:
        os.rename(json_path, backup_path)
    except OSError:
        pass

    return True

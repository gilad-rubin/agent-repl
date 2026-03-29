"""Shared collaboration helpers for the core daemon."""
from __future__ import annotations

from typing import Any

SESSION_STALE_AFTER_SECONDS = 60.0
SESSION_STATUS_RANK = {
    "attached": 3,
    "stale": 2,
    "detached": 1,
}
SESSION_CLIENT_RANK = {
    "vscode": 3,
    "browser": 2,
    "cli": 1,
    "worker": 0,
}
CELL_LEASE_TTL_SECONDS = 45.0


class CollaborationConflictError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.payload = payload or {"error": message}

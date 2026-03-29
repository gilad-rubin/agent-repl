"""Shared collaboration helpers for the core daemon."""
from __future__ import annotations

from typing import Any


class CollaborationConflictError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.payload = payload or {"error": message}

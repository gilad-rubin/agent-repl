"""Shared document request contracts for the core client and daemon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _require_str(payload: dict[str, Any], key: str, *, error: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    return value


@dataclass(frozen=True)
class DocumentOpenRequest:
    path: str

    def to_payload(self) -> dict[str, Any]:
        return {"path": self.path}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DocumentOpenRequest:
        return cls(path=_require_str(payload, "path", error="Missing path"))


@dataclass(frozen=True)
class DocumentRefreshRequest:
    document_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"document_id": self.document_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DocumentRefreshRequest:
        return cls(document_id=_require_str(payload, "document_id", error="Missing document_id"))


@dataclass(frozen=True)
class DocumentRebindRequest:
    document_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"document_id": self.document_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DocumentRebindRequest:
        return cls(document_id=_require_str(payload, "document_id", error="Missing document_id"))

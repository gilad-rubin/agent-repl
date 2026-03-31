"""Shared checkpoint request contracts for the core client and daemon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _require_str(payload: dict[str, Any], key: str, *, error: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class CheckpointCreateRequest:
    path: str
    label: str | None = None
    session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path}
        if self.label is not None:
            body["label"] = self.label
        if self.session_id is not None:
            body["session_id"] = self.session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CheckpointCreateRequest:
        return cls(
            path=_require_str(payload, "path", error="Missing path"),
            label=_optional_str(payload, "label"),
            session_id=_optional_str(payload, "session_id"),
        )


@dataclass(frozen=True)
class CheckpointRestoreRequest:
    checkpoint_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"checkpoint_id": self.checkpoint_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CheckpointRestoreRequest:
        return cls(
            checkpoint_id=_require_str(payload, "checkpoint_id", error="Missing checkpoint_id"),
        )


@dataclass(frozen=True)
class CheckpointListRequest:
    path: str

    def to_payload(self) -> dict[str, Any]:
        return {"path": self.path}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CheckpointListRequest:
        return cls(
            path=_require_str(payload, "path", error="Missing path"),
        )


@dataclass(frozen=True)
class CheckpointDeleteRequest:
    checkpoint_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"checkpoint_id": self.checkpoint_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CheckpointDeleteRequest:
        return cls(
            checkpoint_id=_require_str(payload, "checkpoint_id", error="Missing checkpoint_id"),
        )

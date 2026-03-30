"""Shared runtime and run request contracts for the core client and daemon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_VALID_RUNTIME_MODES = {"interactive", "shared", "headless", "pinned", "ephemeral"}
_VALID_PROMOTE_MODES = {"shared", "pinned"}
_VALID_TARGET_TYPES = {"document", "node", "branch"}


def _require_str(payload: dict[str, Any], key: str, *, error: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(error)
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


# ---- Runtime requests ----


@dataclass(frozen=True)
class RuntimeStartRequest:
    runtime_id: str
    mode: str
    label: str | None = None
    environment: str | None = None
    document_path: str | None = None
    ttl_seconds: int | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"runtime_id": self.runtime_id, "mode": self.mode}
        if self.label is not None:
            body["label"] = self.label
        if self.environment is not None:
            body["environment"] = self.environment
        if self.document_path is not None:
            body["document_path"] = self.document_path
        if self.ttl_seconds is not None:
            body["ttl_seconds"] = self.ttl_seconds
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeStartRequest:
        runtime_id = _require_str(payload, "runtime_id", error="Missing runtime_id")
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in _VALID_RUNTIME_MODES:
            raise ValueError("Invalid mode")
        document_path = _optional_str(payload, "document_path")
        ttl_seconds = _optional_int(payload, "ttl_seconds")
        return cls(
            runtime_id=runtime_id,
            mode=mode,
            label=_optional_str(payload, "label"),
            environment=_optional_str(payload, "environment"),
            document_path=document_path,
            ttl_seconds=ttl_seconds,
        )


@dataclass(frozen=True)
class RuntimeStopRequest:
    runtime_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"runtime_id": self.runtime_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeStopRequest:
        return cls(runtime_id=_require_str(payload, "runtime_id", error="Missing runtime_id"))


@dataclass(frozen=True)
class RuntimeRecoverRequest:
    runtime_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"runtime_id": self.runtime_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeRecoverRequest:
        return cls(runtime_id=_require_str(payload, "runtime_id", error="Missing runtime_id"))


@dataclass(frozen=True)
class RuntimePromoteRequest:
    runtime_id: str
    mode: str = "shared"

    def to_payload(self) -> dict[str, Any]:
        return {"runtime_id": self.runtime_id, "mode": self.mode}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimePromoteRequest:
        runtime_id = _require_str(payload, "runtime_id", error="Missing runtime_id")
        mode = _optional_str(payload, "mode") or "shared"
        if mode not in _VALID_PROMOTE_MODES:
            raise ValueError("Invalid mode")
        return cls(runtime_id=runtime_id, mode=mode)


@dataclass(frozen=True)
class RuntimeDiscardRequest:
    runtime_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"runtime_id": self.runtime_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RuntimeDiscardRequest:
        return cls(runtime_id=_require_str(payload, "runtime_id", error="Missing runtime_id"))


# ---- Run requests ----


@dataclass(frozen=True)
class RunStartRequest:
    run_id: str
    runtime_id: str
    target_type: str
    target_ref: str
    kind: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "runtime_id": self.runtime_id,
            "target_type": self.target_type,
            "target_ref": self.target_ref,
            "kind": self.kind,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RunStartRequest:
        target_type = payload.get("target_type")
        if not isinstance(target_type, str) or target_type not in _VALID_TARGET_TYPES:
            raise ValueError("Invalid target_type")
        return cls(
            run_id=_require_str(payload, "run_id", error="Missing run_id"),
            runtime_id=_require_str(payload, "runtime_id", error="Missing runtime_id"),
            target_type=target_type,
            target_ref=_require_str(payload, "target_ref", error="Missing target_ref"),
            kind=_require_str(payload, "kind", error="Missing kind"),
        )


@dataclass(frozen=True)
class RunFinishRequest:
    run_id: str
    status: str

    def to_payload(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "status": self.status}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> RunFinishRequest:
        return cls(
            run_id=_require_str(payload, "run_id", error="Missing run_id"),
            status=_require_str(payload, "status", error="Missing status"),
        )

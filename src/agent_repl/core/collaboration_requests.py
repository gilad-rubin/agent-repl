"""Shared collaboration request contracts for the core client and daemon."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


def _optional_str_list(payload: dict[str, Any], key: str) -> list[str] | None:
    value = payload.get(key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str) and item]


# ---- Session requests ----


@dataclass(frozen=True)
class SessionStartRequest:
    actor: str
    client: str
    session_id: str
    label: str | None = None
    capabilities: list[str] | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "actor": self.actor,
            "client": self.client,
            "session_id": self.session_id,
        }
        if self.label is not None:
            body["label"] = self.label
        if self.capabilities is not None:
            body["capabilities"] = self.capabilities
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionStartRequest:
        return cls(
            actor=_require_str(payload, "actor", error="Missing actor"),
            client=_require_str(payload, "client", error="Missing client"),
            session_id=_require_str(payload, "session_id", error="Missing session_id"),
            label=_optional_str(payload, "label"),
            capabilities=_optional_str_list(payload, "capabilities"),
        )


@dataclass(frozen=True)
class SessionResolveRequest:
    actor: str = "human"

    def to_payload(self) -> dict[str, Any]:
        return {"actor": self.actor}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionResolveRequest:
        actor = _optional_str(payload, "actor") or "human"
        return cls(actor=actor)


@dataclass(frozen=True)
class SessionTouchRequest:
    session_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"session_id": self.session_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionTouchRequest:
        return cls(session_id=_require_str(payload, "session_id", error="Missing session_id"))


@dataclass(frozen=True)
class SessionDetachRequest:
    session_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"session_id": self.session_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionDetachRequest:
        return cls(session_id=_require_str(payload, "session_id", error="Missing session_id"))


@dataclass(frozen=True)
class SessionEndRequest:
    session_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"session_id": self.session_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SessionEndRequest:
        return cls(session_id=_require_str(payload, "session_id", error="Missing session_id"))


# ---- Presence requests ----


@dataclass(frozen=True)
class PresenceUpsertRequest:
    session_id: str
    path: str
    activity: str
    cell_id: str | None = None
    cell_index: int | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "session_id": self.session_id,
            "path": self.path,
            "activity": self.activity,
        }
        if self.cell_id is not None:
            body["cell_id"] = self.cell_id
        if self.cell_index is not None:
            body["cell_index"] = self.cell_index
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PresenceUpsertRequest:
        return cls(
            session_id=_require_str(payload, "session_id", error="Missing session_id"),
            path=_require_str(payload, "path", error="Missing path"),
            activity=_require_str(payload, "activity", error="Missing activity"),
            cell_id=_optional_str(payload, "cell_id"),
            cell_index=_optional_int(payload, "cell_index"),
        )


@dataclass(frozen=True)
class PresenceClearRequest:
    session_id: str
    path: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"session_id": self.session_id}
        if self.path is not None:
            body["path"] = self.path
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PresenceClearRequest:
        return cls(
            session_id=_require_str(payload, "session_id", error="Missing session_id"),
            path=_optional_str(payload, "path"),
        )


# ---- Branch requests ----


@dataclass(frozen=True)
class BranchStartRequest:
    branch_id: str
    document_id: str
    owner_session_id: str | None = None
    parent_branch_id: str | None = None
    title: str | None = None
    purpose: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "branch_id": self.branch_id,
            "document_id": self.document_id,
        }
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        if self.parent_branch_id is not None:
            body["parent_branch_id"] = self.parent_branch_id
        if self.title is not None:
            body["title"] = self.title
        if self.purpose is not None:
            body["purpose"] = self.purpose
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BranchStartRequest:
        return cls(
            branch_id=_require_str(payload, "branch_id", error="Missing branch_id"),
            document_id=_require_str(payload, "document_id", error="Missing document_id"),
            owner_session_id=_optional_str(payload, "owner_session_id"),
            parent_branch_id=_optional_str(payload, "parent_branch_id"),
            title=_optional_str(payload, "title"),
            purpose=_optional_str(payload, "purpose"),
        )


@dataclass(frozen=True)
class BranchFinishRequest:
    branch_id: str
    status: str

    def to_payload(self) -> dict[str, Any]:
        return {"branch_id": self.branch_id, "status": self.status}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BranchFinishRequest:
        return cls(
            branch_id=_require_str(payload, "branch_id", error="Missing branch_id"),
            status=_require_str(payload, "status", error="Missing status"),
        )


@dataclass(frozen=True)
class BranchReviewRequestRequest:
    branch_id: str
    requested_by_session_id: str
    note: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "branch_id": self.branch_id,
            "requested_by_session_id": self.requested_by_session_id,
        }
        if self.note is not None:
            body["note"] = self.note
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BranchReviewRequestRequest:
        return cls(
            branch_id=_require_str(payload, "branch_id", error="Missing branch_id"),
            requested_by_session_id=_require_str(
                payload, "requested_by_session_id", error="Missing requested_by_session_id"
            ),
            note=_optional_str(payload, "note"),
        )


@dataclass(frozen=True)
class BranchReviewResolveRequest:
    branch_id: str
    resolved_by_session_id: str
    resolution: str
    note: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "branch_id": self.branch_id,
            "resolved_by_session_id": self.resolved_by_session_id,
            "resolution": self.resolution,
        }
        if self.note is not None:
            body["note"] = self.note
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> BranchReviewResolveRequest:
        return cls(
            branch_id=_require_str(payload, "branch_id", error="Missing branch_id"),
            resolved_by_session_id=_require_str(
                payload, "resolved_by_session_id", error="Missing resolved_by_session_id"
            ),
            resolution=_require_str(payload, "resolution", error="Missing resolution"),
            note=_optional_str(payload, "note"),
        )


# ---- Lease requests (non-notebook, session-level) ----


@dataclass(frozen=True)
class LeaseAcquireRequest:
    session_id: str
    resource_id: str
    kind: str = "edit"

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "resource_id": self.resource_id,
            "kind": self.kind,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LeaseAcquireRequest:
        return cls(
            session_id=_require_str(payload, "session_id", error="Missing session_id"),
            resource_id=_require_str(payload, "resource_id", error="Missing resource_id"),
            kind=_optional_str(payload, "kind") or "edit",
        )


@dataclass(frozen=True)
class LeaseReleaseRequest:
    session_id: str
    resource_id: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "resource_id": self.resource_id,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LeaseReleaseRequest:
        return cls(
            session_id=_require_str(payload, "session_id", error="Missing session_id"),
            resource_id=_require_str(payload, "resource_id", error="Missing resource_id"),
        )

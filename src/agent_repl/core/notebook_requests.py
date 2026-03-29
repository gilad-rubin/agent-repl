"""Shared notebook request contracts for the core client and daemon."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _require_path(payload: dict[str, Any]) -> str:
    path = payload.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Missing path")
    return path


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _require_str(payload: dict[str, Any], key: str, *, error: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(error)
    return value


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) else None


def _optional_number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _optional_dict_list(payload: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    value = payload.get(key)
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _require_dict_list(payload: dict[str, Any], key: str, *, error: str) -> list[dict[str, Any]]:
    value = _optional_dict_list(payload, key)
    if value is None:
        raise ValueError(error)
    return value


@dataclass(frozen=True)
class NotebookPathRequest:
    path: str

    def to_payload(self) -> dict[str, Any]:
        return {"path": self.path}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookPathRequest:
        return cls(path=_require_path(payload))


@dataclass(frozen=True)
class NotebookSessionPathRequest:
    path: str
    owner_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path}
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookSessionPathRequest:
        return cls(
            path=_require_path(payload),
            owner_session_id=_optional_str(payload, "owner_session_id"),
        )


@dataclass(frozen=True)
class NotebookCreateRequest:
    path: str
    cells: list[dict[str, Any]] | None = None
    kernel_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path}
        if self.cells is not None:
            body["cells"] = self.cells
        if self.kernel_id is not None:
            body["kernel_id"] = self.kernel_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookCreateRequest:
        return cls(
            path=_require_path(payload),
            cells=_optional_dict_list(payload, "cells"),
            kernel_id=_optional_str(payload, "kernel_id"),
        )


@dataclass(frozen=True)
class NotebookSelectKernelRequest:
    path: str
    kernel_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path}
        if self.kernel_id is not None:
            body["kernel_id"] = self.kernel_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookSelectKernelRequest:
        return cls(path=_require_path(payload), kernel_id=_optional_str(payload, "kernel_id"))


@dataclass(frozen=True)
class NotebookEditRequest:
    path: str
    operations: list[dict[str, Any]]
    owner_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path, "operations": self.operations}
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookEditRequest:
        return cls(
            path=_require_path(payload),
            operations=_require_dict_list(payload, "operations", error="Missing operations"),
            owner_session_id=_optional_str(payload, "owner_session_id"),
        )


@dataclass(frozen=True)
class NotebookExecuteCellRequest:
    path: str
    cell_id: str | None = None
    cell_index: int | None = None
    owner_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path}
        if self.cell_id is not None:
            body["cell_id"] = self.cell_id
        if self.cell_index is not None:
            body["cell_index"] = self.cell_index
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookExecuteCellRequest:
        return cls(
            path=_require_path(payload),
            cell_id=_optional_str(payload, "cell_id"),
            cell_index=_optional_int(payload, "cell_index"),
            owner_session_id=_optional_str(payload, "owner_session_id"),
        )


@dataclass(frozen=True)
class NotebookInsertExecuteRequest:
    path: str
    source: str
    cell_type: str = "code"
    at_index: int = -1
    owner_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "path": self.path,
            "source": self.source,
            "cell_type": self.cell_type,
            "at_index": self.at_index,
        }
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookInsertExecuteRequest:
        return cls(
            path=_require_path(payload),
            source=_require_str(payload, "source", error="Missing source"),
            cell_type=_optional_str(payload, "cell_type") or "code",
            at_index=_optional_int(payload, "at_index") or -1,
            owner_session_id=_optional_str(payload, "owner_session_id"),
        )


@dataclass(frozen=True)
class NotebookExecutionLookupRequest:
    execution_id: str

    def to_payload(self) -> dict[str, Any]:
        return {"execution_id": self.execution_id}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookExecutionLookupRequest:
        execution_id = payload.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("Missing execution_id")
        return cls(execution_id=execution_id)


@dataclass(frozen=True)
class NotebookActivityRequest:
    path: str
    since: float | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path}
        if self.since is not None:
            body["since"] = self.since
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookActivityRequest:
        return cls(path=_require_path(payload), since=_optional_number(payload, "since"))


@dataclass(frozen=True)
class NotebookProjectVisibleRequest:
    path: str
    cells: list[dict[str, Any]]
    owner_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path, "cells": self.cells}
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookProjectVisibleRequest:
        return cls(
            path=_require_path(payload),
            cells=_require_dict_list(payload, "cells", error="Missing cells"),
            owner_session_id=_optional_str(payload, "owner_session_id"),
        )


@dataclass(frozen=True)
class NotebookExecuteVisibleCellRequest:
    path: str
    cell_index: int
    source: str
    owner_session_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path, "cell_index": self.cell_index, "source": self.source}
        if self.owner_session_id is not None:
            body["owner_session_id"] = self.owner_session_id
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookExecuteVisibleCellRequest:
        cell_index = _optional_int(payload, "cell_index")
        if cell_index is None:
            raise ValueError("Missing cell_index")
        return cls(
            path=_require_path(payload),
            cell_index=cell_index,
            source=_require_str(payload, "source", error="Missing source"),
            owner_session_id=_optional_str(payload, "owner_session_id"),
        )


@dataclass(frozen=True)
class NotebookLeaseAcquireRequest:
    path: str
    session_id: str
    cell_id: str | None = None
    cell_index: int | None = None
    kind: str = "edit"
    ttl_seconds: float | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path, "session_id": self.session_id, "kind": self.kind}
        if self.cell_id is not None:
            body["cell_id"] = self.cell_id
        if self.cell_index is not None:
            body["cell_index"] = self.cell_index
        if self.ttl_seconds is not None:
            body["ttl_seconds"] = self.ttl_seconds
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookLeaseAcquireRequest:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Missing session_id")
        return cls(
            path=_require_path(payload),
            session_id=session_id,
            cell_id=_optional_str(payload, "cell_id"),
            cell_index=_optional_int(payload, "cell_index"),
            kind=_optional_str(payload, "kind") or "edit",
            ttl_seconds=_optional_number(payload, "ttl_seconds"),
        )


@dataclass(frozen=True)
class NotebookLeaseReleaseRequest:
    path: str
    session_id: str
    cell_id: str | None = None
    cell_index: int | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"path": self.path, "session_id": self.session_id}
        if self.cell_id is not None:
            body["cell_id"] = self.cell_id
        if self.cell_index is not None:
            body["cell_index"] = self.cell_index
        return body

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> NotebookLeaseReleaseRequest:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("Missing session_id")
        return cls(
            path=_require_path(payload),
            session_id=session_id,
            cell_id=_optional_str(payload, "cell_id"),
            cell_index=_optional_int(payload, "cell_index"),
        )

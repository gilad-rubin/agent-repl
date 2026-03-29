"""Shared notebook runtime client contracts and adapters."""
from __future__ import annotations

from typing import Any, Protocol

from agent_repl.client import BridgeClient


class NotebookRuntimeClient(Protocol):
    def notebook_contents(self, path: str) -> dict[str, Any]: ...
    def notebook_status(self, path: str) -> dict[str, Any]: ...
    def notebook_create(
        self,
        path: str,
        cells: list[dict[str, Any]] | None = None,
        kernel_id: str | None = None,
    ) -> dict[str, Any]: ...
    def notebook_select_kernel(self, path: str, kernel_id: str | None = None) -> dict[str, Any]: ...
    def notebook_edit(
        self,
        path: str,
        operations: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> dict[str, Any]: ...
    def notebook_execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None = None,
        cell_index: int | None = None,
        wait: bool = True,
        timeout: float = 30,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]: ...
    def notebook_insert_execute(
        self,
        path: str,
        source: str,
        *,
        cell_type: str = "code",
        at_index: int = -1,
        wait: bool = True,
        timeout: float = 30,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]: ...
    def notebook_execute_all(self, path: str, owner_session_id: str | None = None) -> dict[str, Any]: ...
    def notebook_restart(self, path: str) -> dict[str, Any]: ...
    def notebook_restart_and_run_all(
        self,
        path: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]: ...
    def resolve_preferred_session(self, *, actor: str = "human") -> dict[str, Any]: ...
    def start_session(
        self,
        *,
        actor: str,
        client: str,
        label: str | None = None,
        capabilities: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]: ...


class BridgeNotebookRuntimeAdapter:
    """Legacy bridge adapter for tests and explicitly bridge-backed flows."""

    def __init__(self, bridge: BridgeClient):
        self._bridge = bridge

    def notebook_contents(self, path: str) -> dict[str, Any]:
        return self._bridge.contents(path)

    def notebook_status(self, path: str) -> dict[str, Any]:
        return self._bridge.status(path)

    def notebook_create(
        self,
        path: str,
        cells: list[dict[str, Any]] | None = None,
        kernel_id: str | None = None,
    ) -> dict[str, Any]:
        return self._bridge.create(path, cells=cells, kernel_id=kernel_id)

    def notebook_select_kernel(self, path: str, kernel_id: str | None = None) -> dict[str, Any]:
        return self._bridge.select_kernel(path, kernel_id=kernel_id)

    def notebook_edit(
        self,
        path: str,
        operations: list[dict[str, Any]],
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._bridge.edit(path, operations)

    def notebook_execute_cell(
        self,
        path: str,
        *,
        cell_id: str | None = None,
        cell_index: int | None = None,
        wait: bool = True,
        timeout: float = 30,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._bridge.execute_cell(
            path,
            cell_id=cell_id,
            cell_index=cell_index,
            wait=wait,
            timeout=timeout,
        )

    def notebook_insert_execute(
        self,
        path: str,
        source: str,
        *,
        cell_type: str = "code",
        at_index: int = -1,
        wait: bool = True,
        timeout: float = 30,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._bridge.insert_and_execute(
            path,
            source,
            cell_type=cell_type,
            at_index=at_index,
            wait=wait,
            timeout=timeout,
        )

    def notebook_execute_all(self, path: str, owner_session_id: str | None = None) -> dict[str, Any]:
        return self._bridge.execute_all(path)

    def notebook_restart(self, path: str) -> dict[str, Any]:
        return self._bridge.restart_kernel(path)

    def notebook_restart_and_run_all(
        self,
        path: str,
        owner_session_id: str | None = None,
    ) -> dict[str, Any]:
        return self._bridge.restart_and_run_all(path)

    def resolve_preferred_session(self, *, actor: str = "human") -> dict[str, Any]:
        return {"status": "ok", "session": None}

    def start_session(
        self,
        *,
        actor: str,
        client: str,
        label: str | None = None,
        capabilities: list[str] | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return {"status": "ok", "session": None}

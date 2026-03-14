"""Notebook model I/O: load, save, normalize."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any
from urllib.parse import quote

from agent_repl.core import CommandError, ServerClient, ServerInfo, DEFAULT_TIMEOUT
from agent_repl.output.normalize import normalize_notebook_outputs


def _synthetic_cell_id(cell: dict[str, Any], *, notebook_path: str, cell_index: int) -> str:
    payload = json.dumps(
        {"path": notebook_path, "index": cell_index, "cell_type": cell.get("cell_type"),
         "source": cell.get("source", ""), "metadata": cell.get("metadata", {})},
        sort_keys=True,
    )
    return f"synthetic-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _ensure_cell_id(cell: dict[str, Any], *, notebook_path: str | None, cell_index: int) -> None:
    if cell.get("id"):
        return
    if notebook_path:
        cell["id"] = _synthetic_cell_id(cell, notebook_path=notebook_path, cell_index=cell_index)
        return
    cell["id"] = uuid.uuid4().hex


def normalize_notebook_content(content: dict[str, Any], notebook_path: str | None = None) -> None:
    content.setdefault("metadata", {})
    content.setdefault("nbformat", 4)
    content.setdefault("nbformat_minor", 5)
    cells = content.setdefault("cells", [])
    for cell_index, cell in enumerate(cells):
        source = cell.get("source", "")
        if isinstance(source, list):
            cell["source"] = "".join(source)
        _ensure_cell_id(cell, notebook_path=notebook_path, cell_index=cell_index)


def load_notebook_model(server: ServerInfo, path: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    client = ServerClient(server, timeout=timeout)
    encoded_path = quote(path, safe="/")
    model = client.request("GET", f"api/contents/{encoded_path}", params={"content": 1, "type": "notebook"})
    normalize_notebook_content(model.setdefault("content", {}), notebook_path=path)
    return model


def save_notebook_content(
    server: ServerInfo, path: str, content: dict[str, Any],
    timeout: float = DEFAULT_TIMEOUT, expected_last_modified: str | None = None,
    normalize_outputs: bool = True,
) -> dict[str, Any]:
    client = ServerClient(server, timeout=timeout)
    encoded_path = quote(path, safe="/")
    normalize_notebook_content(content, notebook_path=path)
    if normalize_outputs:
        normalize_notebook_outputs(content)
    if expected_last_modified is not None:
        current = client.request("GET", f"api/contents/{encoded_path}", params={"content": 0, "type": "notebook"})
        if current.get("last_modified") != expected_last_modified:
            raise CommandError("Notebook changed since it was loaded; reload contents and retry the edit.")
    return client.request("PUT", f"api/contents/{encoded_path}", payload={"type": "notebook", "format": "json", "content": content})


def save_run_all_outputs(server: ServerInfo, path: str, *, executed_model: dict[str, Any], timeout: float) -> None:
    """Merge executed outputs into the latest notebook model before saving."""
    latest_model = load_notebook_model(server, path, timeout=timeout)
    executed_cells = executed_model.get("cells") or []
    latest_cells = (latest_model.get("content") or {}).get("cells") or []

    if len(executed_cells) != len(latest_cells):
        raise CommandError("Notebook changed while run-all was executing; cell structure no longer matches, so outputs were not saved.")

    for index, (executed_cell, latest_cell) in enumerate(zip(executed_cells, latest_cells)):
        if executed_cell.get("cell_type") != latest_cell.get("cell_type"):
            raise CommandError(f"Notebook changed while run-all was executing; cell {index} changed type, so outputs were not saved.")
        if executed_cell.get("cell_type") != "code":
            continue
        executed_id, latest_id = executed_cell.get("id"), latest_cell.get("id")
        if executed_id and latest_id and executed_id != latest_id:
            raise CommandError(f"Notebook changed while run-all was executing; cell {index} changed identity, so outputs were not saved.")
        if (executed_cell.get("source") or "") != (latest_cell.get("source") or ""):
            raise CommandError(f"Notebook changed while run-all was executing; code cell {index} changed source, so outputs were not saved.")
        latest_cell["outputs"] = executed_cell.get("outputs", [])
        latest_cell["execution_count"] = executed_cell.get("execution_count")

    save_notebook_content(server, path, latest_model["content"], timeout=timeout, expected_last_modified=latest_model.get("last_modified"))

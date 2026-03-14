"""Create new notebooks."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from nbformat import v4 as nbf

from agent_repl.core import CommandError, HTTPCommandError, ServerClient, ServerInfo, DEFAULT_TIMEOUT
from agent_repl.notebook.io import normalize_notebook_content


def create_notebook(
    server: ServerInfo, *, path: str, kernel_name: str = "python3",
    cells: list[dict[str, Any]] | None = None, timeout: float = DEFAULT_TIMEOUT, start_kernel: bool = True,
) -> dict[str, Any]:
    """Create a new notebook and optionally start a kernel session."""
    client = ServerClient(server, timeout=timeout)
    encoded_path = quote(path, safe="/")

    try:
        client.request("GET", f"api/contents/{encoded_path}", params={"content": 0})
        raise CommandError(f"Notebook already exists at {path!r}.")
    except HTTPCommandError as exc:
        if exc.status_code != 404:
            raise

    nb = nbf.new_notebook()
    if cells:
        for cell_def in cells:
            cell_type = cell_def.get("type", "code")
            cell_source = cell_def.get("source", "")
            if cell_type == "code":
                nb.cells.append(nbf.new_code_cell(source=cell_source))
            elif cell_type == "markdown":
                nb.cells.append(nbf.new_markdown_cell(source=cell_source))
            elif cell_type == "raw":
                nb.cells.append(nbf.new_raw_cell(source=cell_source))
            else:
                raise CommandError(f"Unsupported cell type {cell_type!r} in cells definition.")
    else:
        nb.cells.append(nbf.new_markdown_cell(source=f"# {Path(path).stem}"))
        nb.cells.append(nbf.new_code_cell(source=""))

    nb.metadata["kernelspec"] = {"name": kernel_name, "display_name": kernel_name}
    content = dict(nb)
    normalize_notebook_content(content, notebook_path=path)

    client.request("PUT", f"api/contents/{encoded_path}", payload={"type": "notebook", "format": "json", "content": content})

    session_info = None
    if start_kernel:
        session_info = client.request("POST", "api/sessions", payload={"path": path, "type": "notebook", "kernel": {"name": kernel_name}})

    return {"operation": "new", "path": path, "kernel_name": kernel_name, "cell_count": len(content.get("cells", [])), "session": session_info}

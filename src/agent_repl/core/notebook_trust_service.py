"""Notebook trust helpers built on the standard Jupyter notary."""
from __future__ import annotations

import copy
import os
from typing import Any

import nbformat
from nbformat.sign import NotebookNotary


class NotebookTrustService:
    """Evaluate and update notebook trust using Jupyter's NotebookNotary."""

    def __init__(self, *, db_file: str | None = None):
        self._db_file = db_file
        if db_file:
            os.makedirs(os.path.dirname(db_file), exist_ok=True)

    def trust_snapshot_for_path(self, real_path: str) -> dict[str, Any]:
        notebook = self._read_notebook(real_path)
        return self.trust_snapshot_for_notebook(notebook)

    def trust_snapshot_for_notebook(self, notebook: Any) -> dict[str, Any]:
        notebook_copy = copy.deepcopy(notebook)
        notary = self._make_notary()
        notebook_trusted = bool(notary.check_signature(notebook_copy))
        notary.mark_cells(notebook_copy, notebook_trusted)

        cell_trust_by_index: list[bool | None] = []
        total_code_cells = 0
        trusted_code_cells = 0
        for cell in notebook_copy.cells:
            if getattr(cell, "cell_type", None) != "code":
                cell_trust_by_index.append(None)
                continue
            total_code_cells += 1
            trusted = bool(getattr(cell, "metadata", {}).get("trusted"))
            trusted_code_cells += int(trusted)
            cell_trust_by_index.append(trusted)

        return {
            "notebook_trusted": notebook_trusted,
            "trusted_code_cells": trusted_code_cells,
            "total_code_cells": total_code_cells,
            "cell_trust_by_index": cell_trust_by_index,
        }

    def sign(self, notebook: Any) -> None:
        self._make_notary().sign(notebook)

    def _make_notary(self) -> NotebookNotary:
        kwargs: dict[str, Any] = {}
        if self._db_file:
            kwargs["db_file"] = self._db_file
        return NotebookNotary(**kwargs)

    def _read_notebook(self, real_path: str) -> Any:
        if os.path.exists(real_path):
            with open(real_path, "r", encoding="utf-8") as handle:
                raw_text = handle.read()
            if raw_text.strip():
                return nbformat.reads(raw_text, as_version=4)
        return nbformat.v4.new_notebook()

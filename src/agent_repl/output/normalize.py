"""Output normalization for clean saves — strip ANSI, clean repr IDs, remove noisy metadata."""
from __future__ import annotations

import re
from typing import Any

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_REPR_ID_RE = re.compile(r"(<.*?)( at 0x[0-9a-fA-F]+)(>)")
_COLAB_MIME = "application/vnd.google.colaboratory.intrinsic+json"


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def clean_repr_ids(text: str) -> str:
    """Remove volatile object addresses: <Foo at 0x7f...> → <Foo>"""
    return _REPR_ID_RE.sub(r"\1\3", text)


def _clean_text(text: str | list[str]) -> str | list[str]:
    if isinstance(text, list):
        return [strip_ansi(clean_repr_ids(line)) for line in text]
    return strip_ansi(clean_repr_ids(text))


def normalize_cell_outputs(cell: dict[str, Any]) -> None:
    """Clean cell outputs in-place for saving. Idempotent."""
    if cell.get("cell_type") != "code":
        return
    for output in cell.get("outputs", []):
        # Strip ANSI + repr IDs from stream text
        if "text" in output:
            output["text"] = _clean_text(output["text"])

        # Clean data dict
        data = output.get("data")
        if not data:
            continue

        # Remove Colab metadata
        data.pop(_COLAB_MIME, None)

        # Clean text-based MIME types
        for key in list(data):
            if key.startswith("text/"):
                data[key] = _clean_text(data[key])

        # Trim trailing whitespace from base64 image data
        for key in list(data):
            if key.startswith("image/") and "svg" not in key and isinstance(data[key], str):
                data[key] = data[key].rstrip()


def normalize_notebook_outputs(content: dict[str, Any]) -> None:
    """Normalize all cell outputs in a notebook content dict."""
    for cell in content.get("cells", []):
        normalize_cell_outputs(cell)

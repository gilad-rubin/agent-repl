"""Cell directive parsing — extract #| agent: instructions and tags from cells."""
from __future__ import annotations

import re
from typing import Any

from agent_repl.notebook.cells import summarize_cell_brief

# Code cells:    #| key: value  (at start of line, before any code)
_CODE_DIRECTIVE_RE = re.compile(r"^#\|\s*(\S+?):\s*(.*)$", re.MULTILINE)
_CODE_FLAG_RE = re.compile(r"^#\|\s*(\S+)\s*$", re.MULTILINE)

# Markdown cells: <!-- agent: instruction -->
_MD_AGENT_RE = re.compile(r"<!--\s*agent:\s*(.*?)\s*-->", re.DOTALL)


def parse_directives(cell: dict[str, Any]) -> dict[str, list[str]]:
    """Extract directives from cell source.

    Code cells:   #| agent: <instruction>
                  #| agent-tags: critical, setup
                  #| agent-skip
    Markdown:     <!-- agent: <instruction> -->

    Returns dict mapping directive name → list of values.
    Flags (no value) get an empty list.
    """
    source = cell.get("source", "")
    cell_type = cell.get("cell_type", "")
    directives: dict[str, list[str]] = {}

    if cell_type in ("code", "raw"):
        # Only parse leading comment lines (before first non-comment, non-empty line)
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("#|"):
                break
            # Try key: value
            m = _CODE_DIRECTIVE_RE.match(stripped)
            if m:
                key, value = m.group(1), m.group(2).strip()
                directives.setdefault(key, []).append(value)
                continue
            # Try bare flag
            m = _CODE_FLAG_RE.match(stripped)
            if m:
                directives.setdefault(m.group(1), [])

    elif cell_type == "markdown":
        for m in _MD_AGENT_RE.finditer(source):
            directives.setdefault("agent", []).append(m.group(1).strip())

    return directives


def has_agent_prompt(cell: dict[str, Any]) -> bool:
    """True if cell contains an agent prompt directive."""
    directives = parse_directives(cell)
    return "agent" in directives


def extract_prompt(cell: dict[str, Any]) -> str | None:
    """Return the agent instruction text, or None."""
    directives = parse_directives(cell)
    values = directives.get("agent", [])
    return values[0] if values else None


def extract_tags(cell: dict[str, Any]) -> set[str]:
    """Return the set of agent tags on a cell."""
    directives = parse_directives(cell)
    tags: set[str] = set()
    for value in directives.get("agent-tags", []):
        tags.update(t.strip() for t in value.split(",") if t.strip())
    return tags


def has_skip_directive(cell: dict[str, Any]) -> bool:
    """True if cell has #| agent-skip directive."""
    return "agent-skip" in parse_directives(cell)


def is_response_cell(cell: dict[str, Any], prompt_cell_id: str) -> bool:
    """True if cell metadata links it as a response to the given prompt."""
    return cell.get("metadata", {}).get("agent-repl", {}).get("responds_to") == prompt_cell_id


def list_prompts(
    cells: list[dict[str, Any]],
    *,
    pending_only: bool = True,
    context_cells: int = 1,
) -> list[dict[str, Any]]:
    """Scan cells for agent prompts, return structured prompt list."""
    prompts: list[dict[str, Any]] = []

    for i, cell in enumerate(cells):
        if not has_agent_prompt(cell):
            continue

        instruction = extract_prompt(cell)
        cell_id = cell.get("id")

        # Check if next cell is a response
        answered = False
        if i + 1 < len(cells) and cell_id:
            answered = is_response_cell(cells[i + 1], cell_id)

        status = "answered" if answered else "pending"
        if pending_only and answered:
            continue

        # Context above
        start = max(0, i - context_cells)
        context_above = [
            summarize_cell_brief(cells[j], index=j)
            for j in range(start, i)
        ]

        # Context below (skip the response cell if answered)
        below_start = i + 2 if answered else i + 1
        below_end = min(len(cells), below_start + context_cells)
        context_below = [
            summarize_cell_brief(cells[j], index=j)
            for j in range(below_start, below_end)
        ]

        prompts.append({
            "cell_id": cell_id,
            "index": i,
            "instruction": instruction,
            "cell_source": cell.get("source", ""),
            "status": status,
            "context_above": context_above,
            "context_below": context_below,
        })

    return prompts

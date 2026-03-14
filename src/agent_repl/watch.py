"""Watch notebook for new agent prompt cells."""
from __future__ import annotations

import time
from typing import Any, Iterator

from agent_repl.core.models import DEFAULT_TIMEOUT, ServerInfo
from agent_repl.notebook.directives import list_prompts
from agent_repl.notebook.io import load_notebook_model


def watch_for_prompts(
    server: ServerInfo,
    *,
    path: str,
    interval: float = 2.0,
    once: bool = False,
    context_cells: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
) -> Iterator[dict[str, Any]]:
    """Poll notebook for new pending prompts. Yields prompt dicts as they appear."""
    seen_prompt_ids: set[str] = set()
    while True:
        model = load_notebook_model(server, path, timeout=timeout)
        cells = model["content"]["cells"]
        prompts = list_prompts(cells, pending_only=True, context_cells=context_cells)
        for prompt in prompts:
            cell_id = prompt.get("cell_id")
            if cell_id and cell_id not in seen_prompt_ids:
                seen_prompt_ids.add(cell_id)
                yield prompt
        if once:
            break
        time.sleep(interval)

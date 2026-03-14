"""Git integration — clean notebooks for version control."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from agent_repl.notebook.io import normalize_notebook_content


def clean_notebook(content: dict[str, Any]) -> dict[str, Any]:
    """Strip outputs, execution counts, and volatile metadata for clean git diffs."""
    cleaned = copy.deepcopy(content)
    normalize_notebook_content(cleaned)

    for cell in cleaned.get("cells", []):
        if cell.get("cell_type") == "code":
            cell["outputs"] = []
            cell["execution_count"] = None

        # Keep only stable metadata (agent-repl tags)
        meta = cell.get("metadata", {})
        agent_meta = meta.get("agent-repl", {})
        stable_agent = {}
        if "tags" in agent_meta:
            stable_agent["tags"] = agent_meta["tags"]
        cell["metadata"] = {"agent-repl": stable_agent} if stable_agent else {}

    # Sort top-level metadata keys for deterministic diffs
    if "metadata" in cleaned:
        cleaned["metadata"] = dict(sorted(cleaned["metadata"].items()))

    return cleaned


def setup_git_filters(repo_path: str = ".") -> dict[str, str]:
    """Configure .gitattributes and git filter for notebook cleaning."""
    repo = Path(repo_path)

    # Write .gitattributes
    gitattributes = repo / ".gitattributes"
    line = "*.ipynb filter=agent-repl-clean\n"
    if gitattributes.exists():
        existing = gitattributes.read_text()
        if "agent-repl-clean" not in existing:
            with gitattributes.open("a") as f:
                f.write(line)
    else:
        gitattributes.write_text(line)

    # Return git config commands (caller runs them)
    return {
        "gitattributes": str(gitattributes),
        "git_config_clean": "git config filter.agent-repl-clean.clean 'agent-repl clean %f'",
        "git_config_smudge": "git config filter.agent-repl-clean.smudge cat",
    }

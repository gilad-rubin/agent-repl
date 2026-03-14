#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "jupyter-client>=8",
#     "jupyter-server>=2",
#     "nbformat>=5",
#     "requests>=2",
#     "websocket-client>=1.8",
# ]
# ///
"""Bootstrap script for `uv run scripts/agent_repl.py` invocation."""
import sys
from pathlib import Path

# Add project root so the agent_repl package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_repl.cli import main

raise SystemExit(main())

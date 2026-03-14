"""CLI helpers: output, argument reading, parser building blocks."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from agent_repl.core.errors import CommandError
from agent_repl.core.models import DEFAULT_EXEC_TIMEOUT, DEFAULT_TIMEOUT


def _sanitize_error_text(text: str, *, server_token: str | None = None) -> str:
    redacted = re.sub(r'([?&]token=)([^&\s]+)', r'\1[REDACTED]', text)
    if server_token:
        redacted = redacted.replace(server_token, "[REDACTED]")
    return redacted


def _print(data: Any, pretty: bool = False) -> None:
    """Print JSON — compact by default (agent-friendly), pretty with --pretty."""
    if pretty:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(json.dumps(data, separators=(",", ":"), sort_keys=True))


def _read_text_argument(value: str | None, file_path: str | None, purpose: str) -> str:
    if value is not None:
        return value
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise CommandError(f"Provide {purpose} with --{purpose}, --{purpose}-file, or stdin.")


def _read_code_argument(code: str | None, code_file: str | None) -> str:
    return _read_text_argument(code, code_file, "code")


def _read_source_argument(source: str | None, source_file: str | None) -> str:
    return _read_text_argument(source, source_file, "source")


def _add_server_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--server-url")
    default_port = os.environ.get("AGENT_REPL_PORT")
    parser.add_argument(
        "-p", "--port", type=int,
        default=int(default_port) if default_port else None,
    )


def _add_path_positional(parser: argparse.ArgumentParser) -> None:
    """Add notebook path as positional arg with --path backward compat."""
    parser.add_argument("path", nargs="?", default=None, metavar="PATH")
    parser.add_argument("--path", dest="path_flag", default=None, help=argparse.SUPPRESS)


def _resolve_path_arg(args: argparse.Namespace, *, required: bool = True) -> str | None:
    path = getattr(args, "path", None) or getattr(args, "path_flag", None)
    if required and not path:
        raise CommandError("Provide a notebook path as a positional argument or with --path.")
    return path


def _add_live_target_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--session-id")
    parser.add_argument("--kernel-id")


def _add_cell_selector(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    group = parser.add_mutually_exclusive_group(required=required)
    group.add_argument("-i", "--index", type=int)
    group.add_argument("--cell-id")


def _resolve_insert_index(args: argparse.Namespace) -> int:
    if args.at_index is not None:
        return args.at_index
    if getattr(args, "before", None) is not None:
        return args.before
    if getattr(args, "after", None) is not None:
        return args.after + 1
    raise CommandError("Pass one of --at-index, --before, or --after.")

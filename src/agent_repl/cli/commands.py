"""CLI command dispatch — main() entry point."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from agent_repl.core.errors import CommandError
from agent_repl.cli.helpers import (
    _print, _read_code_argument, _read_source_argument, _read_text_argument,
    _resolve_insert_index, _resolve_path_arg, _sanitize_error_text,
)
from agent_repl.cli.parser import build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    server_token: str | None = None
    pretty = getattr(args, "pretty", False)

    try:
        if args.command == "servers":
            from agent_repl.server import discover_servers
            _print({"servers": discover_servers(timeout=args.timeout)}, pretty)
            return 0

        if args.command == "start":
            import shutil
            import subprocess
            jupyter = shutil.which("jupyter") or "jupyter"
            cmd = [jupyter, "lab", "--IdentityProvider.token=''", "--ServerApp.password=''", "--no-browser"]
            if hasattr(args, "port") and args.port:
                cmd.append(f"--port={args.port}")
            if hasattr(args, "dir") and args.dir != ".":
                cmd.append(f"--notebook-dir={args.dir}")
            if args.foreground:
                return subprocess.call(cmd)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            print(json.dumps({"pid": proc.pid, "command": " ".join(cmd)}))
            return 0

        if args.command == "git-setup":
            from agent_repl.git import setup_git_filters
            result = setup_git_filters()
            print(json.dumps(result, indent=2))
            return 0

        if args.command == "kernels":
            from agent_repl.server import list_kernelspecs_and_running, select_server
            server = select_server(server_url=args.server_url, port=args.port, timeout=args.timeout)
            server_token = server.token
            _print(list_kernelspecs_and_running(server, timeout=args.timeout), pretty)
            return 0

        from agent_repl.server import select_server
        server = select_server(server_url=args.server_url, port=args.port, timeout=args.timeout)
        server_token = server.token

        if args.command in ("notebooks", "ls"):
            from agent_repl.server import combined_open_notebooks
            _print(combined_open_notebooks(server, timeout=args.timeout), pretty)
            return 0

        if args.command == "prompts":
            from agent_repl.notebook.directives import list_prompts
            from agent_repl.notebook.io import load_notebook_model
            path = _resolve_path_arg(args)
            model = load_notebook_model(server, path, timeout=args.timeout)
            prompts = list_prompts(model["content"]["cells"], pending_only=not args.all, context_cells=args.context)
            _print({"prompts": prompts}, pretty)
            return 0

        if args.command == "respond":
            from agent_repl.execution.runner import respond_to_prompt
            path = _resolve_path_arg(args)
            source = _read_source_argument(args.source, args.source_file)
            _print(respond_to_prompt(server, path=path, prompt_cell_id=args.prompt_cell_id, source=source, cell_type=args.cell_type, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, strip_media=not args.raw_output), pretty)
            return 0

        if args.command == "watch":
            from agent_repl.watch import watch_for_prompts
            path = _resolve_path_arg(args)
            for prompt in watch_for_prompts(server, path=path, interval=args.interval, once=args.once, context_cells=args.context, timeout=args.timeout):
                print(json.dumps(prompt, separators=(",", ":"), sort_keys=True), flush=True)
            return 0

        if args.command == "context":
            from agent_repl.execution.context import build_execution_context
            path = _resolve_path_arg(args)
            _print(build_execution_context(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, include_outputs=args.include_outputs), pretty)
            return 0

        if args.command == "clean":
            from agent_repl.git import clean_notebook
            from agent_repl.notebook.io import load_notebook_model
            path = _resolve_path_arg(args)
            model = load_notebook_model(server, path, timeout=args.timeout)
            cleaned = clean_notebook(model["content"])
            print(json.dumps(cleaned, indent=1, sort_keys=True))
            return 0

        if args.command in ("contents", "cat"):
            from agent_repl.notebook import get_contents
            from agent_repl.notebook.contents import parse_cell_ranges
            path = _resolve_path_arg(args)
            cell_indexes = None
            if args.cells:
                # Defer range resolution — we need cell count from the notebook
                # parse_cell_ranges will be called inside get_contents or here with a pre-load
                from agent_repl.notebook.io import load_notebook_model as _load
                _m = _load(server, path, timeout=args.timeout)
                max_cells = len((_m.get("content") or {}).get("cells", []))
                cell_indexes = parse_cell_ranges(args.cells, max_cells)
            detail = "full" if args.include_outputs else args.detail
            _print(get_contents(server, path, detail=detail, raw=args.raw, timeout=args.timeout, cell_indexes=cell_indexes, cell_type_filter=args.cell_type, strip_media=not args.raw_output), pretty)
            return 0

        if args.command == "new":
            from agent_repl.notebook import create_notebook
            path = _resolve_path_arg(args)
            cells_defs = None
            if args.cells_json:
                cells_defs = json.loads(args.cells_json)
            elif args.cells_file:
                cells_defs = json.loads(Path(args.cells_file).read_text(encoding="utf-8"))
            _print(create_notebook(server, path=path, kernel_name=args.kernel_name, cells=cells_defs, timeout=args.timeout, start_kernel=not args.no_start_kernel), pretty)
            return 0

        if args.command in ("execute", "exec"):
            from agent_repl.execution import execute_code
            from agent_repl.notebook import load_notebook_model
            path = _resolve_path_arg(args, required=False)
            if args.code is None and args.code_file is None and args.cell_id and path:
                model = load_notebook_model(server, path)
                matched_source = next((c.get("source", "") for c in model["content"].get("cells", []) if c.get("id") == args.cell_id), None)
                if matched_source is None:
                    raise CommandError(f"Cell id {args.cell_id!r} not found in {path!r}")
                code = matched_source
            else:
                code = _read_code_argument(args.code, args.code_file)

            if args.stream:
                from agent_repl.execution.streaming import execute_streaming
                from agent_repl.execution.runner import resolve_kernel_target
                target = resolve_kernel_target(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, timeout=args.timeout)
                for event in execute_streaming(server, kernel_id=target.kernel_id, session_id=target.session_id, code=code, timeout=args.timeout, strip_media=not args.raw_output):
                    print(json.dumps(event, separators=(",", ":"), sort_keys=True), flush=True)
                return 0

            _print(execute_code(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, code=code, transport=args.transport, timeout=args.timeout, save_outputs=(args.save_outputs or bool(args.cell_id)) and not args.no_save_outputs, cell_id=args.cell_id, strip_media=not args.raw_output), pretty)
            return 0

        if args.command in ("insert-execute", "ix"):
            from agent_repl.execution import insert_and_execute
            path = _resolve_path_arg(args)
            source = _read_source_argument(args.source, args.source_file)
            _print(insert_and_execute(server, path=path, cell_type=args.cell_type, source=source, at_index=args.at_index, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, strip_media=not args.raw_output), pretty)
            return 0

        if args.command == "restart":
            from agent_repl.execution import restart_kernel
            path = _resolve_path_arg(args, required=False)
            _print(restart_kernel(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, timeout=args.timeout), pretty)
            return 0

        if args.command == "run-all":
            from agent_repl.execution import run_all_cells
            path = _resolve_path_arg(args, required=False)
            skip_tags = {t.strip() for t in args.skip_tags.split(",")} if args.skip_tags else None
            only_tags = {t.strip() for t in args.only_tags.split(",")} if args.only_tags else None
            result = run_all_cells(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, save_outputs=args.save_outputs, strip_media=not args.raw_output, skip_tags=skip_tags, only_tags=only_tags)
            _print(result, pretty)
            return 0 if result.get("status") == "ok" else 1

        if args.command == "restart-run-all":
            from agent_repl.execution import restart_and_run_all
            path = _resolve_path_arg(args, required=False)
            skip_tags = {t.strip() for t in args.skip_tags.split(",")} if args.skip_tags else None
            only_tags = {t.strip() for t in args.only_tags.split(",")} if args.only_tags else None
            result = restart_and_run_all(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, save_outputs=args.save_outputs, strip_media=not args.raw_output, skip_tags=skip_tags, only_tags=only_tags)
            _print(result, pretty)
            return 0 if (result.get("run_all") or {}).get("status") == "ok" else 1

        if args.command == "edit":
            from agent_repl.notebook import batch_edit, clear_cell_outputs, delete_cell, edit_cell_source, insert_cell, move_cell
            path = _resolve_path_arg(args)

            if args.edit_command == "replace-source":
                result = edit_cell_source(server, path=path, index=args.index, cell_id=args.cell_id, source=_read_source_argument(args.source, args.source_file), timeout=args.timeout)
            elif args.edit_command == "insert":
                result = insert_cell(server, path=path, cell_type=args.cell_type, source=_read_source_argument(args.source, args.source_file), at_index=_resolve_insert_index(args), timeout=args.timeout)
            elif args.edit_command == "delete":
                result = delete_cell(server, path=path, index=args.index, cell_id=args.cell_id, timeout=args.timeout)
            elif args.edit_command == "move":
                result = move_cell(server, path=path, index=args.index, cell_id=args.cell_id, to_index=args.to_index, timeout=args.timeout)
            elif args.edit_command == "clear-outputs":
                result = clear_cell_outputs(server, path=path, index=args.index, cell_id=args.cell_id, all_cells=args.all, timeout=args.timeout)
            elif args.edit_command == "batch":
                ops_text = _read_text_argument(args.operations, args.operations_file, "operations")
                result = batch_edit(server, path=path, operations=json.loads(ops_text), timeout=args.timeout)
            else:
                raise CommandError(f"Unknown edit command {args.edit_command!r}.")

            _print(result, pretty)
            return 0

        if args.command in ("variables", "vars"):
            from agent_repl.execution import list_variables, preview_variable
            path = _resolve_path_arg(args, required=False)

            if args.variables_command == "list":
                result = list_variables(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, limit=args.limit, include_private=args.include_private, include_callables=args.include_callables)
            elif args.variables_command == "preview":
                result = preview_variable(server, path=path, session_id=args.session_id, kernel_id=args.kernel_id, transport=args.transport, timeout=args.timeout, name=args.name, max_chars=args.max_chars)
            else:
                raise CommandError(f"Unknown variables command {args.variables_command!r}.")

            _print(result, pretty)
            return 0

    except CommandError as exc:
        print(json.dumps({"error": _sanitize_error_text(str(exc), server_token=server_token)}, indent=2), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"error": _sanitize_error_text(f"Unexpected error: {exc}", server_token=server_token)}, indent=2), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


def main_entry() -> None:
    """Entry point for console_scripts."""
    raise SystemExit(main())

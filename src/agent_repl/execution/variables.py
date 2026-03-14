"""Variable inspection for live Python kernels."""
from __future__ import annotations

import ast
from typing import Any

from agent_repl.core import CommandError, ServerInfo, ExecuteRequest
from agent_repl.execution.runner import _execute_request, resolve_kernel_target


def _bounded_positive_int(value: int, *, name: str, maximum: int) -> int:
    if value < 1:
        raise CommandError(f"{name} must be at least 1.")
    if value > maximum:
        raise CommandError(f"{name} must be at most {maximum}.")
    return value


def _parse_text_plain_literal(value: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _user_expression_value(reply: dict[str, Any], name: str) -> Any:
    user_expressions = reply.get("user_expressions") or {}
    expression_result = user_expressions.get(name)
    if not expression_result:
        raise CommandError(f"Kernel did not return a value for user expression {name!r}.")
    if expression_result.get("status") != "ok":
        raise CommandError(f"User expression {name!r} failed: {expression_result}")
    data = expression_result.get("data") or {}
    if "application/json" in data:
        return data["application/json"]
    if "text/plain" in data:
        return _parse_text_plain_literal(data["text/plain"])
    return expression_result


def _ensure_python_kernel(target: Any, feature: str) -> None:
    if target.kernel_name and "python" in target.kernel_name.lower():
        return
    raise CommandError(f"{feature} currently supports Python kernels only. Resolved kernel: {target.kernel_name!r}.")


def _python_variable_list_expression(*, limit: int, include_private: bool, include_callables: bool) -> str:
    private_filter = "True" if include_private else "not name.startswith('_')"
    callable_filter = "True" if include_callables else "not callable(value)"
    excluded = ("In", "Out", "exit", "quit", "get_ipython")
    return (
        "[{'name': name, 'type': type(value).__name__, 'module': type(value).__module__} "
        "for name, value in sorted(globals().items()) "
        f"if name not in {excluded!r} and ({private_filter}) and ({callable_filter}) "
        f"and type(value).__name__ != 'module'][:{limit}]"
    )


def _python_variable_preview_expression(name: str, *, max_chars: int) -> str:
    return (
        "(lambda _name, _limit: None if _name not in globals() else "
        "(lambda _value, _simple: {'name': _name, 'type': type(_value).__name__, 'module': type(_value).__module__, "
        "'preview': (_value[:_limit] if isinstance(_value, str) else "
        "_value.decode('utf-8', 'replace')[:_limit] if isinstance(_value, bytes) else "
        "_value if isinstance(_value, (type(None), bool, int, float)) else "
        "repr(_value)[:_limit] if isinstance(_value, complex) else "
        "{'kind': type(_value).__name__, 'length': len(_value), 'items': [_simple(item) for item in list(_value)[:5]]} if isinstance(_value, (list, tuple, set)) else "
        "{'kind': 'dict', 'length': len(_value), 'items': [{'key': _simple(key), 'value': _simple(value)} for key, value in list(_value.items())[:5]]} if isinstance(_value, dict) else "
        "f'<{type(_value).__module__}.{type(_value).__name__}>')})"
        "(globals()[_name], lambda _item: (_item[:_limit] if isinstance(_item, str) else "
        "_item.decode('utf-8', 'replace')[:_limit] if isinstance(_item, bytes) else "
        "_item if isinstance(_item, (type(None), bool, int, float)) else "
        "repr(_item)[:_limit] if isinstance(_item, complex) else "
        "f'<{type(_item).__module__}.{type(_item).__name__}>')))(%r, %d)" % (name, max_chars)
    )


def list_variables(
    server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None,
    transport: str, timeout: float, limit: int, include_private: bool, include_callables: bool,
) -> dict[str, Any]:
    limit = _bounded_positive_int(limit, name="limit", maximum=100)
    target = resolve_kernel_target(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    _ensure_python_kernel(target, "Variable listing")
    result = _execute_request(
        server, path=target.path, session_id=target.session_id, kernel_id=target.kernel_id,
        request=ExecuteRequest(code="", silent=True, store_history=False, user_expressions={
            "codex_variables": _python_variable_list_expression(limit=limit, include_private=include_private, include_callables=include_callables),
        }),
        transport=transport, timeout=timeout,
    ).as_dict()
    variables = _user_expression_value(result.get("reply") or {}, "codex_variables")
    return {
        "operation": "variables-list", "kernel_id": target.kernel_id, "kernel_name": target.kernel_name,
        "session_id": target.session_id, "path": target.path, "transport": result.get("transport"),
        "limit": limit, "variables": variables,
    }


def preview_variable(
    server: ServerInfo, *, path: str | None, session_id: str | None, kernel_id: str | None,
    transport: str, timeout: float, name: str, max_chars: int,
) -> dict[str, Any]:
    if not name.isidentifier():
        raise CommandError(f"Variable name {name!r} is not a valid Python identifier.")
    max_chars = _bounded_positive_int(max_chars, name="max-chars", maximum=2000)
    target = resolve_kernel_target(server, path=path, session_id=session_id, kernel_id=kernel_id, timeout=timeout)
    _ensure_python_kernel(target, "Variable preview")
    result = _execute_request(
        server, path=target.path, session_id=target.session_id, kernel_id=target.kernel_id,
        request=ExecuteRequest(code="", silent=True, store_history=False, user_expressions={
            "codex_variable": _python_variable_preview_expression(name, max_chars=max_chars),
        }),
        transport=transport, timeout=timeout,
    ).as_dict()
    payload = _user_expression_value(result.get("reply") or {}, "codex_variable")
    if payload is None:
        raise CommandError(f"No live variable matched name {name!r}.")
    return {
        "operation": "variable-preview", "kernel_id": target.kernel_id, "kernel_name": target.kernel_name,
        "session_id": target.session_id, "path": target.path, "transport": result.get("transport"),
        "variable": payload,
    }

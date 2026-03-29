"""HTTP route helpers for runtime and run core APIs."""
from __future__ import annotations

from http import HTTPStatus
from typing import Any


def handle_runtime_get(state: Any, path: str) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/runtimes":
        return HTTPStatus.OK, state.list_runtimes_payload()
    if path == "/api/runs":
        return HTTPStatus.OK, state.list_runs_payload()
    return None


def handle_runtime_post(
    state: Any,
    path: str,
    payload: dict[str, Any],
) -> tuple[HTTPStatus, dict[str, Any]] | None:
    if path == "/api/runtimes/start":
        runtime_id = payload.get("runtime_id")
        mode = payload.get("mode")
        label = payload.get("label")
        environment = payload.get("environment")
        document_path = payload.get("document_path")
        ttl_seconds = payload.get("ttl_seconds")
        if not isinstance(runtime_id, str) or not runtime_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"}
        if not isinstance(mode, str) or mode not in {"interactive", "shared", "headless", "pinned", "ephemeral"}:
            return HTTPStatus.BAD_REQUEST, {"error": "Invalid mode"}
        if document_path is not None and not isinstance(document_path, str):
            return HTTPStatus.BAD_REQUEST, {"error": "Invalid document_path"}
        if ttl_seconds is not None and not isinstance(ttl_seconds, int):
            return HTTPStatus.BAD_REQUEST, {"error": "Invalid ttl_seconds"}
        return HTTPStatus.OK, state.start_runtime(
            runtime_id=runtime_id,
            mode=mode,
            label=label if isinstance(label, str) else None,
            environment=environment if isinstance(environment, str) else None,
            document_path=document_path if isinstance(document_path, str) else None,
            ttl_seconds=ttl_seconds if isinstance(ttl_seconds, int) else None,
        )

    if path == "/api/runtimes/stop":
        runtime_id = payload.get("runtime_id")
        if not isinstance(runtime_id, str) or not runtime_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"}
        body, status = state.stop_runtime(runtime_id)
        return status, body

    if path == "/api/runtimes/recover":
        runtime_id = payload.get("runtime_id")
        if not isinstance(runtime_id, str) or not runtime_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"}
        body, status = state.recover_runtime(runtime_id)
        return status, body

    if path == "/api/runtimes/promote":
        runtime_id = payload.get("runtime_id")
        mode = payload.get("mode", "shared")
        if not isinstance(runtime_id, str) or not runtime_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"}
        if not isinstance(mode, str) or mode not in {"shared", "pinned"}:
            return HTTPStatus.BAD_REQUEST, {"error": "Invalid mode"}
        body, status = state.promote_runtime(runtime_id, mode=mode)
        return status, body

    if path == "/api/runtimes/discard":
        runtime_id = payload.get("runtime_id")
        if not isinstance(runtime_id, str) or not runtime_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"}
        body, status = state.discard_runtime(runtime_id)
        return status, body

    if path == "/api/runs/start":
        run_id = payload.get("run_id")
        runtime_id = payload.get("runtime_id")
        target_type = payload.get("target_type")
        target_ref = payload.get("target_ref")
        kind = payload.get("kind")
        if not isinstance(run_id, str) or not run_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing run_id"}
        if not isinstance(runtime_id, str) or not runtime_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing runtime_id"}
        if not isinstance(target_type, str) or target_type not in {"document", "node", "branch"}:
            return HTTPStatus.BAD_REQUEST, {"error": "Invalid target_type"}
        if not isinstance(target_ref, str) or not target_ref:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing target_ref"}
        if not isinstance(kind, str) or not kind:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing kind"}
        body, status = state.start_run(
            run_id=run_id,
            runtime_id=runtime_id,
            target_type=target_type,
            target_ref=target_ref,
            kind=kind,
        )
        return status, body

    if path == "/api/runs/finish":
        run_id = payload.get("run_id")
        run_status = payload.get("status")
        if not isinstance(run_id, str) or not run_id:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing run_id"}
        if not isinstance(run_status, str) or not run_status:
            return HTTPStatus.BAD_REQUEST, {"error": "Missing status"}
        body, status = state.finish_run(run_id, run_status)
        return status, body

    return None

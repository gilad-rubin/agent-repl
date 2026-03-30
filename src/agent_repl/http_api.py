"""Shared JSON-over-HTTP client helpers."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import requests


def json_error_payload(response: requests.Response) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    return payload if isinstance(payload, dict) else None


def json_error_message(response: requests.Response) -> str | None:
    payload = json_error_payload(response)
    if payload is None:
        return None

    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return None


class ApiError(RuntimeError):
    """Structured HTTP API failure that preserves JSON payload details."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        reason: str | None = None,
        url: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason
        self.url = url
        self.payload = payload or {}
        self.recovery = self.payload.get("recovery")

    def to_payload(self) -> dict[str, Any]:
        body = dict(self.payload)
        body.setdefault("error", str(self))
        if self.status_code is not None:
            body.setdefault("status_code", self.status_code)
        if self.reason:
            body.setdefault("reason_phrase", self.reason)
        if self.url:
            body.setdefault("url", self.url)
        return body


class JsonApiClient:
    """Base class for authenticated JSON-over-HTTP clients."""

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"token {token}"

    def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        response = self._session.get(
            f"{self.base_url}{endpoint}",
            params=params,
            timeout=timeout,
        )
        self._raise_for_status(response)
        return response.json()

    def _post(
        self,
        endpoint: str,
        body: dict[str, Any],
        *,
        timeout: float = 10,
    ) -> dict[str, Any]:
        response = self._session.post(
            f"{self.base_url}{endpoint}",
            json=body,
            timeout=timeout,
        )
        self._raise_for_status(response)
        return response.json()

    def _raise_for_status(self, response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            payload = json_error_payload(response)
            detail = json_error_message(response)
            status = getattr(response, "status_code", None)
            reason = getattr(response, "reason", "") or "HTTP error"
            url = getattr(response, "url", None)
            if detail:
                location = f" for url: {url}" if url else ""
                raise ApiError(
                    f"{status} {reason}{location}: {detail}",
                    status_code=status,
                    reason=reason,
                    url=url,
                    payload=payload,
                ) from exc
            if payload is not None:
                raise ApiError(
                    payload.get("message") or payload.get("error") or f"{status} {reason}",
                    status_code=status,
                    reason=reason,
                    url=url,
                    payload=payload,
                ) from exc
            raise


def poll_execution_until_complete(
    initial: dict[str, Any],
    *,
    timeout: float,
    fetch_execution: Callable[[str], dict[str, Any]],
    in_progress_statuses: set[str],
) -> dict[str, Any]:
    """Poll an execution endpoint until it reaches a terminal state or times out."""

    execution_id = initial["execution_id"]
    deadline = time.monotonic() + timeout
    interval = 0.2
    while time.monotonic() < deadline:
        time.sleep(interval)
        result = fetch_execution(execution_id)
        status = result.get("status")
        if status not in in_progress_statuses:
            for key in ("cell_id", "cell_index", "operation"):
                if key in initial and key not in result:
                    result[key] = initial[key]
            return result
        interval = min(interval * 1.5, 1.0)
    return {**initial, "status": "timeout", "timeout_seconds": timeout}

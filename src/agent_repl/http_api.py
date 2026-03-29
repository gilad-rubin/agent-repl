"""Shared JSON-over-HTTP client helpers."""
from __future__ import annotations

from typing import Any

import requests


def json_error_message(response: requests.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None

    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return None


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
            detail = json_error_message(response)
            if detail:
                status = getattr(response, "status_code", "HTTP error")
                reason = getattr(response, "reason", "") or "HTTP error"
                url = getattr(response, "url", None)
                location = f" for url: {url}" if url else ""
                raise RuntimeError(f"{status} {reason}{location}: {detail}") from exc
            raise

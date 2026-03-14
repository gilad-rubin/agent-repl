from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import requests

from agent_repl.core.errors import HTTPCommandError
from agent_repl.core.models import DEFAULT_TIMEOUT, ServerInfo


class ServerClient:
    def __init__(self, server: ServerInfo, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.server = server
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        if self.server.token:
            self.session.headers["Authorization"] = f"token {self.server.token}"
        self._primed = False

    def _prime(self) -> None:
        if self._primed:
            return
        params = {"token": self.server.token} if self.server.token else None
        try:
            self.session.get(self.server.root_url, params=params, timeout=self.timeout)
        except requests.RequestException:
            self._primed = True
            return
        xsrf = self.session.cookies.get("_xsrf")
        if xsrf:
            self.session.headers["X-XSRFToken"] = xsrf
        self._primed = True

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
        expect_json: bool = True,
    ) -> Any:
        self._prime()
        merged_params = dict(params or {})
        if self.server.token and "token" not in merged_params:
            merged_params["token"] = self.server.token
        url = urljoin(self.server.root_url, path.lstrip("/"))
        response = self.session.request(
            method,
            url,
            params=merged_params,
            json=payload,
            timeout=timeout or self.timeout,
        )
        if response.status_code >= 400:
            snippet = response.text[:400].strip()
            raise HTTPCommandError(
                f"{method} {url} failed with {response.status_code}: {snippet}",
                status_code=response.status_code,
            )
        if expect_json:
            return response.json()
        return response.text

    def websocket_headers(self) -> list[str]:
        self._prime()
        headers: list[str] = []
        if self.server.token:
            headers.append(f"Authorization: token {self.server.token}")
        cookies = self.session.cookies.get_dict()
        if cookies:
            headers.append("Cookie: " + "; ".join(f"{key}={value}" for key, value in cookies.items()))
        xsrf = cookies.get("_xsrf")
        if xsrf:
            headers.append(f"X-XSRFToken: {xsrf}")
        return headers

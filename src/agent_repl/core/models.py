from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

DEFAULT_TIMEOUT = 10.0
DEFAULT_EXEC_TIMEOUT = None


@dataclass
class ServerInfo:
    url: str
    base_url: str
    root_dir: str
    token: str
    pid: int | None = None
    port: int | None = None
    version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def root_url(self) -> str:
        base = (self.base_url or "/").strip("/")
        suffix = f"{base}/" if base else ""
        return urljoin(self.url if self.url.endswith("/") else f"{self.url}/", suffix)

    @property
    def ws_root_url(self) -> str:
        parts = urlparse(self.root_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return urlunparse((scheme, parts.netloc, parts.path, "", "", ""))

    def summary(self) -> dict[str, Any]:
        return {
            "url": self.root_url,
            "port": self.port,
            "pid": self.pid,
            "version": self.version,
            "root_dir": self.root_dir,
            "token_present": bool(self.token),
        }


@dataclass
class ProbeResult:
    reachable: bool
    auth_ok: bool
    error: str | None = None
    sessions_count: int | None = None
    lab_workspaces_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "auth_ok": self.auth_ok,
            "error": self.error,
            "sessions_count": self.sessions_count,
            "lab_workspaces_available": self.lab_workspaces_available,
        }


@dataclass
class ExecutionResult:
    transport: str
    kernel_id: str
    session_id: str | None
    path: str | None
    reply: dict[str, Any]
    events: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "transport": self.transport,
            "kernel_id": self.kernel_id,
            "session_id": self.session_id,
            "path": self.path,
            "reply": self.reply,
            "events": self.events,
            "status": self.reply.get("status"),
        }


@dataclass
class KernelTarget:
    kernel_id: str
    kernel_name: str | None
    session_id: str | None
    path: str | None


@dataclass
class ExecuteRequest:
    code: str
    silent: bool = False
    store_history: bool = True
    user_expressions: dict[str, str] = field(default_factory=dict)
    stop_on_error: bool = True

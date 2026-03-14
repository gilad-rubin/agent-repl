"""Kernel model helpers and kernelspec listing."""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import requests

from agent_repl.core import CommandError, HTTPCommandError, ServerClient, ServerInfo, DEFAULT_TIMEOUT


def get_kernel_model(
    server: ServerInfo, kernel_id: str, timeout: float = DEFAULT_TIMEOUT, *, client: ServerClient | None = None,
) -> dict[str, Any]:
    client = client or ServerClient(server, timeout=timeout)
    return client.request("GET", f"api/kernels/{quote(kernel_id, safe='')}", timeout=timeout)


def wait_for_kernel_idle(server: ServerInfo, kernel_id: str, timeout: float) -> dict[str, Any]:
    client = ServerClient(server, timeout=timeout)
    deadline = time.time() + timeout
    last_state: str | None = None
    while time.time() < deadline:
        try:
            model = get_kernel_model(server, kernel_id, timeout=timeout, client=client)
        except HTTPCommandError as exc:
            if exc.status_code == 404:
                time.sleep(0.2)
                continue
            raise
        last_state = model.get("execution_state")
        if last_state == "idle":
            return model
        time.sleep(0.2)
    raise CommandError(f"Timed out waiting for kernel {kernel_id} to become idle after restart. Last state: {last_state!r}.")


def ensure_kernel_idle(server: ServerInfo, kernel_id: str, timeout: float) -> None:
    """Wait briefly for the kernel to be idle before attempting execution."""
    client = ServerClient(server, timeout=timeout)
    deadline = time.time() + min(timeout, 10.0)
    last_state: str | None = None
    while time.time() < deadline:
        try:
            model = get_kernel_model(server, kernel_id, timeout=timeout, client=client)
        except HTTPCommandError:
            time.sleep(0.2)
            continue
        last_state = model.get("execution_state")
        if last_state in {"idle", None}:
            return
        time.sleep(0.2)
    if last_state == "starting":
        return
    raise CommandError(f"Timed out waiting for kernel {kernel_id} to become idle before execution. Last state: {last_state!r}.")


def list_kernelspecs_and_running(server: ServerInfo, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch available kernelspecs and running kernels."""
    client = ServerClient(server, timeout=timeout)
    try:
        kernelspecs_data = client.request("GET", "api/kernelspecs", timeout=timeout)
    except (CommandError, requests.RequestException):
        kernelspecs_data = {}

    try:
        running_kernels = client.request("GET", "api/kernels", timeout=timeout)
    except (CommandError, requests.RequestException):
        running_kernels = []

    specs = []
    default_spec = kernelspecs_data.get("default", "")
    for name, spec_info in (kernelspecs_data.get("kernelspecs") or {}).items():
        spec = spec_info.get("spec") or {}
        specs.append({
            "name": name, "display_name": spec.get("display_name", name),
            "language": spec.get("language", ""), "is_default": name == default_spec,
        })

    running = [
        {
            "id": k.get("id"), "name": k.get("name"),
            "execution_state": k.get("execution_state"),
            "last_activity": k.get("last_activity"), "connections": k.get("connections"),
        }
        for k in running_kernels
    ]

    return {"server": server.summary(), "kernelspecs": specs, "running_kernels": running}

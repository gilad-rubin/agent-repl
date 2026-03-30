"""Structured recovery hints shared across user-facing surfaces."""
from __future__ import annotations

from typing import Any


def command(label: str, value: str) -> dict[str, str]:
    return {"label": label, "value": value}


def action(kind: str, label: str) -> dict[str, str]:
    return {"kind": kind, "label": label}


def recovery_payload(
    *,
    reason: str,
    summary: str,
    suggestions: list[str] | None = None,
    commands: list[dict[str, str]] | None = None,
    actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "reason": reason,
        "summary": summary,
    }
    if suggestions:
        payload["suggestions"] = suggestions
    if commands:
        payload["commands"] = commands
    if actions:
        payload["actions"] = actions
    return payload


def lease_conflict_recovery(*, has_suggested_branch: bool) -> dict[str, Any]:
    suggestions = [
        "Refresh the notebook surface to pull in the latest shared state from the active session.",
        "Retry after the other session finishes or its lease expires.",
    ]
    actions = [action("refresh-notebook", "Refresh notebook")]
    if has_suggested_branch:
        suggestions.append("If you need to continue immediately, start a conflict draft branch instead of taking over the active lease.")
        actions.append(action("start-conflict-branch", "Start conflict draft"))
    return recovery_payload(
        reason="lease-conflict",
        summary="Another active session currently owns the notebook region you tried to change.",
        suggestions=suggestions,
        actions=actions,
    )


def runtime_busy_recovery() -> dict[str, Any]:
    return recovery_payload(
        reason="runtime-busy",
        summary="The runtime is still busy finishing existing work.",
        suggestions=[
            "Wait for the current execution to finish, then retry.",
            "Refresh runtime status if the busy state looks stale.",
        ],
        actions=[action("refresh-notebook", "Refresh notebook")],
    )

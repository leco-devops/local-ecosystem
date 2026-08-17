"""Guard rails for agent-driven control of a real machine.

Two independent gates protect anything that destroys data:

1. ``confirm=True`` must be passed explicitly by the caller (an agent cannot wipe a volume
   as a side effect of a vaguer instruction), and
2. the server must have been started with ``LECO_MCP_ALLOW_DESTRUCTIVE=1``.

Credential-reading tools are gated separately by ``LECO_MCP_ALLOW_CREDENTIALS=1`` because
they return plaintext local-dev secrets from the UI credential vault.
"""

from __future__ import annotations

from .activity import note_blocked, note_destructive
from .config import Settings

# Control API actions that delete containers, volumes, or registry entries.
DESTRUCTIVE_CONTROL_ACTIONS = frozenset({"remove", "reset"})

# Dev-stack lifecycle actions that wipe a generated stack.
DESTRUCTIVE_STACK_ACTIONS = frozenset({"destroy", "reinstall"})

# Every action the dashboard Control API accepts (mirrors control.ALLOWED_ACTIONS).
CONTROL_ACTIONS = frozenset(
    {
        "start",
        "stop",
        "restart",
        "remove",
        "pause",
        "unpause",
        "deploy",
        "recreate",
        "reset",
        "backup",
        "staging",
    }
)

DEV_STACK_ACTIONS = frozenset({"start", "stop", "destroy", "repair", "reinstall", "redeploy"})

PLATFORM_SERVICE_ACTIONS = frozenset({"install", "start", "stop", "disable"})


class ActionBlocked(RuntimeError):
    """Raised when a guarded action is attempted without the required opt-ins."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        # "Refused by policy" is the interesting signal in the activity log, and the SDK
        # flattens every handler exception into an isError result before telemetry sees it.
        # Marking at construction is the one place that still knows which this is.
        note_blocked()


def _explain(what: str, why: str) -> str:
    return (
        f"Blocked destructive action: {what}. {why}\n"
        "To allow it: pass confirm=true AND start this MCP server with "
        "LECO_MCP_ALLOW_DESTRUCTIVE=1 (see docs/MCP_SERVER.md). "
        "Both gates are required by design."
    )


def is_destructive(action: str, *, stack: bool = False) -> bool:
    a = (action or "").strip().lower()
    pool = DESTRUCTIVE_STACK_ACTIONS if stack else DESTRUCTIVE_CONTROL_ACTIONS
    return a in pool


def guard_destructive(
    settings: Settings,
    action: str,
    confirm: bool,
    *,
    what: str,
    stack: bool = False,
) -> None:
    """Raise ``ActionBlocked`` unless both destructive gates are satisfied."""
    if not is_destructive(action, stack=stack):
        return
    note_destructive()
    if not settings.allow_destructive:
        raise ActionBlocked(
            _explain(what, "This server was started without LECO_MCP_ALLOW_DESTRUCTIVE=1.")
        )
    if not confirm:
        raise ActionBlocked(
            _explain(what, "The call did not pass confirm=true.")
        )


def guard_explicit_destructive(settings: Settings, confirm: bool, *, what: str) -> None:
    """Same gates for operations that are always destructive (offboard, teardown)."""
    note_destructive()
    if not settings.allow_destructive:
        raise ActionBlocked(
            _explain(what, "This server was started without LECO_MCP_ALLOW_DESTRUCTIVE=1.")
        )
    if not confirm:
        raise ActionBlocked(_explain(what, "The call did not pass confirm=true."))


def guard_credentials(settings: Settings) -> None:
    if not settings.allow_credentials:
        raise ActionBlocked(
            "Credential tools are disabled. These return plaintext local-dev credentials "
            "from the UI credential vault. Start this MCP server with "
            "LECO_MCP_ALLOW_CREDENTIALS=1 to enable them."
        )


def validate_action(action: str, allowed: frozenset[str], *, label: str) -> str:
    a = (action or "").strip().lower()
    if a not in allowed:
        raise ValueError(
            f"Unknown {label} action {action!r}. Allowed: {', '.join(sorted(allowed))}."
        )
    return a

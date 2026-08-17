"""The destructive-action gates are the only thing standing between an agent and a wiped
volume, so they get direct tests rather than relying on integration coverage."""

import pytest

from leco_mcp.config import Settings
from leco_mcp.safety import (
    ActionBlocked,
    CONTROL_ACTIONS,
    DEV_STACK_ACTIONS,
    guard_credentials,
    guard_destructive,
    guard_explicit_destructive,
    is_destructive,
    validate_action,
)

LOCKED = Settings(allow_destructive=False, allow_credentials=False)
UNLOCKED = Settings(allow_destructive=True, allow_credentials=True)


@pytest.mark.parametrize("action", ["start", "stop", "restart", "deploy", "recreate", "backup"])
def test_safe_actions_pass_both_gates(action):
    guard_destructive(LOCKED, action, confirm=False, what="test")
    assert is_destructive(action) is False


@pytest.mark.parametrize("action", ["remove", "reset"])
def test_destructive_blocked_when_server_locked(action):
    assert is_destructive(action) is True
    with pytest.raises(ActionBlocked, match="LECO_MCP_ALLOW_DESTRUCTIVE"):
        guard_destructive(LOCKED, action, confirm=True, what="test")


@pytest.mark.parametrize("action", ["remove", "reset"])
def test_destructive_blocked_without_confirm(action):
    with pytest.raises(ActionBlocked, match="confirm=true"):
        guard_destructive(UNLOCKED, action, confirm=False, what="test")


@pytest.mark.parametrize("action", ["remove", "reset"])
def test_destructive_allowed_with_both_gates(action):
    guard_destructive(UNLOCKED, action, confirm=True, what="test")


def test_dev_stack_destructive_set_differs_from_control():
    # destroy/reinstall are only destructive in the dev-stack namespace.
    assert is_destructive("destroy", stack=True) is True
    assert is_destructive("reinstall", stack=True) is True
    assert is_destructive("destroy", stack=False) is False
    assert is_destructive("repair", stack=True) is False


def test_dev_stack_repair_is_never_gated():
    guard_destructive(LOCKED, "repair", confirm=False, what="test", stack=True)


def test_always_destructive_needs_both_gates():
    with pytest.raises(ActionBlocked):
        guard_explicit_destructive(LOCKED, True, what="offboard")
    with pytest.raises(ActionBlocked):
        guard_explicit_destructive(UNLOCKED, False, what="offboard")
    guard_explicit_destructive(UNLOCKED, True, what="offboard")


def test_credentials_gate():
    with pytest.raises(ActionBlocked, match="LECO_MCP_ALLOW_CREDENTIALS"):
        guard_credentials(LOCKED)
    guard_credentials(UNLOCKED)


def test_blocked_messages_say_how_to_unblock():
    with pytest.raises(ActionBlocked) as exc:
        guard_destructive(LOCKED, "reset", confirm=True, what="reset target 'x'")
    msg = str(exc.value)
    assert "confirm=true" in msg
    assert "LECO_MCP_ALLOW_DESTRUCTIVE=1" in msg
    assert "reset target 'x'" in msg


def test_validate_action_rejects_unknown_and_normalizes_case():
    assert validate_action("  START ", CONTROL_ACTIONS, label="control") == "start"
    with pytest.raises(ValueError, match="Unknown control action"):
        validate_action("obliterate", CONTROL_ACTIONS, label="control")
    with pytest.raises(ValueError, match="Unknown dev stack action"):
        validate_action("deploy", DEV_STACK_ACTIONS, label="dev stack")


def test_control_action_set_matches_dashboard_allowed_actions():
    # Mirrors control.ALLOWED_ACTIONS in dashboard/control.py — drift here means the MCP
    # server would reject an action the dashboard accepts (or vice versa).
    assert CONTROL_ACTIONS == {
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

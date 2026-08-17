"""
One broken app slot must not hide every healthy one.

Regression for a real outage: `hosting/app-available/utility-server-edge/source` pointed at a
path the dashboard container could stat but not resolve, `resolve_app_root` raised
`OSError(EINVAL)`, nothing caught it, and `GET /api/hosted-apps` answered 500 — so botfeed and
raven vanished from the dashboard too, though nothing was wrong with either.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import leco_control  # noqa: E402


@pytest.mark.parametrize(
    "exc",
    [
        OSError(22, "Invalid argument"),  # stale bind-mount view of a new symlink
        OSError(2, "No such file or directory"),  # checkout moved or unmounted
        OSError(40, "Too many levels of symbolic links"),  # symlink loop
    ],
)
def test_unresolvable_app_is_skipped_not_raised(monkeypatch, caplog, exc):
    def boom(_mp):
        raise exc

    monkeypatch.setattr(leco_control, "parse_leco_manifest_for_compose", boom)
    monkeypatch.setattr(leco_control, "parse_leco_effective_manifest_for_compose", boom)
    monkeypatch.setattr(leco_control, "_compose_meta_worker_only", boom)

    with caplog.at_level("WARNING"):
        meta = leco_control._leco_meta_from_resolved_manifest(
            "/project/hosting/app-available/broken/leco.app.yaml", "broken", "Broken"
        )

    assert meta is None, "an unresolvable app must be skipped, not surfaced as a half-built row"
    # Skipping silently is the other half of this bug: the app disappears and nobody knows why.
    assert "broken" in caplog.text
    assert str(exc.strerror) in caplog.text or str(exc.args[-1]) in caplog.text


def test_healthy_app_still_resolves(monkeypatch):
    monkeypatch.setattr(
        leco_control,
        "parse_leco_manifest_for_compose",
        lambda _mp: {"root": "/src", "compose_tail": ["-f", "docker-compose.yml"]},
    )

    meta = leco_control._leco_meta_from_resolved_manifest("/m/leco.app.yaml", "ok", "OK")

    assert meta is not None
    assert meta["leco_slug"] == "ok"
    assert meta["root"] == "/src"


def test_first_parser_failing_does_not_mask_a_working_fallback(monkeypatch):
    """A raising primary parser must not skip the app when a fallback could have answered.

    The three parsers are a fallback chain, so the `or` chain is inside one try block. That is
    the right shape only if a *raising* first parser is genuinely fatal for the whole chain —
    it is, because all three walk the same `root:` symlink, and EINVAL on that link fails every
    one of them. This test pins the behaviour that a first parser returning None (rather than
    raising) still lets the fallback run.
    """
    monkeypatch.setattr(leco_control, "parse_leco_manifest_for_compose", lambda _mp: None)
    monkeypatch.setattr(
        leco_control,
        "parse_leco_effective_manifest_for_compose",
        lambda _mp: {"root": "/fallback", "compose_tail": []},
    )

    meta = leco_control._leco_meta_from_resolved_manifest("/m/leco.app.yaml", "app", "App")

    assert meta is not None and meta["root"] == "/fallback"


def test_real_slot_resolves_when_workspace_parent_is_mounted():
    """The actual utility-server-edge slot, if this checkout has one.

    Skips rather than fails off the dashboard container, where `/workspace-parent` does not
    exist — the symlink is deliberately written in *container* coordinates, matching raven.
    """
    slot = Path(__file__).resolve().parents[2] / "hosting/app-available/utility-server-edge/source"
    if not slot.is_symlink():
        pytest.skip("utility-server-edge slot not present in this checkout")
    target = os.readlink(slot)
    assert target.startswith("/workspace-parent/"), (
        f"source must point into the container mount like raven's does, got {target!r}; "
        "a host-absolute path resolves outside every mount and the app becomes invisible"
    )

"""Dev stack destroy lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stacks import _prune_devstack_project, destroy_stack  # noqa: E402


def test_prune_devstack_project_removes_containers_and_volumes():
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:3] == ["docker", "ps", "-aq"]:
            return MagicMock(returncode=0, stdout="c1\nc2\n", stderr="")
        if cmd[:2] == ["docker", "rm"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "volume", "ls"] and "--filter" in cmd:
            return MagicMock(returncode=0, stdout="vol1\n", stderr="")
        if cmd[:3] == ["docker", "volume", "ls"]:
            return MagicMock(returncode=0, stdout="leco-devstack-demo_extra\n", stderr="")
        if cmd[:2] == ["docker", "volume"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "network", "ls"]:
            return MagicMock(returncode=0, stdout="net1\n", stderr="")
        if cmd[:2] == ["docker", "network"]:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("dev_stacks.subprocess.run", side_effect=fake_run):
        out = _prune_devstack_project("leco-devstack-demo")

    assert "Removed 2 container(s)" in out
    assert "Removed 2 volume(s)" in out
    assert "Removed 1 network(s)" in out
    assert ["docker", "rm", "-f", "c1", "c2"] in calls


def test_destroy_stack_keeps_files_when_compose_down_fails(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    stack_dir = stacks_root / "demo"
    stack_dir.mkdir(parents=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (stack_dir / "stack.yaml").write_text("id: demo\n", encoding="utf-8")

    monkeypatch.setattr("dev_stacks.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr(
        "dev_stacks._compose_cmd",
        lambda *_a, **_k: (1, "compose down failed"),
    )
    monkeypatch.setattr("dev_stacks._prune_devstack_project", lambda *_a, **_k: "")
    monkeypatch.setattr(
        "dev_stacks.load_platform_config",
        lambda: {"dev_stacks": [{"id": "demo", "name": "Demo"}]},
    )
    saved: dict = {}

    def _save(cfg):
        saved["cfg"] = cfg

    monkeypatch.setattr("dev_stacks.save_platform_config", _save)
    monkeypatch.setattr("dev_stack_routes.sync_dev_stack_routes", lambda *_a, **_k: {"ok": True})

    result = destroy_stack("demo")

    assert result["ok"] is False
    assert stack_dir.is_dir()
    assert saved == {}

"""Dev stack config paths and safe file I/O."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_config import (  # noqa: E402
    read_stack_file,
    stack_config_info,
    write_stack_file,
)


def test_stack_config_info_lists_files(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    sid = "demo"
    stack_dir = stacks_root / sid
    stack_dir.mkdir(parents=True)
    (stack_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (stack_dir / "stack.yaml").write_text(
        yaml.safe_dump({"id": sid, "template": "wordpress"}),
        encoding="utf-8",
    )
    (stack_dir / "varnish").mkdir()
    (stack_dir / "varnish" / "default.vcl").write_text("vcl 4.1;\n", encoding="utf-8")

    monkeypatch.setattr("dev_stack_config.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)

    info = stack_config_info(sid)
    paths = {f["path"] for f in info["files"]}
    assert "docker-compose.yml" in paths
    assert "stack.yaml" in paths
    assert "varnish/default.vcl" in paths
    assert info["stack_dir"] == "platform/dev-stacks/demo"
    assert any(r["id"] == "traefik-routes" for r in info["related_files"])


def test_write_rejects_path_traversal(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    sid = "demo"
    stack_dir = stacks_root / sid
    stack_dir.mkdir(parents=True)
    (stack_dir / "stack.yaml").write_text("id: demo\n", encoding="utf-8")

    monkeypatch.setattr("dev_stack_config.STACKS_ROOT", stacks_root)

    with pytest.raises(ValueError):
        write_stack_file(sid, "../escape.yml", "x")


def test_read_write_roundtrip(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    sid = "demo"
    stack_dir = stacks_root / sid
    stack_dir.mkdir(parents=True)
    (stack_dir / "stack.yaml").write_text("id: demo\n", encoding="utf-8")

    monkeypatch.setattr("dev_stack_config.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.TRAEFIK_DEVSTACKS_FILE", tmp_path / "20-dev-stacks.yml")

    write_stack_file(sid, "notes.txt", "hello")
    out = read_stack_file(sid, "notes.txt")
    assert out["content"] == "hello"

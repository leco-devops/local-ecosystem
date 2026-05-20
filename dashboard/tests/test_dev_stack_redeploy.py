"""Dev stack repair, reinstall, and file regeneration."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_redeploy import (  # noqa: E402
    apply_stack_config_updates,
    regenerate_stack_files,
    repair_stack,
)
from dev_stack_templates import create_from_preset  # noqa: E402


def _patch_stack_roots(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_redeploy.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)
    monkeypatch.setattr(
        "dev_stack_routes.TRAEFIK_DEVSTACKS_FILE",
        tmp_path / "hosting" / "traefik" / "20-dev-stacks.yml",
    )
    return stacks_root


def test_regenerate_magento_full_reverts_compose(tmp_path, monkeypatch):
    stacks_root = _patch_stack_roots(tmp_path, monkeypatch)
    create_from_preset("magento-full", stack_id="mag", sample_data=False)
    compose_path = stacks_root / "mag" / "docker-compose.yml"
    raw = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    raw["services"]["mariadb"]["image"] = "broken:db"
    compose_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    import dev_stacks

    monkeypatch.setattr(dev_stacks, "_docker_ps_state", lambda _sid: "stopped")

    _, logs = regenerate_stack_files("mag")
    fixed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert "bitnamilegacy/mariadb:10.6" in fixed["services"]["mariadb"]["image"]
    assert "broken:db" not in fixed["services"]["mariadb"]["image"]
    assert any("template" in ln.lower() for ln in logs)


def test_repair_keeps_manual_image_but_fixes_deprecated(tmp_path, monkeypatch):
    stacks_root = _patch_stack_roots(tmp_path, monkeypatch)
    create_from_preset("magento-full", stack_id="mag", sample_data=False)
    compose_path = stacks_root / "mag" / "docker-compose.yml"
    raw = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    raw["services"]["mariadb"]["image"] = "broken:db"
    raw["services"]["magento"]["image"] = "docker.io/bitnami/magento:2"
    compose_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    import dev_stacks

    monkeypatch.setattr(dev_stacks, "_docker_ps_state", lambda _sid: "stopped")
    monkeypatch.setattr(dev_stacks, "_compose_cmd", lambda _sid, *args: (0, ""))
    monkeypatch.setattr(dev_stacks, "repair_stack_lh_network", lambda _sid: [])

    result = repair_stack("mag")
    assert result["ok"] is True
    fixed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert fixed["services"]["mariadb"]["image"] == "broken:db"
    assert "bitnamilegacy/magento-archived" in fixed["services"]["magento"]["image"]


def test_apply_stack_config_updates_without_compose_up(tmp_path, monkeypatch):
    stacks_root = _patch_stack_roots(tmp_path, monkeypatch)
    create_from_preset("wordpress", stack_id="wp", sample_data=False)
    compose_path = stacks_root / "wp" / "docker-compose.yml"
    raw = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    raw["services"]["wordpress"]["environment"] = {"CUSTOM": "1"}
    compose_path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    import dev_stacks

    monkeypatch.setattr(dev_stacks, "_docker_ps_state", lambda _sid: "stopped")
    monkeypatch.setattr(dev_stacks, "repair_stack_lh_network", lambda _sid: [])

    logs = apply_stack_config_updates("wp")
    fixed = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    assert fixed["services"]["wordpress"]["environment"]["CUSTOM"] == "1"
    assert any("traefik" in ln.lower() for ln in logs)

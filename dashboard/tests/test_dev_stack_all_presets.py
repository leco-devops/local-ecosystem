"""Regression: every ready preset can be created and exposes access metadata."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_access import stack_access_info, template_http_backend  # noqa: E402
from dev_stack_templates import load_dev_stack_presets  # noqa: E402
from dev_stacks import create_stack  # noqa: E402


def _ready_presets() -> list[str]:
    out: list[str] = []
    for key, row in (load_dev_stack_presets().get("presets") or {}).items():
        if isinstance(row, dict) and row.get("template"):
            out.append(key)
    return sorted(out)


def test_all_ready_presets_create_and_access(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_app_urls.STACKS_ROOT", stacks_root)

    for preset in _ready_presets():
        sid = f"t-{preset}"[:32]
        result = create_stack(sid, f"Test {preset}", preset=preset, sample_data=True)
        assert result.get("ok") is True, f"{preset}: {result.get('error')}"
        access = result.get("stack", {}).get("access") or stack_access_info(sid)
        assert access.get("hostname"), f"{preset}: missing hostname in access"
        assert access.get("base_url"), f"{preset}: missing base_url"
        tpl = str(access.get("template") or "")
        assert tpl, f"{preset}: missing template in access"
        backend = template_http_backend(tpl, sid)
        assert backend is not None, f"{preset}: no Traefik backend for {tpl}"
        compose_path = stacks_root / sid / "docker-compose.yml"
        assert compose_path.is_file(), f"{preset}: compose not written"
        raw = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        assert isinstance(raw.get("services"), dict) and raw["services"], f"{preset}: empty services"
        net = access.get("networking") or {}
        assert net.get("nodes"), f"{preset}: missing networking.nodes"
        assert access.get("quick_links"), f"{preset}: missing quick_links"


def test_magento_full_networking_layers(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)

    create_stack("mag-full", "M", preset="magento-full", sample_data=False)
    access = stack_access_info("mag-full")
    layers = (access.get("networking") or {}).get("layers") or []
    assert len(layers) >= 2
    assert "magento" in layers[0]
    assert "mariadb" in layers[1]


def test_magento_access_note_uses_base_url(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)

    create_stack("mag-min", "M", preset="magento-min", sample_data=False)
    access = stack_access_info("mag-min")
    base = access["base_url"]
    assert any(base in str(n) for n in access.get("notes") or []), "Magento note should cite base_url"

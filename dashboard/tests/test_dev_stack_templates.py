"""Dev stack preset catalog and template generation."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_templates import (  # noqa: E402
    create_from_preset,
    load_dev_stack_presets,
    preset_catalog_for_api,
)


def test_preset_catalog_has_ready_stacks():
    cat = preset_catalog_for_api()
    presets = cat.get("presets") or {}
    assert "wordpress" in presets
    assert presets["wordpress"].get("supports_sample_data") is True
    assert "level1" in presets
    assert len(presets) >= 10


def test_create_wordpress_template(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    path, meta = create_from_preset("wordpress", sample_data=True)
    assert path.is_file()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "wordpress" in raw["services"]
    assert "db" in raw["services"]
    assert meta["template"] == "wordpress"
    assert meta["sample_data"] is True


def test_magento_presets_and_templates(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    presets = preset_catalog_for_api().get("presets") or {}
    assert "magento-min" in presets
    assert "magento-full" in presets
    assert "elasticsearch" in presets

    path_min, meta_min = create_from_preset("magento-min", sample_data=False)
    raw_min = yaml.safe_load(path_min.read_text(encoding="utf-8"))
    assert "magento" in raw_min["services"]
    assert "mariadb" in raw_min["services"]
    assert "elasticsearch" not in raw_min["services"]
    assert meta_min["template"] == "magento-min"

    path_full, meta_full = create_from_preset("magento-full", sample_data=False)
    raw_full = yaml.safe_load(path_full.read_text(encoding="utf-8"))
    for svc in ("elasticsearch", "redis", "varnish", "edge", "magento", "mariadb"):
        assert svc in raw_full["services"]
    assert meta_full["template"] == "magento-full"
    assert (stacks_root / "magento-full" / "varnish" / "default.vcl").is_file()

    path_es, meta_es = create_from_preset("elasticsearch", sample_data=False)
    raw_es = yaml.safe_load(path_es.read_text(encoding="utf-8"))
    assert "elasticsearch" in raw_es["services"]
    assert meta_es["template"] == "elasticsearch"

    path_ghost, _ = create_from_preset("ghost", sample_data=False)
    ghost_env = yaml.safe_load(path_ghost.read_text(encoding="utf-8"))["services"]["ghost"]["environment"]
    assert ghost_env["url"] == "http://ghost.lh"

    path_mag, _ = create_from_preset("magento-min", sample_data=False)
    mag_env = yaml.safe_load(path_mag.read_text(encoding="utf-8"))["services"]["magento"]["environment"]
    assert mag_env["MAGENTO_HOST"] == "magento-min.lh"

"""Framework dev-stack template generation."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_frameworks import FRAMEWORK_TEMPLATE_IDS  # noqa: E402
from dev_stack_templates import generate_from_template, load_dev_stack_presets  # noqa: E402


def test_framework_presets_registered():
    presets = load_dev_stack_presets().get("presets") or {}
    for tpl in sorted(FRAMEWORK_TEMPLATE_IDS):
        assert tpl in presets, f"missing preset for framework template {tpl}"
        assert presets[tpl].get("group") == "frameworks"


def test_framework_templates_write_compose(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)

    for tpl in sorted(FRAMEWORK_TEMPLATE_IDS):
        sid = f"fw-{tpl}"[:32]
        generate_from_template(sid, f"Test {tpl}", tpl, sample_data=False)
        compose_path = stacks_root / sid / "docker-compose.yml"
        assert compose_path.is_file(), tpl
        raw = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = raw.get("services") or {}
        assert "app" in services, f"{tpl}: missing app service"
        app = services["app"]
        nets = app.get("networks") or []
        assert "lh-network" in nets, f"{tpl}: app not on lh-network"

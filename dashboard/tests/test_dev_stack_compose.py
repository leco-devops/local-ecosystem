"""Dev stack compose generation."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_compose import generate_compose, _slugify  # noqa: E402


def test_slugify():
    assert _slugify("Billing Team") == "billing-team"


def test_generate_compose_structure(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    path, meta = generate_compose(
        "billing",
        "Billing",
        [{"id": "redis", "version": "7"}],
    )
    assert path.is_file()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "services" in raw
    assert "redis" in raw["services"]
    assert meta["id"] == "billing"
    assert "leco-devstack-billing-internal" in raw["networks"]

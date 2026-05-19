"""Dev stack platform binding on manifests."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_binding import read_platform_binding, set_platform_dev_stack  # noqa: E402


def test_set_and_read_dev_stack_id(tmp_path):
    staging = tmp_path / "hosting" / "app-available" / "demo"
    staging.mkdir(parents=True)
    manifest = staging / "leco.app.yaml"
    manifest.write_text(
        yaml.safe_dump({"name": "demo", "localHostProfile": "leco.yaml"}),
        encoding="utf-8",
    )
    (staging / "leco.yaml").write_text("schemaVersion: 1\narchetype: generic\n", encoding="utf-8")
    r = set_platform_dev_stack(str(manifest), "billing")
    assert r.get("ok") is True
    assert read_platform_binding(str(manifest))["dev_stack_id"] == "billing"
    r2 = set_platform_dev_stack(str(manifest), "")
    assert r2.get("ok") is True
    assert read_platform_binding(str(manifest))["dev_stack_id"] == ""

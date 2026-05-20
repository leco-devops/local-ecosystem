"""Dev stack image registry and preflight."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_images import (  # noqa: E402
    IMAGE_REWRITES,
    KNOWN_UNAVAILABLE,
    MAGENTO_APP_IMAGE,
    check_image_available,
    normalize_compose_images,
    upgrade_magento_full_bind_mounts,
    verify_compose_images,
)
from dev_stack_templates import create_from_preset  # noqa: E402


def test_magento_image_is_legacy_archive():
    assert "bitnamilegacy/magento-archived" in MAGENTO_APP_IMAGE
    assert "bitnami/magento:2" in KNOWN_UNAVAILABLE


def test_nginx_compose_config_needs_fix_detects_unescaped_vars():
    from dev_stack_images import _nginx_compose_config_needs_fix

    assert _nginx_compose_config_needs_fix(
        {"configs": {"nginx_edge_conf": {"content": "proxy_set_header Host $host;\n"}}}
    )
    assert not _nginx_compose_config_needs_fix(
        {"configs": {"nginx_edge_conf": {"content": "proxy_set_header Host $$host;\n"}}}
    )


def test_upgrade_magento_full_bind_mounts_to_configs():
    compose = {
        "services": {
            "varnish": {
                "image": "varnish:7.4",
                "volumes": ["./varnish/default.vcl:/etc/varnish/default.vcl:ro"],
            },
            "edge": {
                "image": "nginx:alpine",
                "volumes": ["./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro"],
            },
        }
    }
    updated, logs = upgrade_magento_full_bind_mounts(compose)
    assert logs
    assert "varnish_vcl" in updated["configs"]
    assert "configs" in updated["services"]["varnish"]
    assert "volumes" not in updated["services"]["varnish"]
    assert "volumes" not in updated["services"]["edge"]


def test_normalize_rewrites_deprecated_magento():
    compose = {
        "services": {
            "magento": {"image": "docker.io/bitnami/magento:2"},
            "db": {"image": "docker.io/bitnami/mariadb:latest"},
        }
    }
    updated, logs = normalize_compose_images(compose)
    assert "bitnamilegacy/magento-archived" in updated["services"]["magento"]["image"]
    assert logs


def test_verify_compose_catches_known_bad_without_registry():
    compose = {"services": {"app": {"image": "bitnami/magento:2"}}}
    errs = verify_compose_images(compose, skip_registry=True)
    assert errs and "deprecated" in errs[0].lower()


def test_all_ready_presets_use_available_images(tmp_path, monkeypatch):
    stacks_root = tmp_path / "platform" / "dev-stacks"
    monkeypatch.setattr("dev_stack_compose.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_templates.STACKS_ROOT", stacks_root)
    monkeypatch.setattr("dev_stack_routes.STACKS_ROOT", stacks_root)

    from dev_stack_templates import load_dev_stack_presets

    for key, row in (load_dev_stack_presets().get("presets") or {}).items():
        if not isinstance(row, dict) or not row.get("template"):
            continue
        path, _ = create_from_preset(key, stack_id=f"img-{key}"[:24], sample_data=False)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        for img in [s.get("image") for s in raw["services"].values() if isinstance(s, dict)]:
            assert "bitnami/magento" not in str(img)
        errs = verify_compose_images(raw, skip_registry=True)
        assert not errs, f"{key}: {errs}"


def test_bitnamilegacy_magento_manifest_available():
    ok, detail = check_image_available(MAGENTO_APP_IMAGE, timeout=30)
    assert ok, detail

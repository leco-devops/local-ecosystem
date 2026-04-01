"""Tests for wrangler.toml → LocalCfResourcePlan (stdlib unittest, no pytest required)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from leco_app.wrangler_cf_resources import parse_wrangler_cf_resources


class TestWranglerCfResources(unittest.TestCase):
    def test_preview_id_when_id_missing(self) -> None:
        toml = """
name = "app"
[[kv_namespaces]]
binding = "CACHE"
preview_id = "abc123def456"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            p = Path(f.name)
        try:
            plan = parse_wrangler_cf_resources(p, None)
            self.assertEqual(len(plan.kv), 1)
            self.assertEqual(plan.kv[0].binding, "CACHE")
            self.assertEqual(plan.kv[0].cf_id, "abc123def456")
        finally:
            p.unlink(missing_ok=True)

    def test_env_overrides_top_kv_binding(self) -> None:
        toml = """
name = "app"
[[kv_namespaces]]
binding = "A"
id = "top-a-id-1111111111111111"

[[kv_namespaces]]
binding = "B"
id = "top-b-id-2222222222222222"

[env.staging]

[[env.staging.kv_namespaces]]
binding = "A"
id = "staging-a-id-3333333333333333"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            p = Path(f.name)
        try:
            plan = parse_wrangler_cf_resources(p, "staging")
            bindings = {r.binding: r.cf_id for r in plan.kv}
            self.assertEqual(bindings["A"], "staging-a-id-3333333333333333")
            self.assertEqual(bindings["B"], "top-b-id-2222222222222222")
        finally:
            p.unlink(missing_ok=True)

    def test_top_only_when_wrangler_env_none(self) -> None:
        toml = """
[[kv_namespaces]]
binding = "X"
id = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

[env.staging]
[[env.staging.kv_namespaces]]
binding = "Y"
id = "yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
"""
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(toml)
            p = Path(f.name)
        try:
            plan = parse_wrangler_cf_resources(p, None)
            self.assertEqual(len(plan.kv), 1)
            self.assertEqual(plan.kv[0].binding, "X")
        finally:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

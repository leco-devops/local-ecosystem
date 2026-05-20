"""Tests for local CF provision policy (no PyYAML / pydantic import)."""

from __future__ import annotations

import unittest

from leco_app.local_cf_policy import evaluate_local_cf_provision_policy


class TestLocalCfPolicy(unittest.TestCase):
    def test_cli_skip(self) -> None:
        self.assertFalse(
            evaluate_local_cf_provision_policy(
                cli_skip=True,
                leco_provision_local_cf_env="",
                wrangler_config_nonempty=True,
                manifest_allows_provision=True,
            )
        )

    def test_env_disable(self) -> None:
        for v in ("0", "false", "no", "off"):
            with self.subTest(v=v):
                self.assertFalse(
                    evaluate_local_cf_provision_policy(
                        cli_skip=False,
                        leco_provision_local_cf_env=v,
                        wrangler_config_nonempty=True,
                        manifest_allows_provision=True,
                    )
                )

    def test_no_wrangler(self) -> None:
        self.assertFalse(
            evaluate_local_cf_provision_policy(
                cli_skip=False,
                leco_provision_local_cf_env="",
                wrangler_config_nonempty=False,
                manifest_allows_provision=True,
            )
        )

    def test_manifest_opt_out(self) -> None:
        self.assertFalse(
            evaluate_local_cf_provision_policy(
                cli_skip=False,
                leco_provision_local_cf_env="",
                wrangler_config_nonempty=True,
                manifest_allows_provision=False,
            )
        )

    def test_default_yes(self) -> None:
        self.assertTrue(
            evaluate_local_cf_provision_policy(
                cli_skip=False,
                leco_provision_local_cf_env="",
                wrangler_config_nonempty=True,
                manifest_allows_provision=True,
            )
        )


if __name__ == "__main__":
    unittest.main()

"""When to run local KV/R2/D1 provisioning (manifest + env + CLI)."""

from __future__ import annotations

import os

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leco_app.schema import ApplicationManifest


def evaluate_local_cf_provision_policy(
    *,
    cli_skip: bool,
    leco_provision_local_cf_env: str,
    wrangler_config_nonempty: bool,
    manifest_allows_provision: bool,
) -> bool:
    """
    Pure policy (no manifest import). Precedence toward skip:

    1. ``cli_skip`` → False
    2. ``LECO_PROVISION_LOCAL_CF`` in ``0``, ``false``, ``no``, ``off`` → False
    3. No wrangler config path → False
    4. ``manifest_allows_provision`` False → False
    5. Else True
    """
    if cli_skip:
        return False
    raw = (leco_provision_local_cf_env or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if not wrangler_config_nonempty:
        return False
    if not manifest_allows_provision:
        return False
    return True


def should_provision_local_cf(manifest: ApplicationManifest, *, cli_skip: bool) -> bool:
    """Read manifest + ``os.environ`` and apply :func:`evaluate_local_cf_provision_policy`."""
    cf = manifest.cloudflare
    wrangler_ok = bool(cf and (cf.wrangler_config or "").strip())
    allows = True
    if cf is not None and cf.provision_local_resources is False:
        allows = False
    return evaluate_local_cf_provision_policy(
        cli_skip=cli_skip,
        leco_provision_local_cf_env=os.environ.get("LECO_PROVISION_LOCAL_CF", ""),
        wrangler_config_nonempty=wrangler_ok,
        manifest_allows_provision=allows,
    )

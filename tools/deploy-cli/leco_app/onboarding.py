"""Register app in leco-registry + optional Traefik dynamic.yml merge (shared by onboard / init / ecosystem-register)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import typer

from leco_app.ecosystem_registry import register_in_ecosystem
from leco_app.local_cf_provision import provision_from_manifest
from leco_app.schema import ApplicationManifest
from leco_app.traefik_dynamic_merge import merge_manifest_routing_into_dynamic_yml

Echo = Callable[..., None]


def resolve_ecosystem_root(explicit: Path | None) -> Path | None:
    """Return resolved ecosystem root from explicit path or LECO_ECOSYSTEM_ROOT."""
    if explicit is not None:
        p = explicit.expanduser().resolve()
        return p if p.is_dir() else None
    import os

    raw = (os.environ.get("LECO_ECOSYSTEM_ROOT") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser().resolve()
    return p if p.is_dir() else None


def run_registry_and_provision(
    ecosystem_root: Path,
    manifest_path: Path,
    *,
    app_id: str | None,
    label: str | None,
    wrangler_env: str | None,
    no_provision_local_cf: bool,
    echo: Echo,
) -> dict[str, Any]:
    entry = register_in_ecosystem(
        ecosystem_root.resolve(),
        manifest_path.resolve(),
        app_id=app_id,
        label=label,
    )
    echo(
        f"Registered {entry['id']} → {ecosystem_root / 'config' / 'leco-registry.yaml'} "
        f"(Control target leco-stack-{entry['id']})",
        fg=typer.colors.GREEN,
    )
    code = provision_from_manifest(
        manifest_path.resolve(),
        app_slug=entry["id"],
        wrangler_env=wrangler_env,
        echo=echo,
        no_provision_local_cf=no_provision_local_cf,
    )
    if code != 0:
        echo(
            "Local CF provision had failures — registry entry is still saved. "
            "Fix adapters/DNS and run: leco-app provision-local-cf",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return entry


def run_traefik_merge_for_manifest(
    manifest: ApplicationManifest,
    *,
    ecosystem_root: Path | None,
    traefik_dynamic: Path | None,
    echo: Echo,
) -> None:
    """Merge routing into traefik/dynamic.yml if path resolves."""
    if traefik_dynamic is not None:
        tf = traefik_dynamic.expanduser().resolve()
    elif ecosystem_root is not None:
        tf = (ecosystem_root / "traefik" / "dynamic.yml").resolve()
    else:
        echo("Traefik merge skipped (no --traefik-dynamic and no ecosystem root).", fg=typer.colors.YELLOW, err=True)
        return

    ok, msg = merge_manifest_routing_into_dynamic_yml(manifest, tf)
    if ok and "skipped" in msg.lower():
        echo(msg)
    elif ok:
        echo(msg, fg=typer.colors.GREEN)
    else:
        echo(msg, fg=typer.colors.YELLOW, err=True)

"""Dashboard bridge for hosted-app seed data import."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

_DASH = Path(__file__).resolve().parent
_CLI_ROOT = _DASH.parent / "tools" / "deploy-cli"


def _purge_leco_app_modules() -> None:
    """Drop cached leco_app so mounted tools/deploy-cli wins over image site-packages."""
    for key in list(sys.modules):
        if key == "leco_app" or key.startswith("leco_app."):
            del sys.modules[key]


def _ensure_deploy_cli_on_path() -> None:
    root = str(_CLI_ROOT.resolve())
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_data_import():
    _purge_leco_app_modules()
    _ensure_deploy_cli_on_path()
    plan = importlib.import_module("leco_app.data_import.plan")
    orch = importlib.import_module("leco_app.data_import.orchestrator")
    return plan, orch


_plan_mod, _orch_mod = _load_data_import()
build_import_plan = _plan_mod.build_import_plan
discover_data_import = _orch_mod.discover_data_import
run_import_plan_stream = _orch_mod.run_import_plan_stream


def _load_services(manifest_path: str, compose_tail: list[str] | None) -> dict[str, dict[str, Any]]:
    try:
        from hosted_app_services import load_merged_compose_services

        return load_merged_compose_services(
            Path(manifest_path).resolve(),
            compose_tail=compose_tail,
        )
    except Exception:
        return {}


def data_import_summary_for_slug(
    slug: str,
    *,
    manifest_path: str,
    compose_tail: list[str] | None = None,
) -> dict[str, Any]:
    """Discovery block for hosted app snapshot."""
    mp = Path(manifest_path).resolve()
    services = _load_services(manifest_path, compose_tail)
    return discover_data_import(mp, services=services)


def iterate_data_import_stream(
    slug: str,
    *,
    manifest_path: str,
    compose_tail: list[str] | None,
    compose_root: str,
    compose_ps: list[dict[str, Any]] | None,
    reimport: bool = False,
    dry_run: bool = False,
    selected_ids: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    mp = Path(manifest_path).resolve()
    services = _load_services(manifest_path, compose_tail)
    yield from run_import_plan_stream(
        mp,
        compose_root=Path(compose_root).resolve(),
        compose_tail=compose_tail or [],
        services=services,
        compose_ps=compose_ps or [],
        reimport=reimport,
        dry_run=dry_run,
        selected_ids=selected_ids,
    )

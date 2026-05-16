"""Hosted app seed data import — plan, discovery, and importers."""

from __future__ import annotations

__all__ = [
    "build_import_plan",
    "discover_data_import",
    "run_import_plan_stream",
]


def __getattr__(name: str):
    if name == "build_import_plan":
        from leco_app.data_import.plan import build_import_plan

        return build_import_plan
    if name == "discover_data_import":
        from leco_app.data_import.orchestrator import discover_data_import

        return discover_data_import
    if name == "run_import_plan_stream":
        from leco_app.data_import.orchestrator import run_import_plan_stream

        return run_import_plan_stream
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""LEco local edge-runtime adapter registry.

Every entry under ``infrastructure.runtimes[]`` in a localhost profile picks
one of these adapters by ``type``. The registry is intentionally tiny and
declarative — adding a new runtime is one new file plus one line below.

The public surface is:

- :data:`REGISTRY` — ``type → RuntimeAdapter`` mapping.
- :func:`get_adapter` — convenience lookup with a clear error message.
- :func:`detect_runtimes` — read-only scan of an app root that yields candidate
  specs for the registration wizard.

Each module under this package implements one adapter; see :mod:`.base` for
the contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .aws_lambda import AwsLambdaAdapter
from .base import (  # re-export
    AdapterNotReady,
    RuntimeAdapter,
    RuntimeBuildContext,
    RuntimeDetection,
)
from .cloudflare_pages import CloudflarePagesAdapter
from .cloudflare_workers import CloudflareWorkersAdapter
from .deno_deploy import DenoDeployAdapter
from .vercel import VercelAdapter


def _build_registry() -> dict[str, RuntimeAdapter]:
    adapters: list[RuntimeAdapter] = [
        CloudflareWorkersAdapter(),
        CloudflarePagesAdapter(),
        VercelAdapter(),
        AwsLambdaAdapter(),
        DenoDeployAdapter(),
    ]
    out: dict[str, RuntimeAdapter] = {}
    for a in adapters:
        if not a.type:
            continue
        out[a.type] = a
    return out


REGISTRY: dict[str, RuntimeAdapter] = _build_registry()


def get_adapter(runtime_type: str) -> RuntimeAdapter:
    """Look up an adapter by type. Raises ``KeyError`` with a friendly message."""
    a = REGISTRY.get(str(runtime_type or "").strip())
    if a is None:
        known = ", ".join(sorted(REGISTRY)) or "(none)"
        raise KeyError(f"Unknown runtime type: {runtime_type!r}. Known: {known}")
    return a


def detect_runtimes(app_root: Path) -> list[RuntimeDetection]:
    """Run every adapter's read-only scan and return all matches."""
    if not isinstance(app_root, Path):
        app_root = Path(str(app_root))
    out: list[RuntimeDetection] = []
    for a in REGISTRY.values():
        try:
            detect_all = getattr(a, "detect_all", None)
            if callable(detect_all):
                hits = detect_all(app_root)
            else:
                hit = a.detect(app_root)
                hits = [hit] if hit is not None else []
        except Exception:
            hits = []
        for hit in hits:
            if hit is not None:
                out.append(hit)
    return out


def list_adapters() -> Iterable[tuple[str, str, str]]:
    """``(type, label, roadmap_note)`` triples for diagnostic UIs and CLI output."""
    for t, a in sorted(REGISTRY.items()):
        yield t, a.label, a.roadmap


__all__ = [
    "AdapterNotReady",
    "REGISTRY",
    "RuntimeAdapter",
    "RuntimeBuildContext",
    "RuntimeDetection",
    "detect_runtimes",
    "get_adapter",
    "list_adapters",
]

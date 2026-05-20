"""Vercel local runtime adapter — roadmap stub.

Will run ``vercel dev`` against the upstream project. Bindings (KV/Blob/Edge
Config) map to Vercel-local in-memory stubs or LEco adapters as the design
firms up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AdapterNotReady, RuntimeAdapter, RuntimeBuildContext, RuntimeDetection


class VercelAdapter(RuntimeAdapter):
    type = "vercel"
    label = "Vercel (vercel dev)"
    roadmap = (
        "Planned image: leco/runtime-vercel. Will mount the upstream project and run "
        "`vercel dev --listen 0.0.0.0:<port>` with a per-app token file kept under "
        "hosting/app-available/<slug>/."
    )

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        return None

    def compose_service(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> dict[str, Any]:
        raise AdapterNotReady(
            "vercel adapter is on the roadmap. Track infra/runtimes/vercel/ for status."
        )

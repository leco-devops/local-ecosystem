"""Deno Deploy local runtime adapter — roadmap stub.

Will run ``deno serve`` against the upstream entry, with Deno KV mapped to
LEco's KV adapter where possible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AdapterNotReady, RuntimeAdapter, RuntimeBuildContext, RuntimeDetection


class DenoDeployAdapter(RuntimeAdapter):
    type = "deno-deploy"
    label = "Deno Deploy (deno serve)"
    roadmap = (
        "Planned image: leco/runtime-deno-deploy. Will run `deno serve` with optional KV-on-LEco "
        "wiring via DENO_KV_URL → kv.lh adapter."
    )

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        return None

    def compose_service(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> dict[str, Any]:
        raise AdapterNotReady(
            "deno-deploy adapter is on the roadmap. Track infra/runtimes/deno-deploy/ for status."
        )

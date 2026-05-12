"""Cloudflare Pages local runtime adapter — roadmap stub.

Will run Pages + Functions via ``wrangler pages dev`` against the upstream
``functions/`` directory (or a ``_routes.json``-driven build). Implementation
notes live in ``infra/runtimes/cloudflare-pages/README.md`` once that arrives.

Until then, declaring ``type: cloudflare-pages`` is allowed by the schema so
operators can lay out their manifest early; overlay materialization raises
:class:`AdapterNotReady` with a clear pointer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import AdapterNotReady, RuntimeAdapter, RuntimeBuildContext, RuntimeDetection


class CloudflarePagesAdapter(RuntimeAdapter):
    type = "cloudflare-pages"
    label = "Cloudflare Pages (functions/ + wrangler pages dev)"
    roadmap = (
        "Planned image: leco/runtime-cloudflare-pages. Will reuse the cloudflare-workers "
        "Wrangler entrypoint but invoke `wrangler pages dev` against the upstream Pages "
        "project (functions/ + static output)."
    )

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        # We could match `functions/` directory plus `_routes.json`, but
        # auto-detecting Pages projects is brittle (some teams keep a
        # `wrangler.toml` that targets both). Leave detection off until the
        # full adapter lands; the wizard can still propose the type manually.
        return None

    def compose_service(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> dict[str, Any]:
        raise AdapterNotReady(
            "cloudflare-pages adapter is on the roadmap. Use cloudflare-workers for now if "
            "your project is Worker-only, or wait for infra/runtimes/cloudflare-pages/."
        )

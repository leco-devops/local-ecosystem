"""Local edge-runtime adapter interface.

LEco materializes one Docker Compose service per :class:`LocalRuntimeSpec` entry
listed under ``infrastructure.runtimes`` on the localhost profile. Each adapter
owns:

1. **Detection** — given an upstream app source root, optionally surface a
   candidate :class:`LocalRuntimeSpec` so the onboarding wizard can pre-fill the
   runtime block (e.g. ``wrangler.toml`` → ``cloudflare-workers``).

2. **Materialization** — given a resolved :class:`LocalRuntimeSpec` plus a
   :class:`RuntimeBuildContext` (manifest path, app slug, mount paths), return a
   compose service dict that LEco merges into
   ``docker-compose.leco-runtime.yml`` beside ``leco.app.yaml``.

3. **Routing target** — return the ``host:port`` LEco's Traefik fragment uses
   when ``routing.entries[].upstream[].target == "runtime"``.

No adapter touches the upstream application tree. All write-back goes into
LEco-owned named volumes or files under ``hosting/app-available/<slug>/``.
This file is the only contract; concrete adapters live next to it.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RuntimeDetection:
    """Result of an adapter's read-only scan of an app source root."""

    type: str
    runtime_id: str
    spec: dict[str, Any]
    """Manifest-shaped dict suitable for ``infrastructure.runtimes[]`` (alias keys)."""
    detail: str = ""
    """Short human-readable explanation (shown in the registration wizard)."""
    suggested_upstream_yaml: str = ""
    """Optional copy-pasteable ``routing.entries[].upstream`` YAML fragment for
    a manifest file. Empty when the adapter cannot infer prefixes."""


@dataclass(frozen=True)
class RuntimeBuildContext:
    """All paths and identifiers an adapter needs to render its compose service.

    ``manifest_dir`` / ``manifest_root`` are absolute **host** paths (so the
    Docker daemon can use them as bind sources). The ``*_container`` siblings
    are the same locations as visible to the process generating the overlay
    (typically the dashboard container) — adapters use these when they need to
    *read* files (e.g. parse an upstream ``wrangler.toml``) before emitting the
    compose service.
    """

    app_slug: str
    manifest_dir: Path
    """Directory containing ``leco.app.yaml`` (host path, for bind-mount sources)."""
    manifest_root: Path
    """Resolved app root, post-``source`` symlink (host path, for bind-mount sources)."""
    manifest_dir_container: Path | None = None
    """Same as ``manifest_dir`` but visible to the running process (read access)."""
    manifest_root_container: Path | None = None
    """Same as ``manifest_root`` but visible to the running process (read access)."""

    def runtime_container(self, runtime_id: str) -> str:
        """Stable Docker DNS name for the runtime container (Traefik upstream)."""
        # Keep ids short: lh-network DNS truncates long names confusingly in `docker ps`.
        slug = _safe_dns_label(self.app_slug)
        rid = _safe_dns_label(runtime_id)
        return f"leco-rt-{slug}-{rid}"

    def named_volume(self, runtime_id: str, suffix: str) -> str:
        """Per-runtime named volume name (e.g. node-modules, wrangler-state)."""
        return f"leco-rt-{_safe_dns_label(self.app_slug)}-{_safe_dns_label(runtime_id)}-{_safe_dns_label(suffix)}"


class RuntimeAdapter(abc.ABC):
    """Base class every runtime adapter implements.

    Stub adapters override :meth:`compose_service` to raise
    :class:`AdapterNotReady` so callers can render a clear "roadmap" message
    in UI without blowing up overlay generation for unrelated apps.
    """

    #: Adapter type as it appears in :class:`LocalRuntimeSpec.type` / YAML.
    type: str = ""
    #: Human label for the registration wizard and ``leco-app runtimes`` output.
    label: str = ""
    #: One-line roadmap note for adapters that aren't fully implemented yet.
    roadmap: str = ""

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        """Read-only probe of the upstream tree. Default: no auto-detection."""
        return None

    @abc.abstractmethod
    def compose_service(
        self,
        spec: dict[str, Any],
        ctx: RuntimeBuildContext,
    ) -> dict[str, Any]:
        """Return a single compose-service dict (the value under ``services.<name>``).

        LEco wraps it in ``docker-compose.leco-runtime.yml`` with the container name
        from :meth:`RuntimeBuildContext.runtime_container` and ``lh-network`` attached.
        """

    def runtime_endpoint(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> tuple[str, int]:
        """``(host, port)`` Traefik should forward ``target: runtime`` rules to."""
        port = int(spec.get("port") or 8787)
        return ctx.runtime_container(str(spec.get("id") or "runtime")), port

    def named_volumes(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> list[str]:
        """Per-runtime named volumes the overlay must declare under top-level ``volumes:``.

        Default: empty. Adapters that mask upstream-generated dirs (e.g.
        ``node_modules``) advertise them here so the overlay writer can list them.
        """
        return []


class AdapterNotReady(NotImplementedError):
    """Raised by stub adapters to signal "type known, implementation pending"."""


# ---------------------------------------------------------------------------

_DNS_RE = re.compile(r"[^a-z0-9-]+")


def _safe_dns_label(s: str) -> str:
    t = _DNS_RE.sub("-", (s or "").strip().lower()).strip("-")
    if not t:
        return "x"
    return t[:40]

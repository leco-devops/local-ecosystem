"""Cloudflare Pages local runtime adapter (``wrangler pages dev``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from leco_wrangler_paths import (
    list_wrangler_pages_config_files,
    pages_runtime_id_from_config,
    read_pages_build_output_dir,
    resolve_pages_asset_dir,
)

from .base import RuntimeAdapter, RuntimeBuildContext, RuntimeDetection

DEFAULT_IMAGE = "leco/runtime-cloudflare-pages:latest"
DEFAULT_PORT = 8791


class CloudflarePagesAdapter(RuntimeAdapter):
    type = "cloudflare-pages"
    label = "Cloudflare Pages (static + wrangler pages dev)"
    roadmap = ""

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        hits = self.detect_all(app_root)
        return hits[0] if hits else None

    def detect_all(self, app_root: Path) -> list[RuntimeDetection]:
        if not app_root.is_dir():
            return []
        out: list[RuntimeDetection] = []
        port = DEFAULT_PORT
        for rel in list_wrangler_pages_config_files(app_root):
            path = (app_root / rel).resolve()
            if not path.is_file():
                continue
            runtime_id = pages_runtime_id_from_config(rel)
            out_rel = read_pages_build_output_dir(path)
            asset_hint = out_rel or "(pages_build_output_dir in config)"
            spec: dict[str, Any] = {
                "id": runtime_id,
                "type": self.type,
                "config": rel.as_posix(),
                "port": port,
            }
            if out_rel:
                spec["pagesBuildOutputDir"] = out_rel
            detail = f"Found {rel.as_posix()}; static output → {asset_hint}"
            yaml_hint = (
                f"      - prefix: /\n"
                f"        target: runtime\n"
                f"        runtime: {runtime_id}"
            )
            out.append(
                RuntimeDetection(
                    type=self.type,
                    runtime_id=runtime_id,
                    spec=spec,
                    detail=detail,
                    suggested_upstream_yaml=yaml_hint,
                )
            )
            port += 1
        return out

    def compose_service(
        self,
        spec: dict[str, Any],
        ctx: RuntimeBuildContext,
    ) -> dict[str, Any]:
        runtime_id = str(spec.get("id") or "dashboard")
        port = int(spec.get("port") or DEFAULT_PORT)
        image = str(spec.get("image") or DEFAULT_IMAGE).strip() or DEFAULT_IMAGE
        source_dir = (spec.get("sourceDir") or "").strip()
        if source_dir:
            host_source = (ctx.manifest_root / source_dir).resolve()
            container_source = (
                (ctx.manifest_root_container / source_dir).resolve()
                if ctx.manifest_root_container is not None
                else None
            )
        else:
            host_source = ctx.manifest_root.resolve()
            container_source = (
                ctx.manifest_root_container.resolve()
                if ctx.manifest_root_container is not None
                else None
            )

        config_rel = (spec.get("config") or "wrangler.pages.toml").strip() or "wrangler.pages.toml"
        environment: dict[str, str] = {
            "LECO_APP_SLUG": ctx.app_slug,
            "LECO_RUNTIME_ID": runtime_id,
            "LECO_PORT": str(port),
            "LECO_WRANGLER_CONFIG": f"/app/{config_rel}",
        }

        pages_out = (spec.get("pagesBuildOutputDir") or spec.get("pages_build_output_dir") or "").strip()
        if not pages_out and container_source is not None:
            pages_out = read_pages_build_output_dir((container_source / config_rel).resolve()) or ""
        if pages_out and container_source is not None:
            asset = resolve_pages_asset_dir(container_source, Path(config_rel))
            if asset is not None:
                try:
                    rel_asset = asset.relative_to(container_source.resolve())
                    environment["LECO_PAGES_ASSET_DIR"] = f"/app/{rel_asset.as_posix()}"
                except ValueError:
                    environment["LECO_PAGES_ASSET_DIR"] = str(asset)

        node_modules_vol = ctx.named_volume(runtime_id, "node-modules")
        wrangler_vol = ctx.named_volume(runtime_id, "wrangler-state")

        volumes: list[dict[str, Any]] = [
            {"type": "bind", "source": str(host_source), "target": "/app"},
            {"type": "volume", "source": node_modules_vol, "target": "/app/node_modules"},
            {"type": "volume", "source": wrangler_vol, "target": "/app/.wrangler"},
        ]

        return {
            "image": image,
            "build": {
                "context": "${LECO_ECOSYSTEM_ROOT:-/project}/infra/runtimes/cloudflare-pages",
            },
            "container_name": ctx.runtime_container(runtime_id),
            "restart": "unless-stopped",
            "working_dir": "/app",
            "environment": environment,
            "volumes": volumes,
            "networks": ["default", "lh-network"],
            "expose": [str(port)],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    f"wget -qO- --spider http://127.0.0.1:{port}/ >/dev/null 2>&1 || exit 1",
                ],
                "interval": "15s",
                "timeout": "3s",
                "retries": 4,
                "start_period": "120s",
            },
        }

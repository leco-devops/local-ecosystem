"""Pydantic schema for leco.app.yaml manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DockerComposeSpec(BaseModel):
    compose_file: str = Field(default="docker-compose.yml", alias="composeFile")
    project_name: str | None = Field(default=None, alias="projectName")
    profiles: list[str] = Field(default_factory=list)
    env_file: str | None = Field(default=None, alias="envFile")

    model_config = {"populate_by_name": True}


class CloudflareSpec(BaseModel):
    wrangler_config: str | None = Field(default=None, alias="wranglerConfig")
    wrangler_env: str | None = Field(default=None, alias="wranglerEnv")

    model_config = {"populate_by_name": True}


class ServiceTarget(BaseModel):
    """Docker DNS name (container or compose service) on lh-network + container port."""

    model_config = ConfigDict(populate_by_name=True)

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


class RoutingEntry(BaseModel):
    """Traefik file-provider fragment.

    **Legacy:** single `backendHost` + `backendPort` → one service for the hostname.

    **Split (recommended for React + API):** `frontend` + `apiBackend` + `apiPathPrefix`
    → higher-priority `Host && PathPrefix` routers to the API, catch-all `Host` to the UI
    (same pattern as local-ecosystem apps behind Traefik).
    """

    model_config = ConfigDict(populate_by_name=True)

    hostname: str = Field(min_length=1)
    api_path_prefix: str = Field(default="/api", alias="apiPathPrefix")
    frontend: ServiceTarget | None = None
    api_backend: ServiceTarget | None = Field(None, alias="apiBackend")
    backend_host: str = Field(default="", alias="backendHost")
    backend_port: int = Field(default=8080, alias="backendPort", ge=1, le=65535)

    @field_validator("api_path_prefix")
    @classmethod
    def normalize_api_prefix(cls, v: str) -> str:
        p = (v or "/api").strip()
        if not p.startswith("/"):
            raise ValueError("apiPathPrefix must start with /")
        return p

    @model_validator(mode="after")
    def routing_shape(self) -> RoutingEntry:
        split = self.frontend is not None and self.api_backend is not None
        legacy = bool(self.backend_host and self.backend_host.strip())
        if split and legacy:
            raise ValueError("routing entry: use either (frontend + apiBackend) or backendHost, not both")
        if not split and not legacy:
            raise ValueError("routing entry: set non-empty backendHost or both frontend and apiBackend")
        return self


class RoutingSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entries: list[RoutingEntry] = Field(default_factory=list)


class TraefikCleanupSpec(BaseModel):
    """Explicit router/service keys in traefik/dynamic.yml to remove on offload (optional).

    Use when you renamed keys while merging (leco defaults use ``{name}-{hostslug}-…``).
    If omitted, keys are derived from ``routing`` the same way as ``traefik-fragment``.
    """

    model_config = ConfigDict(populate_by_name=True)

    routers: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)


class ApplicationManifest(BaseModel):
    """Single logical package per application (see README resource model)."""

    model_config = ConfigDict(populate_by_name=True)

    leco_app_version: str = Field(default="1", alias="lecoAppVersion")
    name: str = Field(..., min_length=1, description="Short slug for state directory")
    root: str = Field(default=".", description="App root relative to manifest location or absolute")

    docker_compose: DockerComposeSpec | None = Field(default=None, alias="dockerCompose")
    cloudflare: CloudflareSpec | None = Field(default=None)
    routing: RoutingSpec | None = None
    traefik_cleanup: TraefikCleanupSpec | None = Field(default=None, alias="traefikCleanup")
    healthcheck_urls: list[str] = Field(default_factory=list, alias="healthcheckUrls")

    def resolved_root(self, manifest_path: Path) -> Path:
        r = Path(self.root)
        if r.is_absolute():
            return r.resolve()
        return (manifest_path.parent / r).resolve()


def load_manifest(path: Path) -> ApplicationManifest:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a YAML mapping")
    return ApplicationManifest.model_validate(data)


def save_manifest(path: Path, manifest: ApplicationManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Dump with aliases for camelCase file format
    d = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    path.write_text(
        yaml.safe_dump(d, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def manifest_from_dict(data: dict[str, Any]) -> ApplicationManifest:
    return ApplicationManifest.model_validate(data)

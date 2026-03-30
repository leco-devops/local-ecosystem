"""Pydantic schema for leco.app.yaml manifest."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field


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


class RoutingEntry(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    hostname: str
    backend_host: str = Field(alias="backendHost")
    backend_port: int = Field(alias="backendPort", ge=1, le=65535)


class RoutingSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entries: list[RoutingEntry] = Field(default_factory=list)


class ApplicationManifest(BaseModel):
    """Single logical package per application (see README resource model)."""

    model_config = ConfigDict(populate_by_name=True)

    leco_app_version: str = Field(default="1", alias="lecoAppVersion")
    name: str = Field(..., min_length=1, description="Short slug for state directory")
    root: str = Field(default=".", description="App root relative to manifest location or absolute")

    docker_compose: DockerComposeSpec | None = Field(default=None, alias="dockerCompose")
    cloudflare: CloudflareSpec | None = Field(default=None)
    routing: RoutingSpec | None = None
    healthcheck_urls: list[str] = Field(default_factory=list, alias="healthcheckUrls")

    model_config = {"populate_by_name": True}

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

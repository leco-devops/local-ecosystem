"""Pydantic schema for leco.app.yaml manifest."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import re

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Local edge-runtime types LEco's adapter registry knows about. New adapters
# add to this tuple in lock-step with dashboard/leco_runtimes/.
LocalRuntimeType = Literal[
    "cloudflare-workers",
    "cloudflare-pages",
    "vercel",
    "aws-lambda",
    "deno-deploy",
]

# Where a routing prefix is forwarded. ``runtime`` references a local edge-runtime
# spec under ``infrastructure.runtimes[]``; ``service`` is a Docker DNS name on
# ``lh-network`` (legacy compose service); ``frontend``/``backend`` are aliases
# for ``service`` kept for vocabulary parity with the older split routing fields.
RoutingUpstreamTarget = Literal["runtime", "service", "frontend", "backend"]

LocalhostArchetype = Literal[
    "generic",
    "wordpress",
    "magento2",
    "nextjs",
    "node",
    "php-fpm",
    "laravel",
    "static",
    "java",
    "dotnet",
]

LocalhostUrlRole = Literal[
    "frontend",
    "api",
    "admin",
    "backend",
    "cdn",
    "websocket",
    "storybook",
    "graphql",
    "other",
]


class DockerComposeSpec(BaseModel):
    compose_file: str = Field(default="docker-compose.yml", alias="composeFile")
    additional_compose_files: list[str] = Field(
        default_factory=list,
        alias="additionalComposeFiles",
        description=(
            "Extra compose files merged after composeFile (docker compose -f a.yml -f b.yml …). "
            "Paths are relative to the manifest resolved root unless absolute."
        ),
    )
    additional_compose_files_from_manifest: list[str] = Field(
        default_factory=list,
        alias="additionalComposeFilesFromManifest",
        description=(
            "Extra compose files relative to the bridge manifest directory (parent of leco.app.yaml). "
            "Use for materialized apps under hosting/app-available/<id>/ so Traefik/network/env overlays "
            "stay out of the upstream application repository. Merged after the primary compose file "
            "(whether that primary is composeFile or composeFileFromManifest)."
        ),
    )
    compose_file_from_manifest: str | None = Field(
        default=None,
        alias="composeFileFromManifest",
        description=(
            "Primary compose file relative to the leco.app.yaml directory (hosting/app-available/<slug>/). "
            "When set, it is the first -f passed to docker compose and composeFile under the resolved root "
            "is not used — typical pattern: include upstream source/docker-compose.yml, then patch services "
            "(e.g. ports: !reset [] to stop publishing host :80 when Traefik owns it). "
            "Keeps all overrides in the hosting tree without editing the upstream repository."
        ),
    )
    project_name: str | None = Field(default=None, alias="projectName")
    profiles: list[str] = Field(default_factory=list)
    env_file: str | None = Field(default=None, alias="envFile")

    model_config = {"populate_by_name": True}


class CloudflareSpec(BaseModel):
    wrangler_config: str | None = Field(default=None, alias="wranglerConfig")
    wrangler_env: str | None = Field(default=None, alias="wranglerEnv")
    provision_local_resources: bool = Field(
        default=True,
        alias="provisionLocalResources",
        description="When true (default), create local KV/R2/D1 from wrangler on provision/deploy hooks.",
    )
    local_cf_public_prefix: str | None = Field(
        default=None,
        alias="localCfPublicPrefix",
        description=(
            "DNS label (e.g. cv). KV/R2/D1 API bases become https://{prefix}-kv.lh, -r2.lh, -d1.lh; "
            "ecosystem-register merges matching Traefik Host rules to shared adapters. "
            "Wrangler browser binding stays on the shared Workers stack."
        ),
    )
    dedicated_local_adapters: bool = Field(
        default=False,
        alias="dedicatedLocalAdapters",
        description=(
            "When true, provision/teardown talk to in-compose adapter services (leco-local-kv-adapter, "
            "leco-local-r2-adapter, leco-local-d1-adapter on lh-network) instead of the ecosystem-wide "
            "kv-adapter/r2-adapter/d1-adapter. Merge docker-compose.leco-dedicated-cf.yml (see sample) "
            "via additionalComposeFiles and bring the stack up before provision-local-cf."
        ),
    )

    model_config = {"populate_by_name": True}

    @field_validator("local_cf_public_prefix", mode="before")
    @classmethod
    def normalize_local_cf_public_prefix(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        s = str(v).strip().lower()
        s = re.sub(r"[^a-z0-9-]+", "-", s).strip("-")
        if not s:
            return None
        if len(s) > 40:
            raise ValueError("localCfPublicPrefix must be at most 40 characters")
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", s):
            raise ValueError(
                "localCfPublicPrefix must be lowercase alphanumeric with single hyphens between segments"
            )
        return s


class ServiceTarget(BaseModel):
    """Docker DNS name (container or compose service) on lh-network + container port."""

    model_config = ConfigDict(populate_by_name=True)

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


class LocalRuntimeSpec(BaseModel):
    """Local edge-runtime declaration.

    LEco's adapter registry (``dashboard/leco_runtimes/``) materializes one
    compose service per entry into ``docker-compose.leco-runtime.yml`` beside
    ``leco.app.yaml``. The upstream application tree is bind-mounted read-only
    where the adapter needs it; LEco-owned named volumes mask generated dirs
    (``node_modules``, ``.wrangler``, ``.vercel``, …) so the upstream repo is
    never written to.

    Only ``cloudflare-workers`` is fully implemented in V1; other ``type``
    values are *known* but their adapters raise ``NotImplementedError`` until
    later iterations land them.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: str = Field(min_length=1, description="Stable id for routing.upstream[].runtime references")
    type: LocalRuntimeType = Field(description="Adapter selector under dashboard/leco_runtimes/")
    config: str | None = Field(
        default=None,
        description=(
            "Adapter-specific config file (e.g. wrangler.toml, vercel.json) relative to ``sourceDir`` "
            "when set, otherwise to the manifest directory."
        ),
    )
    source_dir: str | None = Field(
        default=None,
        alias="sourceDir",
        description=(
            "Upstream source directory mounted into the runtime container. Relative to the manifest "
            "resolved root (so it travels through any ``source`` symlink under hosting/app-available/<slug>/)."
        ),
    )
    dev_vars_file: str | None = Field(
        default=None,
        alias="devVarsFile",
        description=(
            "Optional secrets/env file under hosting/app-available/<slug>/ that the adapter mounts into "
            "the runtime container (e.g. .dev.vars for wrangler). Lives in the LEco hosting tree only."
        ),
    )
    port: int = Field(default=8787, ge=1, le=65535, description="Container port the runtime listens on")
    image: str | None = Field(
        default=None,
        description=(
            "Override the generic adapter image (e.g. for a forked runtime). When unset, the adapter "
            "picks LEco's reference image under infra/runtimes/<type>/."
        ),
    )
    strip_bindings: list[str] | str | None = Field(
        default=None,
        alias="stripBindings",
        description=(
            "Cloudflare Workers only. List of top-level wrangler.toml sections to remove from the "
            "in-container view of the config (e.g. ``[\"browser\"]``). Default = ``[\"browser\"]`` because "
            "Wrangler local cannot simulate Browser Rendering. Set to ``\"none\"`` (or ``[]``) to keep all "
            "bindings and rely on ``wrangler dev --remote`` instead. Upstream wrangler.toml is never edited."
        ),
    )
    production_only_bindings: list[str] | str | None = Field(
        default=None,
        alias="productionOnlyBindings",
        description=(
            "Informational list of binding/feature names this runtime depends on that LEco cannot simulate "
            "locally (e.g. ``[\"browser\", \"vectorize\", \"analytics_engine_datasets\"]``). LEco surfaces "
            "these as ``expected: production-only`` badges in the dashboard / ``leco-devops runtimes`` output "
            "so the operator does not chase phantom \"down\" markers in /health for paid Cloudflare features. "
            "Defaults to a conservative built-in list (see ``dashboard/leco_runtimes/cloudflare_workers.py``). "
            "Set to ``\"none\"`` to suppress the badge entirely."
        ),
    )

    @field_validator("id")
    @classmethod
    def normalize_id(cls, v: str) -> str:
        s = (v or "").strip().lower()
        if not s or not re.match(r"^[a-z0-9][a-z0-9-]{0,39}$", s):
            raise ValueError("runtime id must be lowercase alphanumeric/hyphen, 1-40 chars, start alnum")
        return s


class RoutingUpstreamRule(BaseModel):
    """One path-prefix → upstream mapping inside a ``RoutingEntry``.

    ``target`` selects what kind of upstream this prefix forwards to:

    - ``runtime`` — references a sibling ``infrastructure.runtimes[]`` entry by id.
      Traefik routes to ``leco-rt-<slug>-<runtime.id>:<runtime.port>``.
    - ``service`` / ``frontend`` / ``backend`` — a Docker DNS name on lh-network
      (the latter two are vocabulary aliases that read better in YAML).

    Specificity wins: rules sort by descending prefix length, so ``/api`` beats
    ``/`` for the same hostname (Traefik priorities encode this).
    """

    model_config = ConfigDict(populate_by_name=True)

    prefix: str = Field(default="/", description="Path prefix this rule matches (must start with /)")
    target: RoutingUpstreamTarget = Field(description="Type of upstream this prefix forwards to")
    runtime: str | None = Field(
        default=None,
        description="Required when target=runtime; matches one of infrastructure.runtimes[].id",
    )
    service: ServiceTarget | None = Field(
        default=None,
        description="Required when target in {service, frontend, backend}",
    )

    @field_validator("prefix")
    @classmethod
    def normalize_prefix(cls, v: str) -> str:
        p = (v or "/").strip()
        if not p.startswith("/"):
            raise ValueError("routing upstream prefix must start with /")
        return p

    @model_validator(mode="after")
    def shape(self) -> RoutingUpstreamRule:
        if self.target == "runtime":
            if not self.runtime or not str(self.runtime).strip():
                raise ValueError("routing upstream target=runtime needs 'runtime: <id>'")
            if self.service is not None:
                raise ValueError("routing upstream target=runtime cannot also set 'service'")
        else:
            if self.service is None:
                raise ValueError(
                    f"routing upstream target={self.target} requires 'service: {{host, port}}'"
                )
            if self.runtime:
                raise ValueError(f"routing upstream target={self.target} cannot also set 'runtime'")
        return self


class RoutingEntry(BaseModel):
    """Traefik file-provider fragment.

    **Modern (recommended):** ``upstream:`` list — one rule per path prefix.
    Each rule forwards to either a local edge runtime (``target: runtime``) or
    a Docker DNS name (``target: service``). See :class:`RoutingUpstreamRule`.

    **Split (still supported):** ``frontend`` + ``apiBackend`` + ``apiPathPrefix``
    → higher-priority ``Host && PathPrefix`` routers to the API, catch-all ``Host``
    to the UI (CrawlerVision / local-ecosystem pattern).

    **Legacy:** single ``backendHost`` + ``backendPort`` → one service for the
    hostname.

    Exactly one of these shapes must apply per entry.
    """

    model_config = ConfigDict(populate_by_name=True)

    hostname: str = Field(min_length=1)
    api_path_prefix: str = Field(default="/api", alias="apiPathPrefix")
    frontend: ServiceTarget | None = None
    api_backend: ServiceTarget | None = Field(None, alias="apiBackend")
    backend_host: str = Field(default="", alias="backendHost")
    backend_port: int = Field(default=8080, alias="backendPort", ge=1, le=65535)
    upstream: list[RoutingUpstreamRule] = Field(
        default_factory=list,
        description=(
            "Optional list of path-prefix → upstream rules. When non-empty, this overrides "
            "the legacy ``frontend``/``apiBackend``/``backendHost`` shape for this entry."
        ),
    )

    @field_validator("api_path_prefix")
    @classmethod
    def normalize_api_prefix(cls, v: str) -> str:
        p = (v or "/api").strip()
        if not p.startswith("/"):
            raise ValueError("apiPathPrefix must start with /")
        return p

    @model_validator(mode="after")
    def routing_shape(self) -> RoutingEntry:
        modern = bool(self.upstream)
        split = self.frontend is not None and self.api_backend is not None
        legacy = bool(self.backend_host and self.backend_host.strip())
        chosen = sum(1 for x in (modern, split, legacy) if x)
        if chosen == 0:
            raise ValueError(
                "routing entry: set upstream[], or (frontend + apiBackend), or non-empty backendHost"
            )
        if chosen > 1:
            raise ValueError(
                "routing entry: pick exactly one shape — upstream[], (frontend + apiBackend), or backendHost"
            )
        return self


class RoutingSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    entries: list[RoutingEntry] = Field(default_factory=list)


class TraefikCleanupSpec(BaseModel):
    """Explicit router/service keys in hosting/traefik/dynamic.yml to remove on offload (optional).

    Use when you renamed keys while merging (leco defaults use ``{name}-{hostslug}-…``).
    If omitted, keys are derived from ``routing`` the same way as ``traefik-fragment``.
    """

    model_config = ConfigDict(populate_by_name=True)

    routers: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)


class LocalhostUrlEntry(BaseModel):
    """Logical URL for docs, probes, and operator reference (Traefik routing stays under ``routing``)."""

    model_config = ConfigDict(populate_by_name=True)

    role: LocalhostUrlRole = "other"
    label: str = ""
    public_url: str = Field(default="", alias="publicUrl")
    internal: str | None = None
    path_prefix: str = Field(default="", alias="pathPrefix")


class LifecycleStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    command: str = Field(..., min_length=1)
    cwd: str | None = Field(default=None, description="Relative to app root (manifest parent / root)")
    shell: bool = True
    timeout_sec: int = Field(default=600, ge=1, alias="timeoutSec", description="Per-step timeout")


class LocalhostLifecycleSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prepare: list[LifecycleStep] = Field(default_factory=list)
    build: list[LifecycleStep] = Field(default_factory=list)
    pre_start: list[LifecycleStep] = Field(default_factory=list, alias="preStart")


class ContainerImageSpec(BaseModel):
    """Optional image/build hints for future infra automation (compose remains primary)."""

    model_config = ConfigDict(populate_by_name=True)

    dockerfile: str | None = Field(
        default=None,
        description="Path to Dockerfile relative to application root (manifest resolved root).",
    )
    context: str | None = Field(default=None, description="Build context directory relative to app root.")
    build_args: dict[str, str] = Field(default_factory=dict, alias="buildArgs")
    target: str | None = Field(default=None, description="Multi-stage Dockerfile target name.")


class WranglerBindingPreviewKvRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    binding: str
    cf_id: str = Field(alias="cfId")


class WranglerBindingPreviewR2Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    binding: str
    bucket_name: str = Field(alias="bucketName")


class WranglerBindingPreviewD1Row(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    binding: str
    database_name: str = Field(alias="databaseName")


class WranglerBindingPreview(BaseModel):
    """Human-readable mirror of wrangler.toml bindings. Provision still reads ``wranglerConfig``."""

    model_config = ConfigDict(populate_by_name=True)

    note: str = Field(
        default=(
            "Each kv[] row → one local KV namespace; each r2[] → one R2 bucket; each d1[] → one D1 database. "
            "leco-devops reads wrangler.toml (infrastructure.cloudflare.wranglerConfig) to create them."
        ),
    )
    wrangler_env: str = Field(default="", alias="wranglerEnv")
    kv: list[WranglerBindingPreviewKvRow] = Field(default_factory=list)
    r2: list[WranglerBindingPreviewR2Row] = Field(default_factory=list)
    d1: list[WranglerBindingPreviewD1Row] = Field(default_factory=list)


class ProfileInfrastructureSpec(BaseModel):
    """Source of truth for deployable infra when present on ``leco.yaml`` (profile).

    If this block is set on the merged profile, these fields override the same keys on
    ``leco.app.yaml`` for tooling (compose, Traefik, local Cloudflare adapters).
    """

    model_config = ConfigDict(populate_by_name=True)

    docker_compose: DockerComposeSpec | None = Field(default=None, alias="dockerCompose")
    cloudflare: CloudflareSpec | None = None
    routing: RoutingSpec | None = None
    runtimes: list[LocalRuntimeSpec] = Field(
        default_factory=list,
        description=(
            "Local edge-runtime containers LEco materializes beside the upstream compose stack. "
            "Each entry produces one service in docker-compose.leco-runtime.yml and may be "
            "referenced from routing.entries[].upstream[] by id."
        ),
    )
    traefik_cleanup: TraefikCleanupSpec | None = Field(default=None, alias="traefikCleanup")
    healthcheck_urls: list[str] | None = Field(
        default=None,
        alias="healthcheckUrls",
        description="When set (including empty list), replaces manifest healthcheckUrls.",
    )
    container_image: ContainerImageSpec | None = Field(default=None, alias="containerImage")
    wrangler_binding_preview: WranglerBindingPreview | None = Field(
        default=None,
        alias="wranglerBindingPreview",
        description="Optional; filled by Generate YAML from wrangler.toml for visibility (not a second source of truth).",
    )

    @model_validator(mode="after")
    def _runtime_ids_unique(self) -> ProfileInfrastructureSpec:
        if not self.runtimes:
            return self
        seen: set[str] = set()
        for rt in self.runtimes:
            if rt.id in seen:
                raise ValueError(f"duplicate runtime id: {rt.id}")
            seen.add(rt.id)
        if self.routing and self.routing.entries:
            for entry in self.routing.entries:
                for rule in entry.upstream:
                    if rule.target == "runtime" and rule.runtime not in seen:
                        raise ValueError(
                            f"routing entry {entry.hostname!r} references unknown runtime "
                            f"{rule.runtime!r} (known: {sorted(seen)})"
                        )
        return self


class LocalhostProfile(BaseModel):
    """``leco.yaml`` — operator profile, lifecycle, URLs, and (optionally) full infrastructure spec."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=1, alias="schemaVersion")
    archetype: LocalhostArchetype = "generic"
    infrastructure: ProfileInfrastructureSpec | None = None
    urls: list[LocalhostUrlEntry] = Field(default_factory=list)
    lifecycle: LocalhostLifecycleSpec = Field(default_factory=LocalhostLifecycleSpec)
    notes: str = ""


class BridgeConfigRefs(BaseModel):
    """Paths on the bridge (``leco.app.yaml``) pointing at app config files.

    All paths are relative to :meth:`ApplicationManifest.resolved_root` unless absolute.
    These are discoverability / documentation; deploy still uses ``leco.yaml`` ``infrastructure``
    after merge. Omit keys you do not use. Absolute on-disk paths are mirrored under
    ``resolvedPaths`` (same key names) when YAML is generated or saved.
    """

    model_config = ConfigDict(populate_by_name=True)

    wrangler_config: str | None = Field(
        default=None,
        alias="wranglerConfig",
        description="Cloudflare Wrangler file (wrangler.toml, wrangler.json, …).",
    )
    docker_compose_file: str | None = Field(
        default=None,
        alias="dockerComposeFile",
        description="Primary Compose file (e.g. docker-compose.yml).",
    )
    compose_override_file: str | None = Field(
        default=None,
        alias="composeOverrideFile",
        description="Optional docker-compose.override.yml or similar.",
    )
    env_file: str | None = Field(default=None, alias="envFile", description=".env for compose / runtime.")
    dockerfile: str | None = Field(default=None, alias="dockerfile")
    package_json: str | None = Field(default=None, alias="packageJson")
    wordpress_config_php: str | None = Field(
        default=None,
        alias="wordpressConfigPhp",
        description="WordPress wp-config.php (or custom path).",
    )
    nginx_config: str | None = Field(default=None, alias="nginxConfig", description="nginx.conf or site conf path.")
    varnish_vcl: str | None = Field(default=None, alias="varnishVcl", description="default.vcl or included VCL path.")
    php_fpm_pool: str | None = Field(default=None, alias="phpFpmPool", description="www.conf or pool.d snippet.")
    mysql_init: str | None = Field(default=None, alias="mysqlInit", description="SQL init dir or file for MySQL/MariaDB.")
    mongo_init: str | None = Field(default=None, alias="mongoInit", description="Mongo init scripts directory.")
    redis_config: str | None = Field(default=None, alias="redisConfig", description="redis.conf if not only defaults.")


class ApplicationManifest(BaseModel):
    """Bridge file: links LEco to the app root and the ``leco.yaml`` profile.

    With ``lecoAppVersion`` ``3``+, put Docker / Cloudflare / routing under ``leco.yaml``
    ``infrastructure:``; keep this file to ``name``, ``root``, ``localHostProfile``, and optional
    ``configRefs`` (entrypoint paths). Older manifests may still declare ``dockerCompose`` /
    ``cloudflare`` here (version ``2``).

    ``resolvedPaths`` holds absolute paths on the host for the resolved app root, this manifest,
    the profile file, and targets of ``configRefs`` / effective profile infrastructure (filled when
    generating or saving YAML). Operators may refresh them by re-running generate-yaml.
    """

    model_config = ConfigDict(populate_by_name=True)

    leco_app_version: str = Field(default="1", alias="lecoAppVersion")
    application_version: str | None = Field(
        default=None,
        alias="applicationVersion",
        description="Your app release label (e.g. 1.4.2); shown in Hosted apps to confirm deploy.",
    )
    name: str = Field(..., min_length=1, description="Short slug for state directory")
    root: str = Field(default=".", description="App root relative to manifest location or absolute")

    docker_compose: DockerComposeSpec | None = Field(default=None, alias="dockerCompose")
    cloudflare: CloudflareSpec | None = Field(default=None)
    routing: RoutingSpec | None = None
    runtimes: list[LocalRuntimeSpec] = Field(
        default_factory=list,
        description=(
            "Local edge-runtime declarations. Usually set under leco.yaml infrastructure.runtimes; "
            "kept here for backward compatibility when a manifest carries it inline."
        ),
    )
    traefik_cleanup: TraefikCleanupSpec | None = Field(default=None, alias="traefikCleanup")
    healthcheck_urls: list[str] = Field(default_factory=list, alias="healthcheckUrls")
    local_host_profile: str | None = Field(
        default=None,
        alias="localHostProfile",
        description="Optional path to localhost.yaml relative to this manifest directory",
    )
    localhost: LocalhostProfile | None = Field(
        default=None,
        description="Inline localhost profile; merged over localHostProfile file (lists concatenated)",
    )
    config_refs: BridgeConfigRefs | None = Field(
        default=None,
        alias="configRefs",
        description="Optional paths to wrangler, compose, Dockerfile, WordPress, nginx, Varnish, etc.",
    )
    resolved_paths: dict[str, str] | None = Field(
        None,
        alias="resolvedPaths",
        description=(
            "Absolute paths: sourceRoot, manifestPath, localHostProfile, and keys mirroring "
            "configRefs (wranglerConfig, dockerComposeFile, …) when the target exists on disk."
        ),
    )

    @field_validator("resolved_paths", mode="before")
    @classmethod
    def _coerce_resolved_paths(cls, v: Any) -> dict[str, str] | None:
        if v is None:
            return None
        if not isinstance(v, dict):
            return None
        out: dict[str, str] = {}
        for k, val in v.items():
            if isinstance(k, str) and k.strip():
                out[k.strip()] = str(val) if val is not None else ""
        return out or None

    def resolved_root(self, manifest_path: Path) -> Path:
        r = Path(self.root)
        if r.is_absolute():
            return r.resolve()
        return (manifest_path.parent / r).resolve()


def load_manifest(path: Path) -> ApplicationManifest:
    """Load ``leco.app.yaml`` only (no profile merge). Prefer :func:`load_effective_manifest` for CLI."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a YAML mapping")
    return ApplicationManifest.model_validate(data)


def _profile_infrastructure_nonempty(infra: ProfileInfrastructureSpec | None) -> bool:
    if infra is None:
        return False
    dumped = infra.model_dump(exclude_none=True)
    return bool(dumped)


def merge_profile_infrastructure_into_manifest(
    manifest: ApplicationManifest,
    profile: LocalhostProfile,
) -> ApplicationManifest:
    """Overlay profile ``infrastructure`` onto manifest when the profile defines that block."""
    infra = profile.infrastructure
    if not _profile_infrastructure_nonempty(infra):
        return manifest
    assert infra is not None
    updates: dict[str, Any] = {}
    if infra.docker_compose is not None:
        updates["docker_compose"] = infra.docker_compose
    if infra.cloudflare is not None:
        updates["cloudflare"] = infra.cloudflare
    if infra.routing is not None:
        updates["routing"] = infra.routing
    if infra.runtimes:
        updates["runtimes"] = list(infra.runtimes)
    if infra.traefik_cleanup is not None:
        updates["traefik_cleanup"] = infra.traefik_cleanup
    if infra.healthcheck_urls is not None:
        updates["healthcheck_urls"] = list(infra.healthcheck_urls)
    return manifest.model_copy(update=updates)


def load_effective_manifest(manifest_path: Path) -> ApplicationManifest:
    """Load bridge manifest and merge ``leco.yaml`` ``infrastructure`` for deploy / register / Traefik."""
    m = load_manifest(manifest_path)
    if not m.local_host_profile or not str(m.local_host_profile).strip():
        return m
    rel = Path(m.local_host_profile.strip())
    cand = (manifest_path.parent / rel).resolve()
    if not cand.is_file():
        return m
    profile = load_localhost_profile_file(cand)
    return merge_profile_infrastructure_into_manifest(m, profile)


def load_localhost_profile_file(path: Path) -> LocalhostProfile:
    """Load a standalone ``localhost.yaml`` (or ``leco.localhost.yaml``)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("localhost profile must be a YAML mapping")
    return LocalhostProfile.model_validate(data)


def merge_infrastructure_specs(
    base: ProfileInfrastructureSpec | None,
    overlay: ProfileInfrastructureSpec | None,
) -> ProfileInfrastructureSpec | None:
    """Overlay wins per field when non-None."""
    if base is None and overlay is None:
        return None
    if base is None:
        return overlay
    if overlay is None:
        return base
    return ProfileInfrastructureSpec(
        docker_compose=overlay.docker_compose if overlay.docker_compose is not None else base.docker_compose,
        cloudflare=overlay.cloudflare if overlay.cloudflare is not None else base.cloudflare,
        routing=overlay.routing if overlay.routing is not None else base.routing,
        runtimes=list(overlay.runtimes) if overlay.runtimes else list(base.runtimes),
        traefik_cleanup=overlay.traefik_cleanup if overlay.traefik_cleanup is not None else base.traefik_cleanup,
        healthcheck_urls=overlay.healthcheck_urls if overlay.healthcheck_urls is not None else base.healthcheck_urls,
        container_image=overlay.container_image if overlay.container_image is not None else base.container_image,
        wrangler_binding_preview=overlay.wrangler_binding_preview
        if overlay.wrangler_binding_preview is not None
        else base.wrangler_binding_preview,
    )


def merge_localhost_profiles(
    from_file: LocalhostProfile | None,
    inline: LocalhostProfile | None,
) -> LocalhostProfile:
    """Merge sidecar file then inline: lists concatenate; archetype prefers inline when not generic."""
    if from_file is None and inline is None:
        return LocalhostProfile()
    if from_file is None:
        return inline  # type: ignore[return-value]
    if inline is None:
        return from_file

    arch: LocalhostArchetype = inline.archetype if inline.archetype != "generic" else from_file.archetype
    lc_f = from_file.lifecycle
    lc_i = inline.lifecycle
    merged_lifecycle = LocalhostLifecycleSpec(
        prepare=[*lc_f.prepare, *lc_i.prepare],
        build=[*lc_f.build, *lc_i.build],
        pre_start=[*lc_f.pre_start, *lc_i.pre_start],
    )
    notes_parts = [x for x in (from_file.notes.strip(), inline.notes.strip()) if x]
    merged_inf = merge_infrastructure_specs(from_file.infrastructure, inline.infrastructure)
    return LocalhostProfile(
        schema_version=max(from_file.schema_version, inline.schema_version),
        archetype=arch,
        infrastructure=merged_inf,
        urls=[*from_file.urls, *inline.urls],
        lifecycle=merged_lifecycle,
        notes="\n\n".join(notes_parts),
    )


@dataclass(frozen=True)
class MergedApplication:
    """Manifest plus merged localhost profile for hooks and UI."""

    manifest: ApplicationManifest
    manifest_path: Path
    localhost: LocalhostProfile


def load_merged_manifest(manifest_path: Path) -> MergedApplication:
    """Load bridge + profile; ``manifest`` in the result includes profile infrastructure overlay."""
    m = load_manifest(manifest_path)
    from_file: LocalhostProfile | None = None
    if m.local_host_profile:
        rel = Path(m.local_host_profile.strip())
        cand = (manifest_path.parent / rel).resolve()
        if cand.is_file():
            from_file = load_localhost_profile_file(cand)
    merged = merge_localhost_profiles(from_file, m.localhost)
    effective = merge_profile_infrastructure_into_manifest(m, merged)
    return MergedApplication(
        manifest=effective,
        manifest_path=manifest_path.resolve(),
        localhost=merged,
    )


def save_manifest(path: Path, manifest: ApplicationManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Dump with aliases for camelCase file format
    d = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    path.write_text(
        yaml.safe_dump(d, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def save_localhost_profile(path: Path, profile: LocalhostProfile) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    d = profile.model_dump(mode="json", by_alias=True, exclude_none=True)
    path.write_text(
        yaml.safe_dump(d, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def manifest_from_dict(data: dict[str, Any]) -> ApplicationManifest:
    return ApplicationManifest.model_validate(data)

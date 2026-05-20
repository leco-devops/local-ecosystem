"""Cloudflare Workers local runtime adapter (Wrangler/Miniflare).

Maps a :class:`LocalRuntimeSpec` with ``type: cloudflare-workers`` to a compose
service running LEco's generic ``leco/runtime-cloudflare-workers`` image. The
image starts ``wrangler dev`` against the upstream ``wrangler.toml``; Miniflare
provides local KV/R2/D1 with file persistence inside an LEco-owned named volume.

Zero-touch upstream contract:

- The adapter bind-mounts the upstream ``sourceDir`` (resolved through the
  manifest's ``source`` symlink) into ``/app`` inside the container.
- Two LEco-owned named volumes mask ``/app/node_modules`` and ``/app/.wrangler``
  so ``npm ci`` / Miniflare state never write back into the upstream repo.
- The optional ``.dev.vars`` file lives under
  ``hosting/app-available/<slug>/`` (the LEco hosting tree) and is bind-mounted
  at ``/app/.dev.vars`` read-only.
- When the upstream ``wrangler.toml`` declares bindings Wrangler local refuses
  to start with (e.g. **`[browser]`** — Cloudflare Browser Rendering), the
  adapter materializes a *sanitized* copy under
  ``hosting/app-available/<slug>/.leco-runtime/<runtime_id>/wrangler.toml``
  and overlays it on top of ``/app/wrangler.toml`` via a file bind mount. The
  upstream wrangler.toml is untouched on disk; only the in-container view sees
  the sanitized version, and Wrangler still resolves ``main`` and other
  config-relative paths correctly because the overlay sits at the same
  in-container path the upstream file had.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .base import RuntimeAdapter, RuntimeBuildContext, RuntimeDetection


DEFAULT_IMAGE = "leco/runtime-cloudflare-workers:latest"
DEFAULT_PORT = 8787

# Bindings that Wrangler v3/v4 *local* mode cannot simulate. Listing one in
# wrangler.toml without ``--remote`` makes ``wrangler dev`` exit immediately,
# so we strip them from the in-container view of the config. Operators who
# want real Browser Rendering / etc. locally can override this list per-app via
# ``infrastructure.runtimes[].stripBindings`` (a passthrough field).
#
# When ``LECO_LOCAL_BROWSER_URL`` is set (or the browser-rendering-local stack
# is healthy), browser is removed from this list so the Wrangler bridge can
# forward to the local Playwright/CDP service at browser.lh.
DEFAULT_LOCAL_UNSUPPORTED_BINDINGS: tuple[str, ...] = ("browser",)

import os as _os
_LECO_LOCAL_BROWSER_URL = _os.environ.get("LECO_LOCAL_BROWSER_URL", "").strip()

# Top-level wrangler.toml sections whose features Wrangler local cannot fully
# simulate (today). Surfaced informationally in the dashboard / CLI so an
# operator knows which "down" lines on a hosted app's /health are
# production-only behavior rather than a LEco misconfiguration. Apps can
# override with ``infrastructure.runtimes[].productionOnlyBindings`` to refine
# the badge text per environment.
#
# CF ↔ LEco local substitutes (see docs/CF_LECO_SERVICE_MAP.md):
#   browser      → browser.lh (Wrangler bridge via LECO_LOCAL_BROWSER_URL)
#   hyperdrive   → postgres.lh:5432 / infra mysql (.dev.vars DSN shim)
#   send_email   → Mailpit SMTP (mailpit:1025 inside Docker)
INFORMATIONAL_PRODUCTION_ONLY_BINDINGS: tuple[str, ...] = (
    "vectorize",    # Vector embeddings store (no Miniflare equivalent)
    "analytics_engine_datasets",  # Cloudflare Analytics Engine (cloud-only)
    "mtls_certificates",
)

# Bindings that have local partial substitutes — not in the production-only
# badge list when the local service is available.
_BRIDGE_BINDINGS_WITH_LOCAL_SUBSTITUTE: dict[str, str] = {
    "browser": "browser.lh (Playwright/CDP)",
    "hyperdrive": "postgres.lh:5432 or infra mysql (.dev.vars DSN)",
    "send_email": "Mailpit SMTP (mailpit:1025)",
}

_TOML_TABLE_HEADER_RE = re.compile(r"^\s*\[\[?(?P<name>[A-Za-z_][A-Za-z0-9_.\- ]*)\]\]?\s*(#.*)?$")

# ``binding = "NAME"`` declared anywhere on a line. Wrangler accepts both the
# line-leading form (``binding = "FOO"`` inside ``[[d1_databases]]``) and the
# inline-table form (``assets = { directory = ..., binding = "ASSETS", ... }``)
# so we match the substring without anchoring to start-of-line.
_TOML_BINDING_VALUE_RE = re.compile(r'\bbinding\s*=\s*"([A-Za-z_][A-Za-z0-9_]*)"')

# Lines under ``[vars]`` look like ``KEY = "value"`` / ``KEY = 123``. We never
# capture values — only the key names.
_TOML_VARS_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]{1,})\s*=")

# Worker source references to env. Match both dotted (``env.OPENAI_API_KEY``)
# and bracketed (``env["OPENAI_API_KEY"]``) access. Restricted to UPPER_SNAKE
# (2+ chars after the leading letter) to avoid camelCase property noise.
_ENV_REF_RE = re.compile(
    r"""\benv\s*(?:\.([A-Z][A-Z0-9_]{1,})|\[\s*['"]([A-Z][A-Z0-9_]{1,})['"]\s*\])"""
)

# Worker source files we scan for env references. Limited to keep onboarding
# fast on huge repos (we only need the names that *occur*, not every callsite).
_WORKER_SOURCE_GLOBS: tuple[str, ...] = ("**/*.ts", "**/*.tsx", "**/*.js", "**/*.mjs", "**/*.cjs")

# Hard caps on the source-scan walk so onboarding does not stall on huge
# monorepos. We only need to discover *which* env keys are referenced, so
# bounded reading per file is fine.
_MAX_SOURCE_FILE_BYTES = 256_000
_MAX_SOURCE_FILES_SCANNED = 400


class CloudflareWorkersAdapter(RuntimeAdapter):
    type = "cloudflare-workers"
    label = "Cloudflare Workers (Wrangler/Miniflare)"
    roadmap = ""  # fully implemented

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        """Return the best single runtime candidate (see :meth:`detect_all`)."""
        all_hits = self.detect_all(app_root)
        return all_hits[0] if all_hits else None

    def detect_all(self, app_root: Path) -> list[RuntimeDetection]:
        """Surface one runtime per wrangler TOML (``wrangler.toml``, ``wrangler.api.toml``, …).

        Beyond locating wrangler configs, we also scan each Worker entrypoint
        (``src/index.ts`` / ``index.js`` etc.) for top-level path patterns
        the Worker handles. Operators then know which prefixes to add to
        ``routing.entries[].upstream`` so paths like ``/health/json`` that
        production resolves at the edge don't accidentally fall through to
        a Docker-Compose frontend that serves an SPA shell.
        """
        from leco_wrangler_paths import list_wrangler_config_files, runtime_id_from_wrangler_relpath

        if not app_root.is_dir():
            return []
        configs = list_wrangler_config_files(app_root)
        if not configs:
            # Legacy subdirectory layouts without wrangler.*.toml naming.
            candidates: list[tuple[str | None, Path]] = [
                ("cloudflare", app_root / "cloudflare" / "wrangler.toml"),
                ("worker", app_root / "worker" / "wrangler.toml"),
                ("workers", app_root / "workers" / "wrangler.toml"),
                (None, app_root / "wrangler.toml"),
            ]

            def _score(item: tuple[str | None, Path]) -> int:
                _, p = item
                if not p.is_file():
                    return -1
                sibling_src = p.parent / "src"
                if sibling_src.is_dir():
                    for ext in ("*.ts", "*.tsx", "*.js", "*.mjs"):
                        if any(sibling_src.rglob(ext)):
                            return 2
                    return 1
                return 0

            candidates.sort(key=_score, reverse=True)
            configs = [p.relative_to(app_root) for _, p in candidates if p.is_file()]

        out: list[RuntimeDetection] = []
        port = DEFAULT_PORT
        for rel in configs:
            path = (app_root / rel).resolve()
            if not path.is_file():
                continue
            runtime_id = runtime_id_from_wrangler_relpath(rel)
            config_rel = rel.as_posix()
            spec: dict[str, Any] = {
                "id": runtime_id,
                "type": self.type,
                "config": config_rel,
                "port": port,
            }
            worker_dir = path.parent
            worker_paths = _scan_worker_paths(worker_dir)
            detail = f"Found {rel.as_posix()}"
            yaml_hint = ""
            if worker_paths:
                routing_hint = _routing_hint_from_paths(worker_paths)
                detail = (
                    f"{detail}; Worker paths detected: "
                    f"{', '.join(sorted(worker_paths)[:6])}"
                    + (f"  ➜ suggested upstream rules: {routing_hint}" if routing_hint else "")
                )
                yaml_hint = suggested_upstream_yaml(runtime_id, worker_paths)

            expected_secrets: list[str] = []
            dev_vars_example = ""
            try:
                wrangler_text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                wrangler_text = ""
            if wrangler_text:
                expected_secrets = detect_expected_secrets(worker_dir, wrangler_text)
                if expected_secrets:
                    dev_vars_example = render_dev_vars_example(expected_secrets, app_root.name)
                    detail = (
                        f"{detail}; .dev.vars secrets expected: "
                        f"{', '.join(expected_secrets[:6])}"
                        + (f" (+{len(expected_secrets) - 6} more)" if len(expected_secrets) > 6 else "")
                    )

            out.append(
                RuntimeDetection(
                    type=self.type,
                    runtime_id=runtime_id,
                    spec=spec,
                    detail=detail,
                    suggested_upstream_yaml=yaml_hint,
                    expected_secrets=tuple(expected_secrets),
                    dev_vars_example=dev_vars_example,
                )
            )
            port += 1
        return out

    def compose_service(
        self,
        spec: dict[str, Any],
        ctx: RuntimeBuildContext,
    ) -> dict[str, Any]:
        runtime_id = str(spec.get("id") or "worker")
        port = int(spec.get("port") or DEFAULT_PORT)
        image = str(spec.get("image") or DEFAULT_IMAGE).strip() or DEFAULT_IMAGE

        # source_dir is relative to the resolved manifest root (which already
        # points at the upstream repo via hosting/app-available/<slug>/source).
        # The compose file resolves the bind mount on the Docker host, so we
        # emit absolute host paths.
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

        config_rel = (spec.get("config") or "wrangler.toml").strip() or "wrangler.toml"

        environment = {
            "LECO_APP_SLUG": ctx.app_slug,
            "LECO_RUNTIME_ID": runtime_id,
            "LECO_PORT": str(port),
            "LECO_WRANGLER_CONFIG": f"/app/{config_rel}",
        }

        node_modules_vol = ctx.named_volume(runtime_id, "node-modules")
        wrangler_vol = ctx.named_volume(runtime_id, "wrangler-state")

        volumes: list[dict[str, Any]] = [
            {"type": "bind", "source": str(host_source), "target": "/app"},
            # Named-volume mounts mask the bind so npm/wrangler never write
            # back into the upstream tree.
            {"type": "volume", "source": node_modules_vol, "target": "/app/node_modules"},
            {"type": "volume", "source": wrangler_vol, "target": "/app/.wrangler"},
        ]

        # Materialize a sanitized wrangler.toml when the upstream config exists
        # and contains bindings Wrangler-local refuses to load. The file lives
        # under the per-slug ``.leco-runtime/<runtime_id>/`` directory (gitignored
        # via the parent ``hosting/app-available/*/`` rule) and is bind-overlaid
        # on top of ``/app/<config_rel>`` so Wrangler still resolves ``main``
        # and other config-relative paths from the same in-container directory.
        strip_bindings = self._resolve_strip_list(spec)
        if container_source is not None and strip_bindings:
            upstream_cfg = (container_source / config_rel).resolve()
            if upstream_cfg.is_file() and ctx.manifest_dir_container is not None:
                stripped, removed = _sanitize_wrangler_toml(
                    upstream_cfg.read_text(encoding="utf-8"), strip_bindings
                )
                if removed:
                    out_dir = ctx.manifest_dir_container / ".leco-runtime" / runtime_id
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / Path(config_rel).name
                    header = (
                        "# LEco DevOps — sanitized wrangler.toml overlay (auto-generated).\n"
                        "# Source: " + str(upstream_cfg) + "\n"
                        "# Sections stripped (not supported by wrangler-local): "
                        + ", ".join(sorted(removed))
                        + "\n# Edit infrastructure.runtimes[].stripBindings to override the list.\n"
                        "# This file is bind-mounted onto /app/" + config_rel
                        + " inside the runtime container only.\n\n"
                    )
                    out_path.write_text(header + stripped, encoding="utf-8")
                    # Map to host-visible path for the Docker daemon's bind source.
                    host_out_path = out_path
                    if ctx.manifest_dir_container is not None and ctx.manifest_dir != ctx.manifest_dir_container:
                        try:
                            rel = out_path.relative_to(ctx.manifest_dir_container)
                            host_out_path = ctx.manifest_dir / rel
                        except ValueError:
                            host_out_path = out_path
                    volumes.append(
                        {
                            "type": "bind",
                            "source": str(host_out_path),
                            "target": f"/app/{config_rel}",
                            "read_only": True,
                        }
                    )
                    environment["LECO_WRANGLER_SANITIZED_BINDINGS"] = ",".join(sorted(removed))

        # D1 bootstrap directory. The entrypoint applies any
        # ``d1-bootstrap[-<binding>].sql`` it finds there *before* running
        # ``wrangler d1 migrations apply``. This is how apps whose base schema
        # lives outside ``migrations/`` (e.g. inside a TypeScript module the
        # Worker ``exec()``s manually) get a production-faithful D1 locally.
        # The directory is operator-owned: bare-empty by default; gitignored
        # via the parent ``hosting/app-available/*/`` rule. Mounting an empty
        # dir is harmless — the entrypoint's locate_d1_bootstrap helper simply
        # finds no files and skips the phase.
        if ctx.manifest_dir_container is not None:
            bootstrap_dir_container = ctx.manifest_dir_container / ".leco-runtime" / runtime_id
            bootstrap_dir_container.mkdir(parents=True, exist_ok=True)
            try:
                bootstrap_dir_host = ctx.manifest_dir / bootstrap_dir_container.relative_to(
                    ctx.manifest_dir_container
                )
            except ValueError:
                bootstrap_dir_host = bootstrap_dir_container
            volumes.append(
                {
                    "type": "bind",
                    "source": str(bootstrap_dir_host),
                    "target": "/leco-runtime/d1",
                    "read_only": True,
                }
            )
            environment["LECO_D1_BOOTSTRAP_DIR"] = "/leco-runtime/d1"

        # .dev.vars binding (Wrangler reads /app/.dev.vars automatically).
        #
        # We accept two equivalent inputs:
        #   1. explicit ``infrastructure.runtimes[].devVarsFile`` (path relative
        #      to the manifest dir),
        #   2. an auto-detected ``.dev.vars`` file at the manifest root.
        # If both resolve, the explicit path wins. Compose mounts a file only
        # if the source actually exists (otherwise docker would create a dir).
        dev_vars_target = "/app/.dev.vars"
        dev_vars_host_path: Path | None = None
        explicit_dev_vars = (spec.get("devVarsFile") or "").strip()
        if explicit_dev_vars:
            cand = (ctx.manifest_dir / explicit_dev_vars).resolve()
            if cand.is_file():
                dev_vars_host_path = cand
        if dev_vars_host_path is None and ctx.manifest_dir_container is not None:
            implicit = (ctx.manifest_dir_container / ".dev.vars").resolve()
            if implicit.is_file():
                # Map container-visible path back to the host path used by the
                # Docker daemon (the implicit file always lives under the
                # operator-owned hosting/app-available/<slug>/ tree).
                try:
                    rel = implicit.relative_to(ctx.manifest_dir_container)
                    dev_vars_host_path = (ctx.manifest_dir / rel).resolve()
                except ValueError:
                    dev_vars_host_path = implicit
        if dev_vars_host_path is not None and dev_vars_host_path.is_file():
            volumes.append(
                {
                    "type": "bind",
                    "source": str(dev_vars_host_path),
                    "target": dev_vars_target,
                    "read_only": True,
                }
            )
            environment["LECO_DEV_VARS"] = dev_vars_target
        else:
            environment["LECO_DEV_VARS"] = ""

        # Auto-materialize a .dev.vars.example skeleton when the operator has
        # not yet written one. This is the discoverability bridge — operators
        # see what secrets are expected without spelunking through CV's source.
        # Never overwrite an existing .dev.vars.example (operator may have
        # customized comments / grouping), and never touch .dev.vars itself.
        if ctx.manifest_dir_container is not None:
            try:
                example_text = self._render_dev_vars_example_for_overlay(
                    spec, ctx, config_rel
                )
            except Exception:
                example_text = ""
            if example_text:
                example_path = ctx.manifest_dir_container / ".dev.vars.example"
                if not example_path.exists():
                    try:
                        example_path.write_text(example_text, encoding="utf-8")
                    except OSError:
                        pass

        # Surface production-only bindings (informational env var). Adapters
        # downstream — CLI, dashboard hosted-app card — read this to render
        # "expected unavailable locally" badges so operators don't chase
        # phantom "down" markers for paid Cloudflare-platform features.
        prod_only = self._resolve_production_only_bindings(spec)
        if prod_only:
            environment["LECO_PRODUCTION_ONLY_BINDINGS"] = ",".join(sorted(prod_only))

        return {
            "image": image,
            # LEco builds the reference image lazily — the build context lives
            # in the ecosystem repo, not in the upstream app tree.
            "build": {
                "context": "${LECO_ECOSYSTEM_ROOT:-/project}/infra/runtimes/cloudflare-workers",
            },
            "container_name": ctx.runtime_container(runtime_id),
            "restart": "unless-stopped",
            "working_dir": "/app",
            "environment": environment,
            "volumes": volumes,
            "networks": ["default", "lh-network"],
            "expose": [str(port)],
            "healthcheck": {
                # Wrangler dev exposes any path; HEAD / returns a 404 from
                # Miniflare but the TCP connect is enough to confirm liveness.
                "test": [
                    "CMD-SHELL",
                    f"wget -qO- --spider http://127.0.0.1:{port}/ >/dev/null 2>&1 || exit 1",
                ],
                "interval": "15s",
                "timeout": "3s",
                "retries": 4,
                "start_period": "60s",
            },
        }

    def named_volumes(self, spec: dict[str, Any], ctx: RuntimeBuildContext) -> list[str]:
        runtime_id = str(spec.get("id") or "worker")
        return [
            ctx.named_volume(runtime_id, "node-modules"),
            ctx.named_volume(runtime_id, "wrangler-state"),
        ]

    def _render_dev_vars_example_for_overlay(
        self,
        spec: dict[str, Any],
        ctx: RuntimeBuildContext,
        config_rel: str,
    ) -> str:
        """Read the in-container wrangler.toml + Worker source and return a
        ``.dev.vars.example`` body. Empty if the scan yields zero expected
        secrets (in which case the operator never sees the file).
        """
        if ctx.manifest_root_container is None:
            return ""
        source_dir = (spec.get("sourceDir") or "").strip()
        worker_dir = ctx.manifest_root_container / source_dir if source_dir else ctx.manifest_root_container
        worker_dir = worker_dir.resolve()
        cfg = (worker_dir / config_rel).resolve()
        if not cfg.is_file():
            return ""
        try:
            wrangler_text = cfg.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""
        secrets = detect_expected_secrets(worker_dir, wrangler_text)
        if not secrets:
            return ""
        return render_dev_vars_example(secrets, ctx.app_slug)

    def _resolve_production_only_bindings(self, spec: dict[str, Any]) -> set[str]:
        """Effective list of production-only bindings to surface as informational badges.

        Defaults to :data:`INFORMATIONAL_PRODUCTION_ONLY_BINDINGS`. Operators
        can refine with ``infrastructure.runtimes[].productionOnlyBindings``
        (list or ``"none"``).
        """
        raw = spec.get("productionOnlyBindings", None)
        if raw is None:
            return set(INFORMATIONAL_PRODUCTION_ONLY_BINDINGS)
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in {"", "none", "off", "false"}:
                return set()
            return {p.strip().lower() for p in s.split(",") if p.strip()}
        if isinstance(raw, list):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
        return set(INFORMATIONAL_PRODUCTION_ONLY_BINDINGS)

    def _resolve_strip_list(self, spec: dict[str, Any]) -> set[str]:
        """Effective list of TOML sections to strip from the in-container wrangler.toml.

        Defaults to :data:`DEFAULT_LOCAL_UNSUPPORTED_BINDINGS` (currently
        ``browser``). When ``LECO_LOCAL_BROWSER_URL`` is set, ``browser`` is
        removed from the default strip list so the Wrangler bridge can forward
        to browser.lh. Operators can override per-app with
        ``infrastructure.runtimes[].stripBindings`` — either a list of names
        (e.g. ``["browser", "ai"]``) or ``"none"`` / ``[]`` to disable stripping.
        """
        raw = spec.get("stripBindings", None)
        if raw is None:
            defaults = set(DEFAULT_LOCAL_UNSUPPORTED_BINDINGS)
            if _LECO_LOCAL_BROWSER_URL:
                defaults.discard("browser")
            return defaults
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in {"", "none", "off", "false"}:
                return set()
            return {p.strip().lower() for p in s.split(",") if p.strip()}
        if isinstance(raw, list):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
        defaults = set(DEFAULT_LOCAL_UNSUPPORTED_BINDINGS)
        if _LECO_LOCAL_BROWSER_URL:
            defaults.discard("browser")
        return defaults


_WORKER_PATH_PATTERNS = (
    # Modern flat router style: url.pathname === '/foo'
    re.compile(r"""pathname\s*===?\s*['"]([^'"]+)['"]"""),
    # Prefix style: pathname.startsWith('/foo')
    re.compile(r"""pathname\.startsWith\(\s*['"]([^'"]+)['"]"""),
    # Hono / itty / etc.: app.get('/foo', ...), router.post("/bar", ...)
    re.compile(r"""\b(?:get|post|put|patch|delete|all|use)\(\s*['"]((?:/[^'"\s]+))['"]"""),
)

# Paths LEco never suggests routing to a runtime (they are SPA / asset / dev
# concerns owned by the static frontend, not the Worker).
_WORKER_PATH_IGNORE = {"/", "/index.html", "/favicon.ico", "/robots.txt"}


def _scan_worker_paths(worker_dir: Path) -> set[str]:
    """Read the Worker entrypoint(s) and return distinct top-level URL paths
    referenced via common router/path-matching idioms.

    Heuristic by design: best-effort hint, not a contract. Operators always
    have the final say in ``routing.entries[].upstream``. Scanning is bounded
    (≤200 KB per file, ≤6 candidate entrypoints) so onboarding stays fast on
    huge repos.
    """
    found: set[str] = set()
    candidate_files: list[Path] = []
    for rel in ("src/index.ts", "src/index.js", "index.ts", "index.js",
                "src/worker.ts", "src/worker.js"):
        candidate_files.append(worker_dir / rel)
    seen: set[Path] = set()
    for f in candidate_files:
        try:
            rp = f.resolve()
        except OSError:
            continue
        if rp in seen or not rp.is_file():
            continue
        seen.add(rp)
        try:
            text = rp.read_text(encoding="utf-8", errors="ignore")[:200_000]
        except OSError:
            continue
        for pat in _WORKER_PATH_PATTERNS:
            for m in pat.finditer(text):
                p = m.group(1).strip()
                if p.startswith("/") and p not in _WORKER_PATH_IGNORE:
                    found.add(p)
    return found


def _routing_hint_from_paths(paths: set[str]) -> str:
    """Collapse Worker-path findings into a short list of suggested rules.

    Rules:
    - ``/api*`` paths collapse to a single ``/api`` rule (any subroute of /api
      is going to be Worker-handled).
    - Anything else becomes its own exact-path rule.
    """
    out = _condense_worker_prefixes(paths)
    return ", ".join(f"{p} → runtime" for p in out)


def _condense_worker_prefixes(paths: set[str]) -> list[str]:
    """Return the minimal sorted list of prefixes that cover the input paths.

    Collapses any path starting with ``/api`` to ``/api`` (the Worker handles
    the whole tree). Keeps other findings as-is, sorted alphabetically so the
    output is stable.
    """
    api_seen = any(p == "/api" or p.startswith("/api/") for p in paths)
    explicit = sorted(p for p in paths if not (p == "/api" or p.startswith("/api/")))
    out: list[str] = []
    if api_seen:
        out.append("/api")
    out.extend(explicit)
    return out


def suggested_upstream_yaml(runtime_id: str, paths: set[str]) -> str:
    """Render a copy-pasteable ``routing.entries[].upstream[]`` block.

    The block does NOT include a ``/`` catch-all because we cannot know whether
    the operator's frontend service is named e.g. ``cv-frontend`` or
    ``<slug>-web`` — the wizard/CLI logs the suggestion next to a plain-English
    reminder that the operator needs to append the catch-all themselves.
    """
    prefixes = _condense_worker_prefixes(paths)
    if not prefixes:
        return ""
    lines = ["      upstream:"]
    for p in prefixes:
        lines.append(f"      - prefix: {p}")
        lines.append("        target: runtime")
        lines.append(f"        runtime: {runtime_id}")
    return "\n".join(lines)


def _collect_wrangler_known_names(wrangler_text: str) -> tuple[set[str], set[str]]:
    """Return ``(vars_keys, binding_names)`` from a ``wrangler.toml`` body.

    - ``vars_keys`` are UPPER_SNAKE keys defined under the top-level ``[vars]``
      table (sub-environment ``[env.staging.vars]`` etc. are ignored — they
      only ever ship to that named env, not to local dev).
    - ``binding_names`` are the values of ``binding = "<NAME>"`` lines anywhere
      in the file (D1, KV, R2, queues, vectorize, browser, AI, durable
      objects…). These are valid local references too, so we exclude them from
      the "missing secret" list.

    Pure text walker, no TOML parser: a wrangler.toml is operator-authored
    and we'd rather degrade gracefully than refuse to scan when a line trips
    a strict parser.
    """
    vars_keys: set[str] = set()
    bindings: set[str] = set()
    in_top_vars = False
    for raw in wrangler_text.splitlines():
        line = raw.rstrip()
        header = _TOML_TABLE_HEADER_RE.match(line)
        if header:
            name = header.group("name").strip()
            in_top_vars = name == "vars"
            continue
        if in_top_vars:
            m = _TOML_VARS_KEY_RE.match(line)
            if m:
                vars_keys.add(m.group(1))
        for m2 in _TOML_BINDING_VALUE_RE.finditer(line):
            bindings.add(m2.group(1))
    return vars_keys, bindings


def _scan_worker_env_refs(worker_dir: Path) -> set[str]:
    """Walk Worker source and return the set of UPPER_SNAKE ``env.<NAME>`` references.

    Bounded by ``_MAX_SOURCE_FILE_BYTES`` and ``_MAX_SOURCE_FILES_SCANNED`` so
    onboarding stays fast on big repos. We skip ``node_modules``, ``dist`` and
    ``.wrangler`` to stay inside the operator's own code.
    """
    if not worker_dir.is_dir():
        return set()
    found: set[str] = set()
    scanned = 0
    skip_dirs = {"node_modules", "dist", "build", ".wrangler", ".turbo", ".next", "public"}
    candidates: list[Path] = []
    for pattern in _WORKER_SOURCE_GLOBS:
        for path in worker_dir.glob(pattern):
            if any(part in skip_dirs for part in path.parts):
                continue
            candidates.append(path)
            if len(candidates) >= _MAX_SOURCE_FILES_SCANNED * 2:
                break
        if len(candidates) >= _MAX_SOURCE_FILES_SCANNED * 2:
            break
    # Stable order so repeated scans are deterministic.
    candidates.sort()
    for path in candidates:
        if scanned >= _MAX_SOURCE_FILES_SCANNED:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")[: _MAX_SOURCE_FILE_BYTES]
        except OSError:
            continue
        scanned += 1
        for m in _ENV_REF_RE.finditer(text):
            name = m.group(1) or m.group(2)
            if name:
                found.add(name)
    return found


# Names we never recommend as "missing secrets" even when referenced — they
# are platform / SDK conventions, not operator-supplied API keys, and listing
# them in a ``.dev.vars.example`` would just clutter the file.
_ENV_REF_IGNORE: frozenset[str] = frozenset(
    {
        "NODE_ENV",
        "DEBUG",
        "CI",
        "TZ",
        "LANG",
        # Wrangler injects these for the runtime itself.
        "ENVIRONMENT",
        "WRANGLER_LOG",
    }
)


def detect_expected_secrets(worker_dir: Path, wrangler_text: str) -> list[str]:
    """Return the ordered list of operator-supplied secrets the runtime expects.

    Algorithm:
        1. Read ``[vars]`` keys + ``binding = "NAME"`` values from ``wrangler.toml``.
        2. Grep the Worker source for ``env.<UPPER_SNAKE>`` / ``env["UPPER_SNAKE"]``.
        3. Subtract (1) and a small SDK/platform ignore list from (2).

    The result is what an operator needs to drop into
    ``hosting/app-available/<slug>/.dev.vars`` to actually exercise every
    feature locally — and it is exactly the set LEco materializes into
    ``.dev.vars.example`` so the file is self-documenting.
    """
    vars_keys, bindings = _collect_wrangler_known_names(wrangler_text)
    refs = _scan_worker_env_refs(worker_dir)
    expected = sorted(refs - vars_keys - bindings - _ENV_REF_IGNORE)
    return expected


def render_dev_vars_example(secrets: list[str], slug: str) -> str:
    """Render the ``.dev.vars.example`` body for a runtime's expected secrets.

    The file lists one ``KEY=`` placeholder per detected secret, grouped by
    coarse prefix (LLM / Payment / Email / Cloudflare / Other) so operators
    don't have to scan a flat 30-line list. Never contains real values.
    """
    if not secrets:
        return ""
    groups: dict[str, list[str]] = {
        "Cloudflare platform": [],
        "LLM providers": [],
        "Payments": [],
        "Email": [],
        "Other": [],
    }
    LLM_PREFIXES = ("OPENAI", "ANTHROPIC", "CLAUDE", "GEMINI", "GOOGLE_GEMINI", "PERPLEXITY", "XAI", "GROK", "EDEN_AI")
    PAY_PREFIXES = ("STRIPE", "RAZORPAY", "PAYPAL", "PAYTM")
    EMAIL_PREFIXES = ("BREVO", "RESEND", "POSTMARK", "MAILGUN", "SENDGRID", "SMTP")
    CF_PREFIXES = ("CF_", "CLOUDFLARE_", "WRANGLER_")
    for name in secrets:
        if any(name.startswith(p) for p in CF_PREFIXES):
            groups["Cloudflare platform"].append(name)
        elif any(name.startswith(p) for p in LLM_PREFIXES):
            groups["LLM providers"].append(name)
        elif any(name.startswith(p) for p in PAY_PREFIXES):
            groups["Payments"].append(name)
        elif any(name.startswith(p) for p in EMAIL_PREFIXES):
            groups["Email"].append(name)
        else:
            groups["Other"].append(name)

    header = (
        "# LEco DevOps — auto-generated .dev.vars.example\n"
        f"# App: {slug}\n"
        "# Drop a copy named `.dev.vars` next to this file, fill in real values,\n"
        "# then re-deploy. LEco bind-mounts `.dev.vars` into the runtime at\n"
        "# /app/.dev.vars (Wrangler local reads it automatically — no manifest\n"
        "# field needed). This file lists every UPPER_SNAKE `env.<NAME>` the\n"
        "# Worker source references that is NOT already declared in\n"
        "# wrangler.toml `[vars]` or as a binding. Regenerated each time LEco\n"
        "# materializes the runtime overlay (existing `.dev.vars` is never\n"
        "# touched).\n"
        "\n"
    )
    lines: list[str] = [header]
    for group, names in groups.items():
        if not names:
            continue
        lines.append(f"# --- {group} ---")
        for n in names:
            lines.append(f"{n}=")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _sanitize_wrangler_toml(text: str, strip_names: set[str]) -> tuple[str, set[str]]:
    """Remove top-level ``[name]`` and ``[[name]]`` sections from a TOML string.

    Returns ``(sanitized_text, set_of_actually_removed_names)``. We intentionally
    avoid a full TOML parser: wrangler.toml is operator-authored and we only
    need section-header awareness, which a tiny line walker handles with no
    third-party deps and zero risk of reformatting values.

    Sub-tables of stripped sections (e.g. ``[env.production.browser]``) are
    NOT touched — they are scoped to envs Wrangler local does not enter.
    """
    if not strip_names:
        return text, set()
    out_lines: list[str] = []
    removed: set[str] = set()
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n").rstrip("\r")
        m = _TOML_TABLE_HEADER_RE.match(stripped)
        if m:
            name = m.group("name").strip().lower()
            # Only strip top-level tables; sub-table names contain a '.'.
            if "." not in name and name in strip_names:
                skipping = True
                removed.add(name)
                continue
            skipping = False
        if skipping:
            # Drop the whole table body until the next [header].
            continue
        out_lines.append(line)
    return "".join(out_lines), removed

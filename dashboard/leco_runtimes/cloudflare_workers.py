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
DEFAULT_LOCAL_UNSUPPORTED_BINDINGS: tuple[str, ...] = ("browser",)

_TOML_TABLE_HEADER_RE = re.compile(r"^\s*\[\[?(?P<name>[A-Za-z_][A-Za-z0-9_.\- ]*)\]\]?\s*(#.*)?$")


class CloudflareWorkersAdapter(RuntimeAdapter):
    type = "cloudflare-workers"
    label = "Cloudflare Workers (Wrangler/Miniflare)"
    roadmap = ""  # fully implemented

    def detect(self, app_root: Path) -> RuntimeDetection | None:
        """Surface a candidate runtime spec when ``wrangler.toml`` exists.

        Beyond locating ``wrangler.toml``, we also scan the Worker entrypoint
        (``src/index.ts`` / ``index.js`` etc.) for top-level path patterns
        the Worker handles. Operators then know which prefixes to add to
        ``routing.entries[].upstream`` so paths like ``/health/json`` that
        production resolves at the edge don't accidentally fall through to
        a Docker-Compose frontend that serves an SPA shell.
        """
        if not app_root.is_dir():
            return None
        # Common layouts: ``<root>/cloudflare/wrangler.toml`` for monorepos that
        # carve the Worker out under its own directory (CrawlerVision-style),
        # ``<root>/worker[s]/wrangler.toml`` for similar variants, or
        # ``<root>/wrangler.toml`` for single-Worker repos. Some repos contain
        # BOTH (a top-level legacy stub plus the real subdirectory Worker), so
        # we prefer candidates whose sibling ``src/`` directory exists — that
        # is much more likely to be the active Worker than a stale stub.
        candidates: list[tuple[str | None, Path]] = [
            ("cloudflare", app_root / "cloudflare" / "wrangler.toml"),
            ("worker", app_root / "worker" / "wrangler.toml"),
            ("workers", app_root / "workers" / "wrangler.toml"),
            (None, app_root / "wrangler.toml"),
        ]
        # Re-rank so a wrangler.toml whose sibling src/ has TS/JS files comes
        # first. Ties keep declaration order (which already prefers subdirs).
        def _score(item: tuple[str | None, Path]) -> int:
            _, p = item
            if not p.is_file():
                return -1
            sibling_src = p.parent / "src"
            if sibling_src.is_dir():
                # Cheap signal: any TS/JS file under src/?
                for ext in ("*.ts", "*.tsx", "*.js", "*.mjs"):
                    if any(sibling_src.rglob(ext)):
                        return 2
                return 1
            return 0
        candidates.sort(key=_score, reverse=True)
        for source_dir, path in candidates:
            if path.is_file():
                spec: dict[str, Any] = {
                    "id": "worker",
                    "type": self.type,
                    "config": "wrangler.toml",
                    "port": DEFAULT_PORT,
                }
                if source_dir:
                    spec["sourceDir"] = source_dir
                worker_dir = path.parent
                worker_paths = _scan_worker_paths(worker_dir)
                detail = f"Found {path.relative_to(app_root)}"
                yaml_hint = ""
                if worker_paths:
                    routing_hint = _routing_hint_from_paths(worker_paths)
                    detail = (
                        f"{detail}; Worker paths detected: "
                        f"{', '.join(sorted(worker_paths)[:6])}"
                        + (f"  ➜ suggested upstream rules: {routing_hint}" if routing_hint else "")
                    )
                    yaml_hint = suggested_upstream_yaml("worker", worker_paths)
                return RuntimeDetection(
                    type=self.type,
                    runtime_id="worker",
                    spec=spec,
                    detail=detail,
                    suggested_upstream_yaml=yaml_hint,
                )
        return None

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

        dev_vars = (spec.get("devVarsFile") or "").strip()
        if dev_vars:
            dev_vars_host = (ctx.manifest_dir / dev_vars).resolve()
            # Bind the file (compose creates a dir if the source doesn't exist,
            # so we only emit the mount when the file is actually on disk).
            if dev_vars_host.is_file():
                volumes.append(
                    {
                        "type": "bind",
                        "source": str(dev_vars_host),
                        "target": "/app/.dev.vars",
                        "read_only": True,
                    }
                )

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

    def _resolve_strip_list(self, spec: dict[str, Any]) -> set[str]:
        """Effective list of TOML sections to strip from the in-container wrangler.toml.

        Defaults to :data:`DEFAULT_LOCAL_UNSUPPORTED_BINDINGS` (currently
        ``browser``). Operators can override per-app with
        ``infrastructure.runtimes[].stripBindings`` — either a list of names
        (e.g. ``["browser", "ai"]``) or ``"none"`` / ``[]`` to disable stripping.
        """
        raw = spec.get("stripBindings", None)
        if raw is None:
            return set(DEFAULT_LOCAL_UNSUPPORTED_BINDINGS)
        if isinstance(raw, str):
            s = raw.strip().lower()
            if s in {"", "none", "off", "false"}:
                return set()
            return {p.strip().lower() for p in s.split(",") if p.strip()}
        if isinstance(raw, list):
            return {str(x).strip().lower() for x in raw if str(x).strip()}
        return set(DEFAULT_LOCAL_UNSUPPORTED_BINDINGS)


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

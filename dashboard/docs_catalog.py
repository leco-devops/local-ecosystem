import os
from pathlib import Path

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")

# Whitelisted documentation files (relative to PROJECT_ROOT). IDs are stable API keys.
DOC_MODULES = [
    {
        "id": "ecosystem-readme",
        "title": "Local Ecosystem — README",
        "category": "Develop",
        "rel_path": "README.md",
        "blurb": "Main project guide, URLs, CLI, structure.",
    },
    {
        "id": "cf-readme",
        "title": "Cloudflare Local — README",
        "category": "Cloudflare Local",
        "rel_path": "cloudflare-local/README.md",
        "blurb": "Compose, scripts, quick start.",
    },
    {
        "id": "cf-architecture",
        "title": "Cloudflare Local — Architecture",
        "category": "Cloudflare Local",
        "rel_path": "cloudflare-local/docs/ARCHITECTURE.md",
        "blurb": "Components, topology, limits.",
    },
    {
        "id": "cf-user-manual",
        "title": "Cloudflare Local — User manual",
        "category": "Cloudflare Local",
        "rel_path": "cloudflare-local/docs/USER_MANUAL.md",
        "blurb": "Start/stop, URLs, backups, dashboard notes.",
    },
    {
        "id": "cf-implementation",
        "title": "Cloudflare Local — Implementation guide",
        "category": "Cloudflare Local",
        "rel_path": "cloudflare-local/docs/IMPLEMENTATION_GUIDE.md",
        "blurb": "Traefik, compose, adapters, dashboard wiring.",
    },
    {
        "id": "cf-browser-local",
        "title": "Browser rendering — local Docker",
        "category": "Cloudflare Local",
        "rel_path": "cloudflare-local/docs/BROWSER_RENDERING_LOCAL.md",
        "blurb": "Playwright vs Chromium CDP, URLs, optional CF REST bridge.",
    },
    {
        "id": "cf-browser-production",
        "title": "Browser rendering — Cloudflare production",
        "category": "Cloudflare Local",
        "rel_path": "cloudflare-local/docs/BROWSER_RENDERING_PRODUCTION.md",
        "blurb": "REST API and Workers bindings; not runnable in local Miniflare.",
    },
    {
        "id": "dev-playbook",
        "title": "Development playbook",
        "category": "Extending the platform",
        "rel_path": "docs/DEVELOPMENT_PLAYBOOK.md",
        "blurb": "Repo map, new services, APIs, security.",
    },
    {
        "id": "devops-guide",
        "title": "DevOps — deploy Workers, KV, R2, D1",
        "category": "DevOps",
        "rel_path": "docs/DEVOPS_GUIDE.md",
        "blurb": "Host deploy, compose, Traefik, Workers runtime, adapter APIs, ops checklist.",
    },
    {
        "id": "devops-setup",
        "title": "Setup — first-time install",
        "category": "DevOps",
        "rel_path": "docs/SETUP.md",
        "blurb": "DNS, TLS (mkcert), Docker, lh-network, service URLs.",
    },
    {
        "id": "devops-deployment",
        "title": "Deployment — day-two operations",
        "category": "DevOps",
        "rel_path": "docs/DEPLOYMENT.md",
        "blurb": "Start/stop, backups, bulk actions, stack scripts.",
    },
    {
        "id": "devops-custom-apps",
        "title": "Deploy custom apps — Workers, Docker, NGINX, Node",
        "category": "DevOps",
        "rel_path": "docs/DEPLOY_CUSTOM_APPS.md",
        "blurb": "Traefik routing, full service inventory, Workers vs containers, Node on infra, NGINX patterns.",
    },
    {
        "id": "devops-deploy-cli",
        "title": "LEco DevOps — deploy CLI (reference)",
        "category": "DevOps",
        "rel_path": "docs/DEPLOY_CLI.md",
        "blurb": "Technical reference: leco.app.yaml, leco.yaml, commands, Traefik, dashboard APIs.",
    },
    {
        "id": "leco-user-manual",
        "title": "LEco DevOps — User manual",
        "category": "DevOps",
        "rel_path": "docs/LECO_USER_MANUAL.md",
        "blurb": "What LEco DevOps is, workflows, dashboard registration, sidecar profile, security, troubleshooting.",
    },
    {
        "id": "leco-app-blueprint",
        "title": "LEco application blueprint",
        "category": "DevOps",
        "rel_path": "docs/LECO_APP_BLUEPRINT.md",
        "blurb": "Canonical map: v3 bridge vs profile, hosting materialization, compose, Traefik, offboard, code pointers.",
    },
    {
        "id": "service-management",
        "title": "Service management commands",
        "category": "Operations",
        "rel_path": "",
        "dynamic_content": True,
        "blurb": "CLI per Control target: AI-stack scripts, compose, bulk — same units as the Control tab.",
    },
]

_BY_ID = {m["id"]: m for m in DOC_MODULES}


def build_service_management_markdown() -> str:
    """CLI reference aligned with dashboard Control targets."""
    from control_targets import AI_TARGETS, CF_TARGETS, COMPOSE_REL, INFRA_COMPOSE_REL, INFRA_TARGETS

    lines = [
        "# Service management commands",
        "",
        "Run from the **repository root** on the host. These mirror the **Control** tab targets.",
        "",
        "## AI stack (`ai-stack/services/*.sh`)",
        "",
        "Replace `<action>` with `start`, `stop`, `restart`, `logs`, `status`, `remove`, `reset`, `pause`, `unpause`, … (see each script).",
        "",
    ]
    for t in AI_TARGETS:
        script = t["script"]
        label = t["label"]
        container = t.get("container") or "—"
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- **Container:** `{container}`")
        lines.append(f"- **Direct:** `./ai-stack/services/{script}.sh <action>`")
        lines.append(f"- **Orchestrator:** `./ai-stack/ai-stack.sh <action> {script}`")
        lines.append("")

    lines.extend(
        [
            "### Bulk — all AI-stack services",
            "",
            "- `./ai-stack/ai-stack.sh start` — full stack in `START_ORDER` (see `ai-stack/core.sh`)",
            "- `./ai-stack/ai-stack.sh stop`",
            "- `./ai-stack/ai-stack.sh restart`",
            "",
            "Dashboard **Control → All AI-stack services** uses `bulk_ecosystem` in `core.sh` plus a Python-only **backup** aggregate. Bulk **stop**, **restart**, **deploy**, **pause**, **remove**, **reset**, and **recreate** skip **dashboard** plus **traefik** and **postgres** by default (`ECOSYSTEM_BULK_PLATFORM_SKIP`; legacy `ECOSYSTEM_BULK_PAUSE_SKIP` if unset). **unpause** and **start** still touch every service. Full teardown of edge/DB/dashboard is via per-service CLI — see **DEPLOYMENT.md** (core infra).",
            "",
            "### Control API (same actions from automation)",
            "",
            "- `POST /api/control` with JSON `target_id: stack-ecosystem-all` and `action` set to any value from **ALLOWED_ACTIONS** in `dashboard/control.py` (start, stop, restart, deploy, pause, unpause, remove, reset, recreate, backup). Include `token` if `DASHBOARD_CONTROL_TOKEN` is set.",
            "- `POST /api/control/stream` — same JSON body; response is NDJSON (`{type:log,text}` lines, then `{type:done,result:{...}}`). The Control UI uses this for live command output.",
            "- `POST /api/leco/yaml-status` — JSON `path`, optional `app_id`; returns whether `leco.app.yaml` and the localhost profile exist (for gating **Register**).",
            "- `POST /api/leco/generate-yaml` — JSON `path`, `app_id`, `token`; writes manifest + profile from directory scan (regenerate); read-only roots also refresh `source` and symlinks for `configRefs` / compose / env / wrangler paths (`config_symlinks` in response).",
            "- `POST /api/leco/save-yaml` — JSON `path`, `app_id`, `manifest_yaml`, `localhost_yaml`, `token`; validates and saves editor content; read-only roots same symlink behavior as generate-yaml.",
            "- `POST /api/leco/register/stream` — JSON `path`, `app_id`, `label`, `deploy_stack`, `token`; NDJSON stream for `leco-app ecosystem-register` (YAML must already exist on disk).",
            "",
            "## Cloudflare local (`docker compose`)",
            "",
            f"Compose file (from repo root): `{COMPOSE_REL}`",
            "",
        ]
    )

    for t in CF_TARGETS:
        svc = t["compose_service"]
        label = t["label"]
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- **Compose service:** `{svc}`")
        lines.append(f"- **Up (build):** `docker compose -f {COMPOSE_REL} up -d --build {svc}`")
        lines.append(f"- **Stop:** `docker compose -f {COMPOSE_REL} stop {svc}`")
        lines.append(f"- **Restart:** `docker compose -f {COMPOSE_REL} restart {svc}`")
        lines.append(f"- **Logs:** `docker compose -f {COMPOSE_REL} logs -f {svc}`")
        lines.append("")

    lines.extend(
        [
            "## Infra add-ons (`infra/docker-compose.yml`)",
            "",
            "Per-service targets match the **Control** tab **Infra add-ons** group (MySQL, Redis, Mailpit, Adminer, …).",
            "",
            f"Compose file (from repo root): `{INFRA_COMPOSE_REL}`",
            "",
        ]
    )
    for t in INFRA_TARGETS:
        svc = t["compose_service"]
        label = t["label"]
        lines.append(f"### {label}")
        lines.append("")
        lines.append(f"- **Compose service:** `{svc}`")
        lines.append(f"- **Up (build):** `docker compose -f {INFRA_COMPOSE_REL} up -d --build {svc}`")
        lines.append(f"- **Stop:** `docker compose -f {INFRA_COMPOSE_REL} stop {svc}`")
        lines.append(f"- **Restart:** `docker compose -f {INFRA_COMPOSE_REL} restart {svc}`")
        lines.append(f"- **Logs:** `docker compose -f {INFRA_COMPOSE_REL} logs -f {svc}`")
        lines.append("")

    lines.extend(
        [
            "### Entire infra stack",
            "",
            f"- **Up (build):** `docker compose -f {INFRA_COMPOSE_REL} up -d --build`",
            f"- **Stop:** `docker compose -f {INFRA_COMPOSE_REL} stop`",
            f"- **Down:** `docker compose -f {INFRA_COMPOSE_REL} down --remove-orphans`",
            "",
            "- **Script:** `./ai-stack/services/infra.sh start|stop|restart|remove|reset|logs|status`",
            "",
            "### Entire Cloudflare-local stack",
            "",
            f"- **Up (build):** `docker compose -f {COMPOSE_REL} up -d --build`",
            f"- **Stop:** `docker compose -f {COMPOSE_REL} stop`",
            f"- **Down:** `docker compose -f {COMPOSE_REL} down --remove-orphans`",
            "",
            "- **Script:** `./ai-stack/services/cloudflare-local.sh start|stop|restart|remove|reset|logs|status`",
            "",
            "### Whole stack (Control targets `stack-cf-all` · `stack-infra-all`)",
            "",
            "- **Cloudflare local:** `POST /api/control` with `target_id` `stack-cf-all` and `action` `deploy` / `stop` / `restart` / …",
            "- **Infra compose:** `POST /api/control` with `target_id` `stack-infra-all` and the same actions (backup is not implemented for infra).",
            "",
            "## Ollama pinned models",
            "",
            "- List file: `ai-stack/config/ollama-pinned-models.txt`",
            "- Pull pinned into a running Ollama: `./ai-stack/ai-stack.sh ollama-pull-models`",
            "- Dashboard Infrastructure tab lists **all** models from Ollama `GET /api/tags`, RAM state from `/api/ps`, **Insights** = `POST /api/show`.",
            "- Backups: JSON manifests under `.local-eco-backups/ollama-manifest-*.json` (restore updates the pinned file only).",
            "",
            "## Auto-start, network, repair",
            "",
            "- **AI-stack containers** (Traefik, WebUI, Ollama, Postgres, n8n, dashboard): each `services/*.sh start` creates `lh-network` if missing and uses `--restart unless-stopped`.",
            "- **Cloudflare-local:** `docker-compose.yml` sets `restart: unless-stopped` on every service.",
            "- **One-shot fix:** `./ai-stack/ai-stack.sh repair-network` — creates `lh-network`, `docker network connect`s every name in `core.sh` `NETWORK_CONTAINERS`, and `docker update --restart unless-stopped` on each running container.",
            "- **Dashboard image:** `./ai-stack/services/dashboard.sh start` (or `deploy`) rebuilds and runs the ops dashboard.",
            "",
        ]
    )

    return "\n".join(lines)


def _safe_resolve(rel_path: str) -> Path | None:
    root = Path(PROJECT_ROOT).resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def get_doc_catalog():
    out = []
    for m in DOC_MODULES:
        if m.get("dynamic_content"):
            present = True
            rel = m.get("rel_path") or ""
        else:
            p = _safe_resolve(m["rel_path"])
            present = p is not None and p.is_file()
            rel = m["rel_path"]
        out.append(
            {
                "id": m["id"],
                "title": m["title"],
                "category": m["category"],
                "blurb": m["blurb"],
                "path": rel,
                "available": present,
            }
        )
    return {
        "project_root": PROJECT_ROOT,
        "modules": out,
    }


def get_doc_content(doc_id: str):
    meta = _BY_ID.get(doc_id)
    if not meta:
        return None, "unknown document id"
    if meta.get("dynamic_content"):
        try:
            md = build_service_management_markdown()
        except Exception as exc:
            return None, str(exc)
        return {
            "id": doc_id,
            "title": meta["title"],
            "markdown": md,
            "synthetic": False,
            "path": meta.get("rel_path") or "",
        }, None
    path = _safe_resolve(meta["rel_path"])
    if path is None or not path.is_file():
        return (
            {
                "id": doc_id,
                "title": meta["title"],
                "markdown": (
                    f"# {meta['title']}\n\n"
                    "The source file is not available inside the dashboard container.\n\n"
                    f"- Expected path on the host: `{meta['rel_path']}` under the repo root.\n"
                    "- Ensure the dashboard container mounts the repo: `-v \"$PROJECT_ROOT:/project:rw\"` "
                    "(see `ai-stack/services/dashboard.sh`).\n"
                    "- Rebuild/restart the dashboard after changing compose or mount.\n"
                ),
                "synthetic": True,
                "path": meta.get("rel_path") or "",
            },
            None,
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, str(exc)
    return {
        "id": doc_id,
        "title": meta["title"],
        "markdown": text,
        "synthetic": False,
        "path": meta.get("rel_path") or "",
    }, None

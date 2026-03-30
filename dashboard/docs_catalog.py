import os
from pathlib import Path

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")

# Whitelisted documentation files (relative to PROJECT_ROOT). IDs are stable API keys.
DOC_MODULES = [
    {
        "id": "ecosystem-readme",
        "title": "Local Ecosystem — README",
        "category": "Overview",
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
        "id": "dev-playbook",
        "title": "Development playbook",
        "category": "Extending the platform",
        "rel_path": "docs/DEVELOPMENT_PLAYBOOK.md",
        "blurb": "Repo map, new services, APIs, security.",
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
    from control_targets import AI_TARGETS, CF_TARGETS, COMPOSE_REL

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
            "Dashboard **Control → All AI-stack services** uses `bulk_ecosystem` in `core.sh` (stop phases skip this dashboard so the API can finish).",
            "",
            "### Control API (same actions from automation)",
            "",
            "- `POST /api/control` with JSON `{ \"target_id\": \"stack-ecosystem-all\", \"action\": \"start\"|\"stop\"|\"restart\"|\"deploy\", \"token\": \"…\" }` (token if `DASHBOARD_CONTROL_TOKEN` is set).",
            "- `POST /api/control/stream` — same JSON body; response is NDJSON (`{type:log,text}` lines, then `{type:done,result:{...}}`). The Control UI uses this for live command output.",
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
            "### Entire Cloudflare-local stack",
            "",
            f"- **Up (build):** `docker compose -f {COMPOSE_REL} up -d --build`",
            f"- **Stop:** `docker compose -f {COMPOSE_REL} stop`",
            f"- **Down:** `docker compose -f {COMPOSE_REL} down --remove-orphans`",
            "",
            "- **Script:** `./ai-stack/services/cloudflare-local.sh start|stop|restart|remove|reset|logs|status`",
            "",
            "### Whole stack (Control target `stack-cf-all`)",
            "",
            "- Same as above; or `POST /api/control` with `target_id` `stack-cf-all` and `action` `deploy` / `stop` / `restart` / …",
            "",
            "## Ollama pinned models",
            "",
            "- List file: `ai-stack/config/ollama-pinned-models.txt`",
            "- Pull pinned into a running Ollama: `./ai-stack/ai-stack.sh ollama-pull-models`",
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
        return {"id": doc_id, "title": meta["title"], "markdown": md, "synthetic": False}, None
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
            },
            None,
        )
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return None, str(exc)
    return {"id": doc_id, "title": meta["title"], "markdown": text, "synthetic": False}, None

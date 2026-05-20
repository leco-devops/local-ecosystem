import os
from pathlib import Path

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")

# Whitelisted documentation files (relative to PROJECT_ROOT). IDs are stable API keys.
DOC_MODULES = [
    {
        "id": "ecosystem-readme",
        "title": "Platform stack — repository guide",
        "category": "Develop",
        "rel_path": "docs/PROJECT.md",
        "blurb": "LEco DevOps Open Project guide: identity, URLs, CLI, and repository structure.",
    },
    {
        "id": "open-source-stewardship",
        "title": "Open source — stewardship",
        "category": "Open source",
        "rel_path": "docs/OPEN_SOURCE.md",
        "blurb": "MIT license, Techtonic Systems Media And Research LLC stewardship, and contributor pointers.",
    },
    {
        "id": "open-source-notice",
        "title": "Open source — NOTICE",
        "category": "Open source",
        "rel_path": "NOTICE.md",
        "blurb": "Copyright notice and third-party attribution.",
    },
    {
        "id": "architecture-overview",
        "title": "Architecture overview",
        "category": "Architecture",
        "rel_path": "docs/ARCHITECTURE.md",
        "blurb": "System context, topology, and reading order across architecture docs.",
    },
    {
        "id": "architecture-hld",
        "title": "Architecture — HLD",
        "category": "Architecture",
        "rel_path": "docs/HLD.md",
        "blurb": "High-level design: layers, responsibilities, and core flows.",
    },
    {
        "id": "architecture-lld",
        "title": "Architecture — LLD",
        "category": "Architecture",
        "rel_path": "docs/LLD.md",
        "blurb": "Low-level design: modules, APIs, data contracts, and sequence details.",
    },
    {
        "id": "leco-tooling",
        "title": "LEco tooling reference",
        "category": "Architecture",
        "rel_path": "docs/LECO_TOOLING.md",
        "blurb": "CLI, manifest, registry, and dashboard integration map.",
    },
    {
        "id": "agents-guide",
        "title": "Agent guide (AGENTS.md)",
        "category": "Architecture",
        "rel_path": "AGENTS.md",
        "blurb": "Automation context, guardrails, and validation checklist for agents.",
    },
    {
        "id": "open-source-license",
        "title": "Open source — MIT license",
        "category": "Open source",
        "rel_path": "LICENSE",
        "blurb": "Project license terms for use, distribution, and contribution.",
    },
    {
        "id": "open-source-contributing",
        "title": "Open source — Contributing",
        "category": "Open source",
        "rel_path": "CONTRIBUTING.md",
        "blurb": "How to contribute, local setup pointers, and safety notes.",
    },
    {
        "id": "open-source-security",
        "title": "Open source — Security policy",
        "category": "Open source",
        "rel_path": "SECURITY.md",
        "blurb": "How to privately report vulnerabilities.",
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
        "id": "cf-leco-service-map",
        "title": "Cloudflare ↔ LEco service map",
        "category": "Cloudflare Local",
        "rel_path": "docs/CF_LECO_SERVICE_MAP.md",
        "blurb": "Binding coverage, reuse rules, dual-path architecture, and adapter roadmap.",
    },
    {
        "id": "ui-credential-vault",
        "title": "UI credential vault (local dev)",
        "category": "Operations",
        "rel_path": "docs/UI_CREDENTIAL_VAULT.md",
        "blurb": "Store UI logins, signed-in assist links, and reset/apply for MinIO, Adminer, and registry services.",
    },
    {
        "id": "project-changelog",
        "title": "Changelog",
        "category": "Project",
        "rel_path": "CHANGELOG.md",
        "blurb": "Keep a Changelog history for the LEco DevOps Open Project.",
    },
    {
        "id": "project-release-notes",
        "title": "Release notes",
        "category": "Project",
        "rel_path": "docs/RELEASE_NOTES.md",
        "blurb": "Release index, current version, and upgrade pointers.",
    },
    {
        "id": "project-versioning",
        "title": "Versioning policy",
        "category": "Project",
        "rel_path": "docs/VERSIONING.md",
        "blurb": "SemVer, VERSION/version.json, release workflow, and file manifests.",
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
        "id": "airllm-integration",
        "title": "AirLLM Integration",
        "category": "DevOps",
        "rel_path": "docs/AIRLLM_INTEGRATION.md",
        "blurb": "Large HF model inference (70B/405B) via AirLLM layer-by-layer loading; runs as the `airllm` Docker container with an Ollama-compatible API.",
    },
    {
        "id": "hosted-apps-traefik-runbook",
        "title": "Hosted apps — Traefik runbook",
        "category": "DevOps",
        "rel_path": "docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md",
        "blurb": "502, lh-network, compose DNS names, wrong-prefix routing, dashboard *.lh probes, same-origin /api, local edge runtimes (Workers/Pages/Vercel/Lambda/Deno) — symptoms, fixes, code map.",
    },
    {
        "id": "hosted-app-attached-services",
        "title": "Hosted apps — attached services",
        "category": "DevOps",
        "rel_path": "docs/help/12-hosted-app-attached-services.md",
        "blurb": "Per-app data stores, credentials, host vs Docker DNS connection strings, Compass/Adminer links (dashboard Hosted apps detail).",
    },
    {
        "id": "hosted-app-data-import",
        "title": "Hosted apps — seed data import",
        "category": "DevOps",
        "rel_path": "docs/help/13-hosted-app-data-import.md",
        "blurb": "data/ folder convention, CLI pipe mongodump|mongorestore, dashboard Import data stream, per-store cookbooks.",
    },
    {
        "id": "cloud-vm-deployment",
        "title": "Cloud VM deployment",
        "category": "DevOps",
        "rel_path": "docs/CLOUD_VM_DEPLOYMENT.md",
        "blurb": "Install profiles, custom domain/TLS, dev stack builder, Repair/Reinstall, Platform tab, ai-cloud external LLM keys.",
    },
    {
        "id": "help-platform-tab",
        "title": "Help — Platform tab & dev stacks",
        "category": "DevOps",
        "rel_path": "docs/help/03-platform-tab.md",
        "blurb": "User manual: dev stack builder, presets, lifecycle actions, stack cards, cloud VM URLs.",
    },
    {
        "id": "dev-stack-isolation",
        "title": "Dev stack isolation",
        "category": "DevOps",
        "rel_path": "docs/DEV_STACK_ISOLATION.md",
        "blurb": "Isolated compose projects, repair/reinstall/destroy, networks, platform.devStackId binding.",
    },
    {
        "id": "srs-cloud-vm-platform",
        "title": "SRS — Cloud VM platform",
        "category": "Architecture",
        "rel_path": "docs/SRS_CLOUD_VM_PLATFORM.md",
        "blurb": "Functional requirements for cloud VM profiles, dev stacks, TLS, and dashboard platform APIs.",
    },
    {
        "id": "help-cloud-vm-deployment",
        "title": "Help — Cloud VM deployment",
        "category": "DevOps",
        "rel_path": "docs/help/12-cloud-vm-deployment.md",
        "blurb": "Operator guide: profiles, platform settings, dev stacks, hosted apps on a cloud VM.",
    },
    {
        "id": "dev-platform-cloud",
        "title": "Platform cloud APIs (developers)",
        "category": "Develop",
        "rel_path": "docs/help/dev-09-platform-cloud.md",
        "blurb": "REST APIs, repair/reinstall/stream, component catalog, dev stack generator, platform.devStackId.",
    },
    {
        "id": "dev-data-import",
        "title": "Data import (developers)",
        "category": "Develop",
        "rel_path": "docs/help/dev-09-data-import.md",
        "blurb": "Import orchestrator, manifest schema, NDJSON API, ImportContext, adding importer plugins.",
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
        "blurb": "CLI per Control target: ecosystem-stack scripts, compose, bulk — same units as the Control tab.",
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
        "- **Foundation installer:** `./ecosystem-stack/install-foundation.sh` (checks dependencies, prompts service-by-service start selection).",
        "",
        "## Ecosystem stack (`ecosystem-stack/services/*.sh`)",
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
        lines.append(f"- **Direct:** `./ecosystem-stack/services/{script}.sh <action>`")
        lines.append(f"- **Orchestrator:** `./ecosystem-stack/ecosystem-stack.sh <action> {script}`")
        lines.append("")

    lines.extend(
        [
            "### Bulk — all ecosystem stack services",
            "",
            "- `./ecosystem-stack/ecosystem-stack.sh start` — full stack in `START_ORDER` (see `ecosystem-stack/core.sh`)",
            "- `./ecosystem-stack/ecosystem-stack.sh stop`",
            "- `./ecosystem-stack/ecosystem-stack.sh restart`",
            "",
            "Dashboard **Control → All ecosystem stack services** uses `bulk_ecosystem` in `core.sh` plus a Python-only **backup** aggregate. Bulk **stop**, **restart**, **deploy**, **pause**, **remove**, **reset**, and **recreate** skip **dashboard** plus **traefik** and **postgres** by default (`ECOSYSTEM_BULK_PLATFORM_SKIP`; legacy `ECOSYSTEM_BULK_PAUSE_SKIP` if unset). **unpause** and **start** still touch every service. Full teardown of edge/DB/dashboard is via per-service CLI — see **DEPLOYMENT.md** (core infra).",
            "",
            "### Control API (same actions from automation)",
            "",
            "- `POST /api/control` with JSON `target_id: stack-ecosystem-all` and `action` set to any value from **ALLOWED_ACTIONS** in `dashboard/control.py` (start, stop, restart, deploy, pause, unpause, remove, reset, recreate, backup). Include `token` if `DASHBOARD_CONTROL_TOKEN` is set.",
            "- `POST /api/control/stream` — same JSON body; response is NDJSON (`{type:log,text}` lines, then `{type:done,result:{...}}`). The Control UI uses this for live command output.",
            "- `POST /api/leco/yaml-status` — JSON `path`, optional `app_id`; returns whether `leco.app.yaml` and the localhost profile exist (for gating **Register**).",
            "- `POST /api/leco/generate-yaml` — JSON `path`, `app_id`, `token`; writes manifest + profile from directory scan (regenerate); read-only roots also refresh `source` and config symlinks for `configRefs`, each `infrastructure.runtimes[].config`, and discovered `wrangler.*.toml` paths (`config_symlinks` in response).",
            "- `POST /api/leco/save-yaml` — JSON `path`, `app_id`, `manifest_yaml`, `localhost_yaml`, `token`; validates and saves editor content; read-only roots same symlink behavior as generate-yaml.",
            "- `POST /api/hosted-apps/<slug>/validate-configuration` — no token; reads `leco.app.yaml` + profile from disk; returns `validation_ok`, `summary_text`, `reference_errors` / `reference_warnings` (Pydantic schema + merged-manifest file path checks).",
            "- `POST /api/leco/register/stream` — JSON `path`, `app_id`, `label`, `deploy_stack`, `token`; NDJSON stream for `leco-devops ecosystem-register` (YAML must already exist on disk).",
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
            "- **Script:** `./ecosystem-stack/services/infra.sh start|stop|restart|remove|reset|logs|status`",
            "",
            "### Entire Cloudflare-local stack",
            "",
            f"- **Up (build):** `docker compose -f {COMPOSE_REL} up -d --build`",
            f"- **Stop:** `docker compose -f {COMPOSE_REL} stop`",
            f"- **Down:** `docker compose -f {COMPOSE_REL} down --remove-orphans`",
            "",
            "- **Script:** `./ecosystem-stack/services/cloudflare-local.sh start|stop|restart|remove|reset|logs|status`",
            "",
            "### Whole stack (Control targets `stack-cf-all` · `stack-infra-all`)",
            "",
            "- **Cloudflare local:** `POST /api/control` with `target_id` `stack-cf-all` and `action` `deploy` / `stop` / `restart` / …",
            "- **Infra compose:** `POST /api/control` with `target_id` `stack-infra-all` and the same actions (backup is not implemented for infra).",
            "",
            "## Ollama pinned models",
            "",
            "- List file: `ecosystem-stack/config/ollama-pinned-models.txt`",
            "- Pull pinned into a running Ollama: `./ecosystem-stack/ecosystem-stack.sh ollama-pull-models`",
            "- LEco DevOps **Infrastructure** tab lists **all** models from Ollama `GET /api/tags`, RAM state from `/api/ps`, **Insights** = `POST /api/show`.",
            "- Backups: JSON manifests under `.local-eco-backups/ollama-manifest-*.json` (restore updates the pinned file only).",
            "",
            "## AirLLM pinned models",
            "",
            "- List file: `ecosystem-stack/config/airllm-pinned-models.txt` (HuggingFace repo ids; safetensors, not GGUF).",
            "- Pull pinned into the running `airllm` container: `./ecosystem-stack/ecosystem-stack.sh airllm-pull-models` (or `./leco-cli.sh airllm pull`).",
            "- LEco DevOps **Infrastructure** tab lists cached HF models from the shim `GET /api/tags` at `http://airllm:11435`.",
            "- Backups: JSON manifests under `.local-eco-backups/airllm-manifest-*.json`.",
            "- Runs as a Docker container on `lh-network` (CPU on macOS; rebuild with CUDA torch + `--gpus=all` on Linux for GPU acceleration).",
            "",
            "## Auto-start, network, repair",
            "",
            "- **Ecosystem stack containers** (Traefik, WebUI, Ollama, Postgres, n8n, LEco DevOps): each `services/*.sh start` creates `lh-network` if missing and uses `--restart unless-stopped`.",
            "- **Cloudflare-local:** `docker-compose.yml` sets `restart: unless-stopped` on every service.",
            "- **One-shot fix:** `./ecosystem-stack/ecosystem-stack.sh repair-network` — creates `lh-network`, `docker network connect`s every name in `core.sh` `NETWORK_CONTAINERS`, and `docker update --restart unless-stopped` on each running container.",
            "- **LEco DevOps image:** `./ecosystem-stack/services/dashboard.sh start` (or `deploy`) rebuilds and runs the container.",
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
                    "(see `ecosystem-stack/services/dashboard.sh`).\n"
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

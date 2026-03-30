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
]

_BY_ID = {m["id"]: m for m in DOC_MODULES}


def _safe_resolve(rel_path: str) -> Path | None:
    root = Path(PROJECT_ROOT).resolve()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def get_doc_catalog():
    root = Path(PROJECT_ROOT)
    out = []
    for m in DOC_MODULES:
        p = _safe_resolve(m["rel_path"])
        present = p is not None and p.is_file()
        out.append(
            {
                "id": m["id"],
                "title": m["title"],
                "category": m["category"],
                "blurb": m["blurb"],
                "path": m["rel_path"],
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

"""Structured Help & User Manual for the LEco DevOps dashboard (/help).

Content lives under docs/help/*.md (repo-relative). The tree is defined here so the
UI can render a multi-level navigation without parsing markdown headings.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
HELP_DIR = PROJECT_ROOT / "docs" / "help"

# Multi-level tree: folders may have `children`; leaves have `file` (under docs/help/).
HELP_TREE: list[dict] = [
    {
        "id": "welcome",
        "title": "Welcome",
        "file": "00-welcome.md",
    },
    {
        "id": "architecture-diagrams",
        "title": "Architecture & diagrams",
        "file": "13-architecture-diagrams.md",
    },
    {
        "id": "requirements",
        "title": "Requirements & prerequisites",
        "file": "01-requirements.md",
    },
    {
        "id": "installation",
        "title": "Installation",
        "children": [
            {"id": "install-stack", "title": "Ecosystem stack (first-time)", "file": "02-install-stack.md"},
            {"id": "install-cli", "title": "LEco CLI (leco-devops)", "file": "02-install-cli.md"},
            {"id": "install-dns", "title": "DNS (*.lh) and certificates", "file": "02-install-dns.md"},
        ],
    },
    {
        "id": "daily-use",
        "title": "Daily operations",
        "children": [
            {"id": "dash-overview", "title": "Dashboard tour", "file": "03-dashboard.md"},
            {"id": "dash-control", "title": "Control tab (start/stop)", "file": "03-control.md"},
            {"id": "dash-infra", "title": "Infrastructure tab", "file": "03-infrastructure.md"},
        ],
    },
    {
        "id": "llm",
        "title": "Local AI (Ollama & AirLLM)",
        "children": [
            {"id": "ollama", "title": "Ollama (GGUF models)", "file": "04-ollama.md"},
            {"id": "airllm", "title": "AirLLM (large HF models)", "file": "05-airllm.md"},
            {"id": "llm-compare", "title": "Ollama vs AirLLM", "file": "04-llm-compare.md"},
        ],
    },
    {
        "id": "updates",
        "title": "Updates & LLM catalogs",
        "children": [
            {"id": "update-catalog-service", "title": "Update catalog service", "file": "14-update-catalog-service.md"},
            {"id": "ecosystem-updates", "title": "Stack & model updates (live)", "file": "generated/14-ecosystem-updates.md"},
            {"id": "llm-catalog-ollama", "title": "Ollama LLM catalog (live table)", "file": "generated/15-llm-catalog-ollama.md"},
            {"id": "llm-catalog-airllm", "title": "AirLLM LLM catalog (live table)", "file": "generated/16-llm-catalog-airllm.md"},
        ],
    },
    {
        "id": "hosting",
        "title": "Hosting & onboarding",
        "children": [
            {"id": "hosting-layout", "title": "Hosting layout & components", "file": "12-hosting-layout.md"},
            {"id": "onboarding-overview", "title": "Onboarding new apps", "file": "12-onboarding-overview.md"},
            {"id": "onboarding-materialize", "title": "wsp: paths & materialize", "file": "12-onboarding-materialize.md"},
            {"id": "multi-wrangler-monorepo", "title": "Multi-Wrangler monorepos", "file": "12-multi-wrangler-monorepo.md"},
            {"id": "hosted-app-attached-services", "title": "Attached services panel", "file": "12-hosted-app-attached-services.md"},
            {"id": "hosting-overrides", "title": "Overriding upstream apps", "file": "12-hosting-overrides.md"},
            {"id": "deploy-rebuild", "title": "Deploy, rebuild & offload", "file": "12-deploy-rebuild.md"},
        ],
    },
    {
        "id": "ai-news",
        "title": "AI news aggregator",
        "file": "18-ai-news.md",
    },
    {
        "id": "cli",
        "title": "LEco CLI reference",
        "children": [
            {"id": "cli-basics", "title": "leco-devops basics", "file": "06-cli.md"},
            {"id": "hosted-apps", "title": "Hosted apps (dashboard)", "file": "07-hosted-apps.md"},
            {"id": "traefik-routes", "title": "Traefik routes", "file": "07-traefik.md"},
        ],
    },
    {
        "id": "developer-guide",
        "title": "Developer's guide",
        "children": [
            {"id": "dev-overview", "title": "Codebase overview", "file": "dev-00-overview.md"},
            {"id": "dev-dashboard", "title": "Dashboard architecture", "file": "dev-01-dashboard.md"},
            {"id": "dev-cli", "title": "CLI & schema", "file": "dev-02-cli.md"},
            {"id": "dev-registration-flow", "title": "Registration data flow", "file": "dev-03-registration-flow.md"},
            {"id": "dev-traefik", "title": "Traefik & routing code", "file": "dev-04-traefik.md"},
            {"id": "dev-ecosystem-stack", "title": "Ecosystem stack", "file": "dev-05-ecosystem-stack.md"},
            {"id": "dev-extending", "title": "Extending LEco", "file": "dev-06-extending.md"},
            {"id": "dev-debugging", "title": "Debugging & validation", "file": "dev-07-debugging.md"},
            {"id": "dev-hosted-app-services", "title": "Attached services (API)", "file": "dev-08-hosted-app-services.md"},
        ],
    },
    {
        "id": "cloudflare",
        "title": "Cloudflare local",
        "file": "08-cloudflare-local.md",
    },
    {
        "id": "troubleshooting",
        "title": "Troubleshooting",
        "children": [
            {"id": "ts-common", "title": "Common issues", "file": "09-troubleshooting.md"},
            {"id": "ts-502", "title": "502 / routing / lh-network", "file": "09-502-routing.md"},
        ],
    },
    {
        "id": "removal",
        "title": "Removal & uninstall",
        "file": "10-removal.md",
    },
    {
        "id": "reference",
        "title": "Further reading",
        "children": [
            {
                "id": "ref-docs-tab",
                "title": "Technical docs (Docs tab)",
                "file": "11-further-reading.md",
            },
            {
                "id": "releases-versioning",
                "title": "Releases & versioning",
                "file": "17-releases-versioning.md",
            },
        ],
    },
]


def _flatten_tree(nodes: list[dict], trail: list[str] | None = None) -> list[dict]:
    """Depth-first list of leaves with breadcrumb trail."""
    trail = trail or []
    out: list[dict] = []
    for node in nodes:
        title = str(node.get("title") or "")
        path = [*trail, title] if title else list(trail)
        children = node.get("children")
        if children:
            out.extend(_flatten_tree(children, path))
            continue
        fid = str(node.get("id") or "")
        rel = node.get("file")
        if fid and rel:
            out.append(
                {
                    "id": fid,
                    "title": title,
                    "file": str(rel),
                    "breadcrumb": " › ".join(path),
                }
            )
    return out


def _node_by_id(nodes: list[dict], node_id: str) -> dict | None:
    for node in nodes:
        if node.get("id") == node_id:
            return node
        found = _node_by_id(node.get("children") or [], node_id)
        if found:
            return found
    return None


def get_help_tree() -> dict:
    return {"ok": True, "tree": HELP_TREE}


def get_help_content(node_id: str) -> tuple[dict | None, str | None]:
    node = _node_by_id(HELP_TREE, node_id)
    if not node:
        return None, f"unknown help topic: {node_id}"
    rel = node.get("file")
    if not rel:
        return None, "topic has no content file (folder only)"
    path = HELP_DIR / str(rel)
    if not path.is_file():
        if str(rel).startswith("generated/"):
            stub = (
                f"# {node.get('title', 'Updates')}\n\n"
                "_Catalog not generated yet._\n\n"
                "Run:\n\n"
                "```bash\n"
                "./ecosystem-stack/services/update-catalog.sh run-once\n"
                "```\n\n"
                "Or start the background watcher:\n\n"
                "```bash\n"
                "./ecosystem-stack/services/update-catalog.sh start\n"
                "```\n\n"
                "See [Update catalog service](help:update-catalog-service).\n"
            )
            leaves = _flatten_tree(HELP_TREE)
            crumb = next((x["breadcrumb"] for x in leaves if x["id"] == node_id), node.get("title", ""))
            return {
                "ok": True,
                "id": node_id,
                "title": node.get("title", ""),
                "breadcrumb": crumb,
                "path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
                "markdown": stub,
                "generated_stub": True,
            }, None
        return None, f"missing help file: {path}"
    try:
        markdown = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, str(exc)
    leaves = _flatten_tree(HELP_TREE)
    crumb = next((x["breadcrumb"] for x in leaves if x["id"] == node_id), node.get("title", ""))
    return {
        "ok": True,
        "id": node_id,
        "title": node.get("title", ""),
        "breadcrumb": crumb,
        "path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
        "markdown": markdown,
    }, None


def search_help(query: str, limit: int = 40) -> dict:
    q = (query or "").strip().lower()
    if len(q) < 2:
        return {"ok": True, "query": query, "results": []}
    leaves = _flatten_tree(HELP_TREE)
    results: list[dict] = []
    for leaf in leaves:
        path = HELP_DIR / leaf["file"]
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lower = text.lower()
        title_l = leaf["title"].lower()
        score = 0
        if q in title_l:
            score += 10
        if q in lower:
            score += 1
        if score == 0:
            continue
        snippet = _snippet_around(text, q)
        results.append(
            {
                "id": leaf["id"],
                "title": leaf["title"],
                "breadcrumb": leaf["breadcrumb"],
                "score": score,
                "snippet": snippet,
            }
        )
    results.sort(key=lambda r: (-r["score"], r["title"]))
    return {"ok": True, "query": query, "results": results[:limit]}


def _snippet_around(text: str, q: str, radius: int = 90) -> str:
    idx = text.lower().find(q.lower())
    if idx < 0:
        return (text[:180] + "…") if len(text) > 180 else text
    start = max(0, idx - radius)
    end = min(len(text), idx + len(q) + radius)
    chunk = text[start:end].replace("\n", " ")
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return prefix + chunk.strip() + suffix


def help_index_for_search() -> list[dict]:
    """All leaves for client-side search bootstrap."""
    return _flatten_tree(HELP_TREE)

#!/usr/bin/env python3
"""LEco update-catalog watcher — Docker-only background service.

Periodically checks:
  - Docker Hub (or local image) versions for ecosystem stack services
  - Ollama library releases (ollama.com/api/tags)
  - HuggingFace trending instruct models (for AirLLM catalog)

Writes JSON under ecosystem-stack/config/generated/ and regenerates Help manual
markdown tables under docs/help/generated/.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

LOG = logging.getLogger("leco-update-catalog")

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "ecosystem-stack" / "config"
GENERATED_DIR = CONFIG_DIR / "generated"
HELP_GENERATED = PROJECT_ROOT / "docs" / "help" / "generated"

SERVICES_CFG = CONFIG_DIR / "update-watcher-services.json"
SCHEDULE_JSON = CONFIG_DIR / "update-catalog-schedule.json"
OLLAMA_SEED = CONFIG_DIR / "llm-catalog-ollama-seed.json"
AIRLLM_SEED = CONFIG_DIR / "llm-catalog-airllm-seed.json"

OUT_UPDATES = GENERATED_DIR / "ecosystem-updates.json"
OUT_OLLAMA = GENERATED_DIR / "llm-catalog-ollama.json"
OUT_AIRLLM = GENERATED_DIR / "llm-catalog-airllm.json"
OUT_META = GENERATED_DIR / "catalog-meta.json"

INTERVAL_HOURS = float(os.getenv("UPDATE_CATALOG_INTERVAL_HOURS", "6"))
RUN_ONCE = os.getenv("UPDATE_CATALOG_RUN_ONCE", "").strip() in ("1", "true", "yes")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "LEco-DevOps-UpdateCatalog/1.0"})


def iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def human_bytes(n: int | float | None) -> str:
    if not n:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"~{n:.1f} {unit}" if unit != "B" else f"~{int(n)} B"
        n /= 1024
    return f"~{n:.1f} PB"


def docker_container_image(container: str) -> str | None:
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f", "{{.Config.Image}}", container],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            return None
        return (out.stdout or "").strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def docker_hub_latest_tag(repo: str, tag_filter: str | None = None) -> dict | None:
    """Return {tag, updated, digest} for newest sensible tag."""
    # ghcr.io/owner/img -> use GitHub API fallback later; try hub for docker.io style
    if repo.startswith("ghcr.io/"):
        return _github_latest_release(repo.replace("ghcr.io/", ""))
    parts = repo.split("/", 1)
    if len(parts) == 1:
        namespace, name = "library", parts[0]
    else:
        namespace, name = parts[0], parts[1]
    url = f"https://hub.docker.com/v2/repositories/{namespace}/{name}/tags"
    params = {"page_size": 25, "ordering": "-last_updated"}
    try:
        r = SESSION.get(url, params=params, timeout=30)
        r.raise_for_status()
        results = r.json().get("results") or []
    except requests.RequestException as exc:
        LOG.warning("Docker Hub %s: %s", repo, exc)
        return None
    for item in results:
        tag = str(item.get("name") or "")
        if not tag or tag == "latest":
            continue
        if tag_filter and tag_filter not in tag:
            continue
        if re.search(r"(rc|beta|alpha|nightly)", tag, re.I):
            continue
        updated = item.get("last_updated") or item.get("tag_last_pushed")
        return {"tag": tag, "updated": updated, "full": f"{repo}:{tag}"}
    for item in results:
        tag = str(item.get("name") or "")
        if tag:
            return {
                "tag": tag,
                "updated": item.get("last_updated"),
                "full": f"{repo}:{tag}",
            }
    return None


def _github_latest_release(repo_path: str) -> dict | None:
    url = f"https://api.github.com/repos/{repo_path}/releases/latest"
    try:
        r = SESSION.get(url, timeout=25)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        data = r.json()
        tag = str(data.get("tag_name") or "").lstrip("v")
        return {
            "tag": tag or "latest",
            "updated": data.get("published_at"),
            "full": tag or "latest",
            "html_url": data.get("html_url"),
        }
    except requests.RequestException as exc:
        LOG.warning("GitHub releases %s: %s", repo_path, exc)
        return None


def _read_local_version(rel_path: str) -> str | None:
    path = PROJECT_ROOT / rel_path
    try:
        line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return line.lstrip("v") if line else None
    except (OSError, IndexError):
        return None


def check_github_repos(cfg: dict) -> list[dict]:
    """Check public GitHub repos for releases newer than local VERSION file."""
    rows: list[dict] = []
    for item in cfg.get("github_repos") or []:
        if not isinstance(item, dict):
            continue
        repo = str(item.get("repo") or "").strip()
        if not repo or "/" not in repo:
            continue
        label = str(item.get("label") or repo)
        local_ver = _read_local_version(str(item.get("version_file") or "VERSION"))
        row: dict[str, Any] = {
            "id": str(item.get("id") or repo.replace("/", "-")),
            "label": label,
            "container": "git",
            "source": "github",
            "repo": repo,
            "running_image": f"local:{local_ver}" if local_ver else "local:unknown",
            "upgrade_help": str(item.get("upgrade_help") or "ecosystem-updates"),
            "upgrade_steps": list(item.get("upgrade_steps") or []),
            "status": "unknown",
        }
        api_repo = f"https://api.github.com/repos/{repo}"
        try:
            r = SESSION.get(api_repo, timeout=20)
            if r.status_code == 404:
                row["status"] = "skipped"
                row["note"] = "Repository not found or not accessible (private)."
                rows.append(row)
                continue
            if r.status_code == 403:
                row["status"] = "skipped"
                row["note"] = "GitHub API rate limit or forbidden; skipped."
                rows.append(row)
                continue
            r.raise_for_status()
            repo_meta = r.json()
            if repo_meta.get("private") and item.get("skip_if_private", True):
                row["status"] = "skipped"
                row["note"] = "Private repository; skipped."
                rows.append(row)
                continue
        except requests.RequestException as exc:
            row["status"] = "check_failed"
            row["note"] = str(exc)[:120]
            rows.append(row)
            continue

        latest = _github_latest_release(repo)
        row["latest"] = latest
        if not latest:
            row["status"] = "no_releases"
            row["note"] = "No published GitHub release; compare with default branch manually."
        elif not local_ver:
            row["status"] = "update_available"
            row["note"] = f"Latest release: {latest.get('tag')}"
        else:
            remote_tag = str(latest.get("tag") or "").lstrip("v")
            if remote_tag and remote_tag != local_ver:
                row["status"] = "update_available"
                row["note"] = f"Release {remote_tag} vs local {local_ver}"
            else:
                row["status"] = "up_to_date"
        rows.append(row)
    return rows


def check_stack_services(cfg: dict) -> list[dict]:
    rows: list[dict] = []
    for svc in cfg.get("services") or []:
        if not isinstance(svc, dict):
            continue
        sid = str(svc.get("id") or "")
        label = str(svc.get("label") or sid)
        container = str(svc.get("container") or sid)
        running = docker_container_image(container)
        local_image = svc.get("local_image")
        row: dict[str, Any] = {
            "id": sid,
            "label": label,
            "container": container,
            "running_image": running,
            "status": "unknown",
            "upgrade_help": str(svc.get("upgrade_help") or "deploy-rebuild"),
            "upgrade_steps": list(svc.get("upgrade_steps") or []),
        }
        if local_image:
            row["source"] = "local_image"
            row["latest"] = {"full": str(local_image)}
            if running and local_image.split(":")[0] in (running or ""):
                row["status"] = "running"
                row["note"] = "Rebuild image after Dockerfile or dependency changes."
            elif running:
                row["status"] = "image_mismatch"
                row["note"] = f"Running {running}; expected {local_image}. Rebuild."
            else:
                row["status"] = "not_running"
            rows.append(row)
            continue
        repo = str(svc.get("image_repo") or "")
        if not repo:
            rows.append(row)
            continue
        latest = docker_hub_latest_tag(repo, svc.get("tag_filter"))
        row["source"] = "docker_hub"
        row["latest"] = latest
        if not running:
            row["status"] = "not_running"
        elif not latest:
            row["status"] = "check_failed"
        else:
            latest_ref = latest.get("full") or ""
            running_base = (running or "").split("@")[0]
            if latest_ref and (latest_ref in running_base or latest.get("tag", "") in running_base):
                row["status"] = "up_to_date"
            else:
                row["status"] = "update_available"
                row["note"] = f"Hub latest: {latest_ref}"
        rows.append(row)
    return rows


def fetch_ollama_online() -> list[dict]:
    try:
        r = SESSION.get("https://ollama.com/api/tags", timeout=45)
        r.raise_for_status()
        models = r.json().get("models") or []
    except requests.RequestException as exc:
        LOG.warning("Ollama API: %s", exc)
        return []
    out = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or m.get("model") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "size_bytes": m.get("size"),
                "modified_at": m.get("modified_at"),
                "online_source": "ollama.com/api/tags",
            }
        )
    return out


def fetch_hf_instruct(limit: int = 40) -> list[dict]:
    params = {
        "filter": "text-generation",
        "sort": "downloads",
        "direction": -1,
        "limit": limit,
    }
    try:
        r = SESSION.get("https://huggingface.co/api/models", params=params, timeout=45)
        r.raise_for_status()
        items = r.json()
    except requests.RequestException as exc:
        LOG.warning("HuggingFace API: %s", exc)
        return []
    out = []
    if not isinstance(items, list):
        return out
    for m in items:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid or "instruct" not in mid.lower() and "chat" not in mid.lower():
            # still allow top downloads without instruct in name
            tags = m.get("tags") or []
            if "conversational" not in tags and "text-generation" not in tags:
                continue
        lic = ""
        for t in m.get("tags") or []:
            if str(t).startswith("license:"):
                lic = str(t).replace("license:", "")
        out.append(
            {
                "name": mid,
                "downloads": m.get("downloads"),
                "likes": m.get("likes"),
                "license": lic,
                "pipeline_tag": m.get("pipeline_tag"),
                "online_source": "huggingface.co/api/models",
            }
        )
    return out


def _infer_publisher(name: str) -> str:
    base = name.split(":")[0].split("/")[-1]
    fam = base.split("-")[0].split(".")[0]
    mapping = {
        "llama": "Meta",
        "qwen": "Alibaba (Qwen)",
        "deepseek": "DeepSeek",
        "mistral": "Mistral AI",
        "gemma": "Google",
        "phi": "Microsoft",
        "codellama": "Meta",
        "nomic": "Nomic AI",
    }
    return mapping.get(fam.lower(), fam.title())


def merge_ollama_catalog(seed_models: list[dict], online: list[dict]) -> dict:
    by_name: dict[str, dict] = {}
    for m in seed_models:
        if isinstance(m, dict) and m.get("name"):
            by_name[str(m["name"])] = dict(m)
    online_names = {o["name"] for o in online}
    for o in online:
        name = o["name"]
        if name in by_name:
            ent = by_name[name]
            ent["online_seen"] = True
            ent["modified_at"] = o.get("modified_at") or ent.get("modified_at")
            if o.get("size_bytes"):
                ent["size_bytes"] = o["size_bytes"]
                if not ent.get("size_disk"):
                    ent["size_disk"] = human_bytes(o["size_bytes"])
        else:
            base = name.split(":")[0]
            by_name[name] = {
                "name": name,
                "label": name,
                "publisher": _infer_publisher(name),
                "license": "See ollama.com/library",
                "parameters": name.split(":")[-1] if ":" in name else "",
                "size_disk": human_bytes(o.get("size_bytes")),
                "niche": ["discovered"],
                "specialty": f"Listed on Ollama library (discovered {iso_now()[:10]}).",
                "use_where": ["https://ollama.lh", "https://ai.lh"],
                "use_how": f"./leco-cli.sh ollama install {name}",
                "install_cli": f"./leco-cli.sh ollama install {name}",
                "source_url": f"https://ollama.com/library/{base}",
                "tags": ["discovered"],
                "discovered_online": True,
                "modified_at": o.get("modified_at"),
            }
    models = sorted(by_name.values(), key=lambda x: (0 if x.get("discovered_online") else -1, str(x.get("name"))))
    new_online = [n for n in online_names if n not in {str(m.get("name")) for m in seed_models}]
    return {
        "ok": True,
        "backend": "ollama",
        "generated_at": iso_now(),
        "model_count": len(models),
        "new_online_count": len(new_online),
        "new_online": sorted(new_online)[:30],
        "models": models,
    }


def merge_airllm_catalog(seed_models: list[dict], online: list[dict]) -> dict:
    by_name: dict[str, dict] = {}
    for m in seed_models:
        if isinstance(m, dict) and m.get("name"):
            by_name[str(m["name"])] = dict(m)
    seed_names = set(by_name)
    for o in online:
        name = o["name"]
        if name in by_name:
            ent = by_name[name]
            ent["online_seen"] = True
            ent["hf_downloads"] = o.get("downloads")
            if o.get("license") and not ent.get("license"):
                ent["license"] = o["license"]
        elif "/" in name and any(k in name.lower() for k in ("instruct", "chat", "coder")):
            by_name[name] = {
                "name": name,
                "label": name.split("/")[-1],
                "publisher": name.split("/")[0],
                "license": o.get("license") or "See HuggingFace",
                "parameters": "",
                "size_disk": "",
                "niche": ["discovered"],
                "specialty": "Trending on HuggingFace; verify AirLLM compatibility before production.",
                "use_where": ["https://airllm.lh"],
                "use_how": f"./leco-cli.sh airllm install {name}",
                "install_cli": f"./leco-cli.sh airllm install {name}",
                "source_url": f"https://huggingface.co/{name}",
                "tags": ["discovered"],
                "discovered_online": True,
                "hf_downloads": o.get("downloads"),
            }
    models = sorted(by_name.values(), key=lambda x: str(x.get("name")))
    discovered = [m["name"] for m in models if m.get("discovered_online")]
    return {
        "ok": True,
        "backend": "airllm",
        "generated_at": iso_now(),
        "model_count": len(models),
        "new_online_count": len(discovered),
        "new_online": sorted(discovered)[:30],
        "models": models,
    }


def build_updates_payload(
    services: list[dict], ollama: dict, airllm: dict, github_repos: list[dict] | None = None
) -> dict:
    model_alerts = []
    for name in ollama.get("new_online") or []:
        model_alerts.append(
            {
                "type": "ollama_model",
                "name": name,
                "message": f"New or trending on Ollama library: {name}",
                "install": f"./leco-cli.sh ollama install {name}",
                "help": "ollama",
            }
        )
    github_repos = github_repos or []
    all_services = list(services) + list(github_repos)
    svc_updates = [s for s in all_services if s.get("status") == "update_available"]
    return {
        "ok": True,
        "generated_at": iso_now(),
        "services": all_services,
        "docker_services": services,
        "github_repos": github_repos,
        "service_updates_available": len(svc_updates),
        "model_alerts": model_alerts,
        "ollama_new_count": ollama.get("new_online_count", 0),
        "airllm_catalog_count": airllm.get("model_count", 0),
    }


def _md_escape(s: str) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def _models_table_md(models: list[dict], backend: str) -> str:
    if not models:
        return "_No models in catalog._\n"
    lines = [
        "| Model | Publisher | Niche | Size | Use where | Install |",
        "|-------|-----------|-------|------|-----------|---------|",
    ]
    for m in models[:80]:
        name = _md_escape(m.get("name"))
        pub = _md_escape(m.get("publisher"))
        niche = _md_escape(", ".join(m.get("niche") or [])[:60])
        size = _md_escape(m.get("size_disk"))
        where = _md_escape("; ".join(m.get("use_where") or [])[:80])
        inst = _md_escape(m.get("install_cli") or m.get("use_how", "")[:60])
        flag = " 🆕" if m.get("discovered_online") else ""
        lines.append(f"| `{name}`{flag} | {pub} | {niche} | {size} | {where} | `{inst}` |")
    if len(models) > 80:
        lines.append(f"\n_…and {len(models) - 80} more in `/api/llm-catalog/{backend}`._\n")
    return "\n".join(lines) + "\n"


def write_help_markdown(updates: dict, ollama: dict, airllm: dict) -> None:
    HELP_GENERATED.mkdir(parents=True, exist_ok=True)
    ts = updates.get("generated_at", iso_now())

    svc_lines = [
        "# Ecosystem updates (auto-generated)\n",
        f"_Generated at **{ts}** by `leco-update-catalog`. [Refresh service](/help?topic=ecosystem-updates)_\n",
        "## Stack service versions\n",
        "| Service | Status | Running | Latest | Upgrade |",
        "|---------|--------|---------|--------|---------|",
    ]
    for s in updates.get("docker_services") or updates.get("services") or []:
        if s.get("source") == "github":
            continue
        st = s.get("status", "")
        run = _md_escape(s.get("running_image") or "—")
        lat = _md_escape((s.get("latest") or {}).get("full") or "—")
        steps = s.get("upgrade_steps") or []
        up = "<br>".join(_md_escape(x) for x in steps[:3]) if steps else "—"
        svc_lines.append(
            f"| {s.get('label')} | **{st}** | `{run}` | `{lat}` | {up} |"
        )
    gh = updates.get("github_repos") or []
    if gh:
        svc_lines.append("\n## GitHub repository releases\n")
        svc_lines.append("| Project | Status | Local | Latest release | Notes |")
        svc_lines.append("|---------|--------|-------|----------------|-------|")
        for s in gh:
            st = s.get("status", "")
            run = _md_escape(s.get("running_image") or "—")
            lat = _md_escape((s.get("latest") or {}).get("tag") or "—")
            note = _md_escape(s.get("note") or "—")
            svc_lines.append(f"| {s.get('label')} | **{st}** | `{run}` | `{lat}` | {note} |")
    svc_lines.append("\n## New Ollama library entries\n")
    new_o = ollama.get("new_online") or []
    if new_o:
        for n in new_o[:20]:
            svc_lines.append(f"- `{n}` — `./leco-cli.sh ollama install {n}`")
    else:
        svc_lines.append("_No new Ollama names since last seed merge._\n")
    svc_lines.append("\nSee [How to upgrade](help:deploy-rebuild).\n")
    (HELP_GENERATED / "14-ecosystem-updates.md").write_text("\n".join(svc_lines), encoding="utf-8")

    ollama_md = [
        "# Ollama open-source LLM catalog (auto-generated)\n",
        f"_Generated **{ts}**. Full live table: API `/api/llm-catalog/ollama` or Help topic below._\n",
        "## Curated + discovered models\n",
        _models_table_md(ollama.get("models") or [], "ollama"),
        "\n**Legend:** 🆕 = discovered from ollama.com since last seed edit.\n",
        "\n[Model manager](https://localhost.lh/?tab=infrastructureTab#infra-ollama) · [Ollama help](help:ollama)\n",
    ]
    (HELP_GENERATED / "15-llm-catalog-ollama.md").write_text("\n".join(ollama_md), encoding="utf-8")

    airllm_md = [
        "# AirLLM / HuggingFace open-source LLM catalog (auto-generated)\n",
        f"_Generated **{ts}**. API `/api/llm-catalog/airllm`._\n",
        "## Curated + trending HF instruct models\n",
        _models_table_md(airllm.get("models") or [], "airllm"),
        "\n**Gated models** need `HF_TOKEN` with access. See [AirLLM help](help:airllm).\n",
        "\n[Model manager](https://localhost.lh/?tab=infrastructureTab#infra-airllm)\n",
    ]
    (HELP_GENERATED / "16-llm-catalog-airllm.md").write_text("\n".join(airllm_md), encoding="utf-8")


def run_once() -> None:
    LOG.info("update-catalog run started")
    cfg = read_json(SERVICES_CFG, {"services": [], "check_interval_hours": 6})
    ollama_seed = read_json(OLLAMA_SEED, {}).get("models") or []
    airllm_seed = read_json(AIRLLM_SEED, {}).get("models") or []

    services = check_stack_services(cfg)
    github_rows = check_github_repos(cfg)
    ollama_online = fetch_ollama_online()
    hf_online = fetch_hf_instruct()
    ollama_cat = merge_ollama_catalog(ollama_seed, ollama_online)
    airllm_cat = merge_airllm_catalog(airllm_seed, hf_online)
    updates = build_updates_payload(services, ollama_cat, airllm_cat, github_rows)

    sched = read_json(SCHEDULE_JSON, {"mode": "interval", "interval_hours": cfg.get("check_interval_hours", INTERVAL_HOURS)})
    meta = {
        "generated_at": iso_now(),
        "interval_hours": sched.get("interval_hours", cfg.get("check_interval_hours", INTERVAL_HOURS)),
        "schedule_mode": sched.get("mode", "interval"),
        "ollama_online_count": len(ollama_online),
        "hf_fetched": len(hf_online),
    }

    write_json(OUT_UPDATES, updates)
    write_json(OUT_OLLAMA, ollama_cat)
    write_json(OUT_AIRLLM, airllm_cat)
    write_json(OUT_META, meta)
    write_help_markdown(updates, ollama_cat, airllm_cat)
    LOG.info(
        "wrote catalogs: services=%s ollama=%s airllm=%s",
        len(services),
        ollama_cat.get("model_count"),
        airllm_cat.get("model_count"),
    )


def _sleep_seconds_from_schedule() -> float:
    """Align with dashboard/ecosystem_updates.sleep_seconds_for_schedule (duplicate light logic)."""
    sched = read_json(SCHEDULE_JSON, {"mode": "interval", "interval_hours": INTERVAL_HOURS})
    mode = str(sched.get("mode") or "interval")
    now = datetime.now(timezone.utc)
    meta = read_json(OUT_META, {})
    last_s = meta.get("generated_at")
    last_dt = None
    if last_s:
        try:
            last_dt = datetime.fromisoformat(str(last_s).replace("Z", "+00:00"))
        except ValueError:
            pass
    if mode == "fixed":
        times = sched.get("fixed_times_utc") or ["06:00", "18:00"]
        candidates = []
        for t in times:
            try:
                h, m = [int(x) for x in str(t).split(":")[:2]]
                for off in (0, 1):
                    cand = (now + timedelta(days=off)).replace(
                        hour=h, minute=m, second=0, microsecond=0, tzinfo=timezone.utc
                    )
                    if cand > now:
                        candidates.append(cand)
            except (ValueError, TypeError):
                continue
        if candidates:
            return max(300.0, (min(candidates) - now).total_seconds())
    interval = float(sched.get("interval_hours") or INTERVAL_HOURS)
    if last_dt:
        next_dt = last_dt + timedelta(hours=interval)
        if next_dt > now:
            return max(300.0, (next_dt - now).total_seconds())
    return max(3600.0, interval * 3600)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    if RUN_ONCE:
        run_once()
        return
    sched = read_json(SCHEDULE_JSON, {"mode": "interval", "interval_hours": INTERVAL_HOURS})
    LOG.info("leco-update-catalog schedule mode=%s", sched.get("mode", "interval"))
    while True:
        try:
            run_once()
        except Exception:
            LOG.exception("update-catalog run failed")
        secs = _sleep_seconds_from_schedule()
        LOG.info("sleeping %.0f s until next catalog check", secs)
        time.sleep(secs)


if __name__ == "__main__":
    main()

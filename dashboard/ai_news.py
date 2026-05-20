"""Live consolidated AI news from configured RSS/Atom feeds + optional local LLM query refinement."""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(
    os.getenv("DASHBOARD_PROJECT_ROOT") or str(Path(__file__).resolve().parent.parent)
)
SOURCES_JSON = PROJECT_ROOT / "ecosystem-stack" / "config" / "ai-news-sources.json"
GENERATED_CACHE = PROJECT_ROOT / "ecosystem-stack" / "config" / "generated" / "ai-news-cache.json"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "LEco-DevOps-AINews/1.0"})
_CACHE: dict[str, Any] = {"fetched_at": 0.0, "payload": None}


def _read_json(path: Path, default: Any) -> Any:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else default
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _parse_rss_or_atom(xml_text: str, feed_meta: dict) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = _strip_html(item.findtext("description") or item.findtext("summary") or "")
        pub = item.findtext("pubDate") or item.findtext("published") or ""
        items.append(_normalize_item(title, link, desc, pub, feed_meta))

    if items:
        return items

    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = entry.find("a:link", ns)
        link = (link_el.get("href") if link_el is not None else "") or ""
        desc = _strip_html(
            entry.findtext("a:summary", default="", namespaces=ns)
            or entry.findtext("a:content", default="", namespaces=ns)
            or ""
        )
        pub = entry.findtext("a:updated", default="", namespaces=ns) or entry.findtext(
            "a:published", default="", namespaces=ns
        )
        items.append(_normalize_item(title, link, desc, pub, feed_meta))
    return items


def _normalize_item(title: str, link: str, summary: str, pub: str, feed_meta: dict) -> dict:
    published_at = None
    if pub:
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            published_at = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError, OverflowError):
            try:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                published_at = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                published_at = None
    tags = list(feed_meta.get("tags") or [])
    hay = f"{title} {summary}".lower()
    for cat, hints in (feed_meta.get("keyword_hints") or {}).items():
        if any(h.lower() in hay for h in hints):
            if cat not in tags:
                tags.append(cat)
    return {
        "id": re.sub(r"[^a-zA-Z0-9]+", "-", (link or title or "item"))[:120],
        "title": title or "(untitled)",
        "url": link,
        "summary": summary[:500] if summary else "",
        "published_at": published_at,
        "source_id": feed_meta.get("id"),
        "source_title": feed_meta.get("title"),
        "category": feed_meta.get("category") or "tools",
        "tags": tags,
    }


def _fetch_feed(feed: dict, cfg: dict) -> list[dict]:
    url = str(feed.get("url") or "").strip()
    if not url:
        return []
    meta = {
        **feed,
        "keyword_hints": cfg.get("keyword_hints") or {},
    }
    try:
        r = _SESSION.get(url, timeout=25)
        r.raise_for_status()
        return _parse_rss_or_atom(r.text, meta)[: int(cfg.get("max_items_per_feed") or 15)]
    except requests.RequestException:
        return []


def fetch_all_news(force: bool = False) -> dict:
    """Aggregate feeds; disk + in-memory cache."""
    cfg = _read_json(SOURCES_JSON, {"feeds": [], "cache_ttl_seconds": 900})
    ttl = float(cfg.get("cache_ttl_seconds") or 900)
    now = time.time()
    if not force and _CACHE.get("payload") and now - float(_CACHE.get("fetched_at") or 0) < ttl:
        return _CACHE["payload"]

    if not force:
        disk = _read_json(GENERATED_CACHE, {})
        if disk.get("generated_at") and disk.get("items"):
            try:
                gen = datetime.fromisoformat(str(disk["generated_at"]).replace("Z", "+00:00"))
                age = now - gen.timestamp()
                if age < ttl:
                    _CACHE["fetched_at"] = now
                    _CACHE["payload"] = disk
                    return disk
            except (TypeError, ValueError):
                pass

    all_items: list[dict] = []
    errors: list[dict] = []
    for feed in cfg.get("feeds") or []:
        if not isinstance(feed, dict):
            continue
        got = _fetch_feed(feed, cfg)
        if got:
            all_items.extend(got)
        else:
            errors.append({"feed": feed.get("id"), "title": feed.get("title")})

    all_items.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    max_total = int(cfg.get("max_items_total") or 80)
    payload = {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "categories": cfg.get("default_categories") or [],
        "feeds_configured": len(cfg.get("feeds") or []),
        "item_count": min(len(all_items), max_total),
        "items": all_items[:max_total],
        "errors": errors,
    }
    _write_json(GENERATED_CACHE, payload)
    _CACHE["fetched_at"] = now
    _CACHE["payload"] = payload
    return payload


def filter_news(
    payload: dict,
    *,
    category: str | None = None,
    tags: list[str] | None = None,
    q: str | None = None,
) -> list[dict]:
    items = list(payload.get("items") or [])
    if category:
        cat_l = category.strip().lower()
        items = [i for i in items if (i.get("category") or "").lower() == cat_l]
    if tags:
        want = {t.strip().lower() for t in tags if t.strip()}
        if want:
            items = [
                i
                for i in items
                if want.intersection({str(t).lower() for t in (i.get("tags") or [])})
            ]
    if q:
        ql = q.strip().lower()
        if ql:
            items = [
                i
                for i in items
                if ql in (i.get("title") or "").lower()
                or ql in (i.get("summary") or "").lower()
                or ql in " ".join(i.get("tags") or []).lower()
            ]
    return items


def refine_query_with_llm(user_query: str) -> dict:
    """Use local Ollama to suggest categories, tags, and search keywords."""
    from ai_config import load_config

    cfg = load_config()
    prov = (cfg.get("providers") or {}).get("ollama") or {}
    base = str(prov.get("base_url") or "http://ollama:11434").rstrip("/")
    model = str(cfg.get("default_model") or prov.get("default_model") or "qwen2.5-coder")
    sources = _read_json(SOURCES_JSON, {})
    cats = sources.get("default_categories") or []

    prompt = (
        "You help filter AI industry news. Given the user interest, respond with JSON only:\n"
        '{"categories":[],"tags":[],"keywords":[]}\n'
        f"Available categories: {', '.join(cats)}\n"
        f"User interest: {user_query.strip()[:500]}\n"
    )
    try:
        r = requests.post(
            f"{base}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=int(prov.get("timeout") or 120),
        )
        r.raise_for_status()
        raw = r.json().get("response") or ""
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        return {
            "ok": True,
            "categories": data.get("categories") or [],
            "tags": data.get("tags") or [],
            "keywords": data.get("keywords") or [],
            "model": model,
        }
    except (requests.RequestException, json.JSONDecodeError, TypeError, ValueError) as exc:
        # Heuristic fallback
        ql = user_query.lower()
        guess_tags = []
        guess_cats = []
        for cat, hints in (sources.get("keyword_hints") or {}).items():
            if any(h in ql for h in hints):
                guess_cats.append(cat)
        for word in re.findall(r"[a-z]{4,}", ql):
            guess_tags.append(word)
        return {
            "ok": False,
            "error": str(exc)[:200],
            "categories": guess_cats[:3],
            "tags": guess_tags[:8],
            "keywords": re.findall(r"[a-zA-Z0-9][a-zA-Z0-9._-]{2,}", user_query)[:10],
            "model": model,
            "fallback": True,
        }

"""Discover and parse wrangler configs (TOML, JSON and JSONC) for hosted Cloudflare apps.

Cloudflare's current default config file is ``wrangler.jsonc``; ``wrangler.toml`` is the legacy
format that older LEco-hosted apps were registered against. Discovery therefore has to cover all
three formats, and it has to do so *additively*: an app already registered with a
``configRefs``/``runtimes[].config`` pointing at a TOML path must keep resolving to exactly the
same path, in exactly the same order, after this module learned about JSON.

Two shapes of repository matter here:

* single-worker repos (one ``wrangler.toml`` / ``wrangler.jsonc`` at the root), and
* pnpm/turbo monorepos where every worker owns a ``workers/<id>/wrangler.jsonc``. Those need one
  runtime entry per worker, so :func:`enumerate_wrangler_workers` hands the caller the worker name
  and directory alongside the config path.

Parsing is deliberately forgiving. A monorepo with one malformed config still has to onboard the
other workers, so :func:`read_wrangler_config` never raises: it reports what it could read and puts
the failure in the ``error`` key for the caller to surface or ignore.

JSONC is not JSON. It allows ``//`` and ``/* */`` comments and trailing commas, and real wrangler
files are heavily commented. The stripper below is a character scanner rather than a regex because
``"https://example.com"`` and ``"a,]"`` are legal JSON strings that a regex would corrupt.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

try:  # Python 3.11+; the dashboard targets 3.11+, the fallback keeps 3.10 importable.
    import tomllib
except ImportError:  # pragma: no cover - exercised only on Python < 3.11
    tomllib = None  # type: ignore[assignment]

# Conventional locations probed before the directory walk. TOML entries stay first so a repo that
# ships both formats keeps resolving to the TOML file it was registered with.
WRANGLER_PATHS = (
    Path("wrangler.toml"),
    Path("cloudflare") / "wrangler.toml",
    Path("wrangler.jsonc"),
    Path("wrangler.json"),
    Path("cloudflare") / "wrangler.jsonc",
    Path("cloudflare") / "wrangler.json",
)

WRANGLER_CONFIG_EXTENSIONS = ("toml", "jsonc", "json")

# ``wrangler.toml``, ``wrangler.api.jsonc``, ``wrangler.pages.json`` ...
_WRANGLER_CONFIG_NAME_RE = re.compile(
    r"^wrangler(\.[a-zA-Z0-9_.-]+)?\.(toml|jsonc|json)$",
    re.IGNORECASE,
)

# Preference among otherwise-equal candidates, so a repo carrying both formats never flips.
_EXTENSION_RANK = {"toml": 0, "jsonc": 1, "json": 2}

_SCAN_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".next",
        ".nuxt",
        "dist",
        "build",
        "coverage",
        ".turbo",
        ".wrangler",
        "vendor",
    }
)

_BINDING_LIST_KEYS = (
    "kv_namespaces",
    "r2_buckets",
    "d1_databases",
    "services",
    "queues",
    "durable_objects",
)


def is_wrangler_config_name(name: str) -> bool:
    """True for any wrangler config filename in a supported format."""
    return bool(_WRANGLER_CONFIG_NAME_RE.match(name or ""))


def wrangler_config_format(name: str) -> str:
    """``wrangler.api.jsonc`` -> ``jsonc``; unknown filenames report ``unknown``."""
    n = (name or "").lower()
    for ext in WRANGLER_CONFIG_EXTENSIONS:
        if n.endswith("." + ext):
            return ext
    return "unknown"


def is_wrangler_pages_config(name: str) -> bool:
    """Pages projects use ``wrangler pages dev``, not the Workers runtime adapter."""
    n = (name or "").lower()
    if n in ("wrangler.pages.toml", "wrangler.pages.jsonc", "wrangler.pages.json"):
        return True
    return ".pages." in n


def _wrangler_stem(name: str) -> str:
    """``wrangler.api.jsonc`` -> ``wrangler.api``; leaves unrecognised names untouched."""
    n = (name or "").lower()
    ext = wrangler_config_format(n)
    if ext == "unknown":
        return n
    return n[: -(len(ext) + 1)]


def runtime_id_from_wrangler_relpath(rel: Path) -> str:
    """``infra/wrangler.api.toml`` -> ``api``; ``wrangler.jsonc`` -> ``worker``."""
    name = rel.name.lower()
    if not is_wrangler_config_name(name):
        return "worker"
    stem = _wrangler_stem(name)
    if stem == "wrangler":
        return "worker"
    if stem.startswith("wrangler."):
        return stem[len("wrangler.") :].replace("_", "-") or "worker"
    return "worker"


def _wrangler_config_sort_key(rel: Path) -> tuple:
    """Best-first ordering: ``wrangler.api.*``, then root ``wrangler.*``, then ``infra/``, then rest.

    The depth slot only varies for bare ``wrangler.<ext>`` files, where a root config outranks a
    nested one; every other bucket keeps the plain path ordering the TOML-only version used.
    """
    name = rel.name.lower()
    stem = _wrangler_stem(name)
    ext_rank = _EXTENSION_RANK.get(wrangler_config_format(name), 9)
    if stem == "wrangler.api":
        return (0, 0, ext_rank, str(rel))
    if stem == "wrangler":
        return (1, len(rel.parts) - 1, ext_rank, str(rel))
    if rel.parts and rel.parts[0].lower() == "infra":
        return (2, 0, ext_rank, str(rel))
    if is_wrangler_pages_config(name):
        return (9, 0, ext_rank, str(rel))
    return (3, 0, ext_rank, str(rel))


def _walk_wrangler_configs(root: Path, *, max_depth: int, pages: bool) -> list[Path]:
    r = root.resolve()
    seen: set[str] = set()
    found: list[Path] = []

    if not pages:
        for rel in WRANGLER_PATHS:
            if (r / rel).is_file():
                p = rel.as_posix()
                if p not in seen:
                    seen.add(p)
                    found.append(rel)

    for dirpath, dirs, files in os.walk(r):
        rp = Path(dirpath)
        try:
            depth = len(rp.relative_to(r).parts)
        except ValueError:
            continue
        dirs[:] = [d for d in dirs if d not in _SCAN_PRUNE_DIRS]
        if depth > max_depth:
            dirs[:] = []
            continue
        for fn in files:
            if not _WRANGLER_CONFIG_NAME_RE.match(fn):
                continue
            if is_wrangler_pages_config(fn) is not pages:
                continue
            rel = (rp / fn).relative_to(r)
            p = rel.as_posix()
            if p not in seen:
                seen.add(p)
                found.append(rel)
    return found


def list_wrangler_config_files(root: Path, *, max_depth: int = 6) -> list[Path]:
    """All Worker-oriented wrangler config paths under ``root`` (relative), best-first."""
    found = _walk_wrangler_configs(root, max_depth=max_depth, pages=False)
    found.sort(key=_wrangler_config_sort_key)
    return found


def pick_primary_wrangler_config(root: Path) -> Path | None:
    configs = list_wrangler_config_files(root)
    return configs[0] if configs else None


# ---------------------------------------------------------------------------
# JSONC
# ---------------------------------------------------------------------------


def strip_jsonc_comments(text: str) -> str:
    """Remove ``//`` and ``/* */`` comments while leaving string literals byte-for-byte intact.

    Newlines inside block comments are kept so json error line numbers still point at the source.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            out.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                    if text[i] == "\n":
                        out.append("\n")
                    i += 1
                i += 2  # skips the closing "*/"; overshoots harmlessly if unterminated
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_json_trailing_commas(text: str) -> str:
    """Drop commas that sit immediately before ``}`` or ``]`` (ignoring commas inside strings)."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            if ch == "\\" and i + 1 < n:
                out.append(ch)
                out.append(text[i + 1])
                i += 2
                continue
            out.append(ch)
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def loads_jsonc(text: str) -> Any:
    """Parse JSON with comments and trailing commas. Raises ``json.JSONDecodeError`` like ``json``."""
    return json.loads(strip_json_trailing_commas(strip_jsonc_comments(text)))


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

_TOML_SCALAR_RES = {
    "name": re.compile(r"^\s*name\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE),
    "main": re.compile(r"^\s*main\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE),
    "compatibility_date": re.compile(
        r"^\s*compatibility_date\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE
    ),
}

# The pre-tomllib expression, kept verbatim so a TOML file too broken for tomllib still yields the
# same Pages output dir it always did.
_PAGES_OUTPUT_DIR_RE = re.compile(
    r'^pages_build_output_dir\s*=\s*["\']?([^"\']+)["\']?\s*$',
    re.MULTILINE,
)


def _empty_config(path: Path, fmt: str, error: str | None) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "format": fmt,
        "name": None,
        "main": None,
        "compatibility_date": None,
        "compatibility_flags": [],
        "account_id": None,
        "kv_namespaces": [],
        "r2_buckets": [],
        "d1_databases": [],
        "services": [],
        "durable_objects": [],
        "queues": {"producers": [], "consumers": []},
        "vars": {},
        "pages_build_output_dir": None,
        "raw": {},
        "error": error,
    }


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_binding_list(value: Any) -> list[dict[str, Any]]:
    """Both formats spell binding arrays the same way; ``durable_objects`` wraps them in ``bindings``."""
    if isinstance(value, dict):
        value = value.get("bindings")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _as_queues(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict):
        return {"producers": [], "consumers": []}
    return {
        "producers": _as_binding_list(value.get("producers")),
        "consumers": _as_binding_list(value.get("consumers")),
    }


def _normalize_wrangler_data(path: Path, fmt: str, data: Any, error: str | None) -> dict[str, Any]:
    out = _empty_config(path, fmt, error)
    if not isinstance(data, dict):
        return out
    out["raw"] = data
    out["name"] = _as_str(data.get("name"))
    out["main"] = _as_str(data.get("main"))
    out["compatibility_date"] = _as_str(data.get("compatibility_date"))
    out["account_id"] = _as_str(data.get("account_id"))
    out["pages_build_output_dir"] = _as_str(data.get("pages_build_output_dir"))
    flags = data.get("compatibility_flags")
    if isinstance(flags, list):
        out["compatibility_flags"] = [f for f in flags if isinstance(f, str)]
    for key in _BINDING_LIST_KEYS:
        if key == "queues":
            continue
        out[key] = _as_binding_list(data.get(key))
    out["queues"] = _as_queues(data.get("queues"))
    variables = data.get("vars")
    out["vars"] = dict(variables) if isinstance(variables, dict) else {}
    return out


def _parse_toml_text(path: Path, text: str) -> dict[str, Any]:
    if tomllib is not None:
        try:
            return _normalize_wrangler_data(path, "toml", tomllib.loads(text), None)
        except (tomllib.TOMLDecodeError, ValueError) as exc:
            error = f"toml parse error: {exc}"
    else:  # pragma: no cover - Python < 3.11 only
        error = "tomllib unavailable; scalar-only regex fallback"
    # Fallback: recover the top-level scalars so a syntax error elsewhere in the file still leaves
    # the caller with a usable worker name.
    out = _empty_config(path, "toml", error)
    for key, pattern in _TOML_SCALAR_RES.items():
        m = pattern.search(text)
        if m:
            out[key] = m.group(1).strip() or None
    m = _PAGES_OUTPUT_DIR_RE.search(text)
    if m:
        out["pages_build_output_dir"] = m.group(1).strip().strip('"').strip("'") or None
    return out


def read_wrangler_config(config_path: Path) -> dict[str, Any]:
    """Read one wrangler config in any supported format into a normalized dict.

    Never raises. On failure the returned dict carries ``error`` and empty binding collections, so a
    single broken config in a monorepo cannot take the rest of discovery down with it.
    """
    path = Path(config_path)
    fmt = wrangler_config_format(path.name)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _empty_config(path, fmt, f"unreadable: {exc}")

    if fmt == "toml":
        return _parse_toml_text(path, text)
    if fmt in ("json", "jsonc"):
        try:
            return _normalize_wrangler_data(path, fmt, loads_jsonc(text), None)
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            return _empty_config(path, fmt, f"{fmt} parse error: {exc}")
    return _empty_config(path, fmt, f"unsupported wrangler config format: {path.name}")


# ---------------------------------------------------------------------------
# Monorepo enumeration
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str | None) -> str:
    return _SLUG_RE.sub("-", (value or "").lower()).strip("-")


def enumerate_wrangler_workers(root: Path, *, max_depth: int = 6) -> list[dict[str, Any]]:
    """One entry per discovered Worker config under ``root``, best-first and deterministic.

    Each entry has ``config`` (repo-relative posix path), ``dir`` (containing directory, ``"."`` at
    the root), ``name`` (the worker name declared in the config, or ``None``), ``format``,
    ``runtime_id`` and ``error``.

    ``runtime_id`` is what a caller needs to build ``infrastructure.runtimes[]``: it keeps the
    historical ``wrangler.api.toml`` -> ``api`` and root ``wrangler.toml`` -> ``worker`` mapping, and
    only for *nested* bare configs (the monorepo case, where every file is called ``wrangler.jsonc``
    and the relpath rule would collide on ``worker``) does it fall back to the declared worker name
    and then the directory name. Duplicates are suffixed ``-2``, ``-3``, ... in list order.
    """
    r = Path(root).resolve()
    entries: list[dict[str, Any]] = []
    used: dict[str, int] = {}

    for rel in list_wrangler_config_files(r, max_depth=max_depth):
        parsed = read_wrangler_config(r / rel)
        parent = rel.parent
        runtime_id = runtime_id_from_wrangler_relpath(rel)
        if runtime_id == "worker" and len(rel.parts) > 1:
            runtime_id = _slug(parsed["name"]) or _slug(parent.name) or "worker"
        count = used.get(runtime_id, 0) + 1
        used[runtime_id] = count
        entries.append(
            {
                "config": rel.as_posix(),
                "dir": parent.as_posix(),
                "name": parsed["name"],
                "format": parsed["format"],
                "runtime_id": runtime_id if count == 1 else f"{runtime_id}-{count}",
                "error": parsed["error"],
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def list_wrangler_pages_config_files(root: Path, *, max_depth: int = 6) -> list[Path]:
    """Wrangler configs that describe a Pages project (``wrangler.pages.toml``/``.jsonc``/``.json``)."""
    found = _walk_wrangler_configs(root, max_depth=max_depth, pages=True)

    def _sort_key(rel: Path) -> tuple:
        name = rel.name.lower()
        ext_rank = _EXTENSION_RANK.get(wrangler_config_format(name), 9)
        if _wrangler_stem(name) == "wrangler.pages":
            return (0, ext_rank, str(rel))
        if rel.parts and rel.parts[0].lower() == "infra":
            return (1, ext_rank, str(rel))
        return (2, ext_rank, str(rel))

    found.sort(key=_sort_key)
    return found


def pages_runtime_id_from_config(rel: Path) -> str:
    """``infra/wrangler.pages.toml`` -> ``dashboard``; other ``*.pages.*`` -> ``pages``."""
    name = rel.name.lower()
    if "pages" in name:
        return "dashboard"
    return "pages"


def read_pages_build_output_dir(config_path: Path) -> str | None:
    """Parse ``pages_build_output_dir`` from a wrangler Pages config (relative to the config dir)."""
    return read_wrangler_config(Path(config_path))["pages_build_output_dir"]


def resolve_pages_asset_dir(app_root: Path, config_rel: Path) -> Path | None:
    """Resolved absolute directory of built static assets for ``wrangler pages dev``."""
    cfg = (app_root / config_rel).resolve()
    if not cfg.is_file():
        return None
    out_rel = read_pages_build_output_dir(cfg)
    if not out_rel:
        return None
    out = Path(out_rel)
    if out.is_absolute():
        return out if out.is_dir() else None
    return (cfg.parent / out).resolve()

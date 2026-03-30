"""Detect and parse wrangler.toml for Cloudflare Worker projects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib

WRANGLER_PATHS = (
    Path("wrangler.toml"),
    Path("cloudflare") / "wrangler.toml",
)


@dataclass
class WranglerDetection:
    config_path: Path | None = None
    """Relative to scan root."""
    worker_name: str | None = None
    main: str | None = None
    kv_bindings: list[str] = field(default_factory=list)
    r2_bindings: list[str] = field(default_factory=list)
    d1_bindings: list[str] = field(default_factory=list)
    browser_binding: str | None = None
    env_sections: list[str] = field(default_factory=list)


def _binding_names(rows: list[dict[str, Any]] | None, key: str = "binding") -> list[str]:
    if not rows:
        return []
    out: list[str] = []
    for row in rows:
        if isinstance(row, dict) and key in row:
            out.append(str(row[key]))
    return out


def _parse_wrangler_data(data: dict[str, Any]) -> tuple[str | None, str | None, list[str], list[str], list[str], str | None]:
    name = data.get("name")
    main = data.get("main")
    kv = _binding_names(data.get("kv_namespaces"))
    r2 = _binding_names(data.get("r2_buckets"))
    d1 = _binding_names(data.get("d1_databases"))
    browser = None
    b = data.get("browser")
    if isinstance(b, dict) and "binding" in b:
        browser = str(b["binding"])
    return (
        str(name) if name else None,
        str(main) if main else None,
        kv,
        r2,
        d1,
        browser,
    )


def _env_names(data: dict[str, Any]) -> list[str]:
    """Table names under [env.NAME] in wrangler.toml."""
    env = data.get("env")
    if not isinstance(env, dict):
        return []
    return sorted(k for k in env if k != "default")


def detect_wrangler(root: Path) -> WranglerDetection:
    root = root.resolve()
    det = WranglerDetection()
    chosen: Path | None = None
    for rel in WRANGLER_PATHS:
        if (root / rel).is_file():
            chosen = rel
            break
    if not chosen:
        return det

    det.config_path = chosen
    try:
        raw = (root / chosen).read_text(encoding="utf-8")
        data = tomllib.loads(raw)
    except (OSError, tomllib.TOMLDecodeError):
        return det

    n, m, kv, r2, d1, browser = _parse_wrangler_data(data)
    det.worker_name = n
    det.main = m
    det.kv_bindings = kv
    det.r2_bindings = r2
    det.d1_bindings = d1
    det.browser_binding = browser
    det.env_sections = _env_names(data)

    return det


def list_likely_secret_var_keys(wrangler_root: Path, config_rel: Path, env_name: str | None) -> list[str]:
    """Heuristic: var keys in wrangler.toml that often should be `wrangler secret put`."""
    path = wrangler_root / config_rel
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []

    def vars_from(section: dict[str, Any] | None) -> dict[str, Any]:
        if not section:
            return {}
        v = section.get("vars")
        return v if isinstance(v, dict) else {}

    keys: set[str] = set()
    for k, v in vars_from(data).items():
        if _looks_sensitive_key(k):
            keys.add(k)

    env = data.get("env")
    if isinstance(env, dict):
        if env_name and env_name in env and isinstance(env[env_name], dict):
            for k, v in vars_from(env[env_name]).items():
                if _looks_sensitive_key(k):
                    keys.add(k)
        else:
            for _ename, esection in env.items():
                if isinstance(esection, dict):
                    for k, v in vars_from(esection).items():
                        if _looks_sensitive_key(k):
                            keys.add(k)

    return sorted(keys)


def _looks_sensitive_key(name: str) -> bool:
    u = name.upper()
    needles = (
        "SECRET",
        "PASSWORD",
        "TOKEN",
        "API_KEY",
        "APIKEY",
        "_KEY",
        "PRIVATE",
        "CREDENTIAL",
    )
    return any(n in u for n in needles)

"""Map wrangler.toml → LocalCfResourcePlan (adapter only; no file writes)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tomllib

from leco_app.resource_plan import (
    D1DatabaseRow,
    KvNamespaceRow,
    LocalCfResourcePlan,
    R2BucketRow,
)

# Backwards-compatible names
WranglerCfResourcePlan = LocalCfResourcePlan


def _as_dict_list(v: Any) -> list[dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out: list[dict[str, Any]] = []
    for item in v:
        if isinstance(item, dict):
            out.append(item)
    return out


def _union_by_binding(
    top: list[dict[str, Any]],
    env: list[dict[str, Any]],
    *,
    binding_key: str = "binding",
) -> list[dict[str, Any]]:
    """Env rows override top-level rows with the same binding; stable key order."""
    by_b: dict[str, dict[str, Any]] = {}
    for row in top:
        b = row.get(binding_key)
        if isinstance(b, str) and b.strip():
            by_b[b.strip()] = dict(row)
    for row in env:
        b = row.get(binding_key)
        if isinstance(b, str) and b.strip():
            by_b[b.strip()] = dict(row)
    return [by_b[k] for k in sorted(by_b.keys())]


def _kv_rows_for_env(
    data: dict[str, Any],
    env_section: dict[str, Any] | None,
    wrangler_env: str | None,
) -> list[dict[str, Any]]:
    top = _as_dict_list(data.get("kv_namespaces"))
    env_kv = _as_dict_list(env_section.get("kv_namespaces")) if env_section else []
    if wrangler_env:
        return _union_by_binding(top, env_kv)
    return top


def _r2_rows_for_env(
    data: dict[str, Any],
    env_section: dict[str, Any] | None,
    wrangler_env: str | None,
) -> list[dict[str, Any]]:
    top = _as_dict_list(data.get("r2_buckets"))
    env_r2 = _as_dict_list(env_section.get("r2_buckets")) if env_section else []
    if wrangler_env:
        return _union_by_binding(top, env_r2)
    return top


def _d1_rows_for_env(
    data: dict[str, Any],
    env_section: dict[str, Any] | None,
    wrangler_env: str | None,
) -> list[dict[str, Any]]:
    top = _as_dict_list(data.get("d1_databases"))
    env_d1 = _as_dict_list(env_section.get("d1_databases")) if env_section else []
    if wrangler_env:
        return _union_by_binding(top, env_d1)
    return top


def _kv_cf_id(row: dict[str, Any]) -> str | None:
    for key in ("id", "preview_id"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def parse_wrangler_cf_resources(wrangler_path: Path, wrangler_env: str | None) -> LocalCfResourcePlan:
    raw = wrangler_path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    env_section: dict[str, Any] | None = None
    if wrangler_env:
        env_root = data.get("env")
        if isinstance(env_root, dict):
            cand = env_root.get(wrangler_env)
            if isinstance(cand, dict):
                env_section = cand

    kv_raw = _kv_rows_for_env(data, env_section, wrangler_env)
    r2_raw = _r2_rows_for_env(data, env_section, wrangler_env)
    d1_raw = _d1_rows_for_env(data, env_section, wrangler_env)

    kv: list[KvNamespaceRow] = []
    for row in kv_raw:
        b = row.get("binding")
        cid = _kv_cf_id(row)
        if isinstance(b, str) and b.strip() and cid:
            kv.append(KvNamespaceRow(binding=b.strip(), cf_id=cid))

    r2: list[R2BucketRow] = []
    for row in r2_raw:
        b = row.get("binding")
        bn = row.get("bucket_name")
        if isinstance(b, str) and b.strip() and isinstance(bn, str) and bn.strip():
            r2.append(R2BucketRow(binding=b.strip(), bucket_name=bn.strip()))

    d1: list[D1DatabaseRow] = []
    for row in d1_raw:
        b = row.get("binding")
        dn = row.get("database_name")
        if isinstance(b, str) and b.strip() and isinstance(dn, str) and dn.strip():
            d1.append(D1DatabaseRow(binding=b.strip(), database_name=dn.strip()))

    return LocalCfResourcePlan(kv=kv, r2=r2, d1=d1)


def local_kv_namespace_name(app_slug: str, binding: str, cf_id: str) -> str:
    """Re-export for callers that imported from this module."""
    from leco_app.resource_plan import local_kv_namespace_name as _lk

    return _lk(app_slug, binding, cf_id)

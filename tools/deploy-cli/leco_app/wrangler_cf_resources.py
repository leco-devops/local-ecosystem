"""Read KV / R2 / D1 resource tables from wrangler.toml (no file writes)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomllib


@dataclass
class KvNamespaceRow:
    binding: str
    cf_id: str


@dataclass
class R2BucketRow:
    binding: str
    bucket_name: str


@dataclass
class D1DatabaseRow:
    binding: str
    database_name: str


@dataclass
class WranglerCfResourcePlan:
    """Effective bindings for one wrangler env slice (top-level or [env.NAME])."""

    kv: list[KvNamespaceRow] = field(default_factory=list)
    r2: list[R2BucketRow] = field(default_factory=list)
    d1: list[D1DatabaseRow] = field(default_factory=list)


def _as_dict_list(v: Any) -> list[dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out: list[dict[str, Any]] = []
    for item in v:
        if isinstance(item, dict):
            out.append(item)
    return out


def _merge_table(top: list[dict[str, Any]], env_section: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    """Use env-specific table when present and non-empty; else top-level."""
    if env_section and isinstance(env_section.get(key), list) and len(env_section[key]) > 0:
        return _as_dict_list(env_section[key])
    return _as_dict_list(top)


def parse_wrangler_cf_resources(wrangler_path: Path, wrangler_env: str | None) -> WranglerCfResourcePlan:
    raw = wrangler_path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    env_section: dict[str, Any] | None = None
    if wrangler_env:
        env_root = data.get("env")
        if isinstance(env_root, dict):
            cand = env_root.get(wrangler_env)
            if isinstance(cand, dict):
                env_section = cand

    kv_raw = _merge_table(_as_dict_list(data.get("kv_namespaces")), env_section, "kv_namespaces")
    r2_raw = _merge_table(_as_dict_list(data.get("r2_buckets")), env_section, "r2_buckets")
    d1_raw = _merge_table(_as_dict_list(data.get("d1_databases")), env_section, "d1_databases")

    kv: list[KvNamespaceRow] = []
    for row in kv_raw:
        b = row.get("binding")
        cid = row.get("id")
        if isinstance(b, str) and b.strip() and isinstance(cid, str) and cid.strip():
            kv.append(KvNamespaceRow(binding=b.strip(), cf_id=cid.strip()))

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

    return WranglerCfResourcePlan(kv=kv, r2=r2, d1=d1)


def local_kv_namespace_name(app_slug: str, binding: str, cf_id: str) -> str:
    """Dedicated KV namespace on kv-adapter: app + binding + stable id fragment (mirrors prod namespace id)."""
    slug = app_slug.strip().replace(" ", "-").lower()
    frag = cf_id.replace("-", "")[:12] if len(cf_id.replace("-", "")) >= 8 else cf_id
    safe_binding = binding.replace(" ", "_")
    return f"{slug}__{safe_binding}__{frag}"

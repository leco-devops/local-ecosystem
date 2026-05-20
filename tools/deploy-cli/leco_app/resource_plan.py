"""Neutral model for local Cloudflare-adapter resources (KV / R2 / D1).

Wrangler and future discovery adapters map into this plan; provision executes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KvNamespaceRow:
    binding: str
    cf_id: str
    """Cloudflare namespace id or preview_id — used only for stable local naming."""


@dataclass
class R2BucketRow:
    binding: str
    bucket_name: str


@dataclass
class D1DatabaseRow:
    binding: str
    database_name: str


@dataclass
class LocalCfResourcePlan:
    """Resources to create on local kv.lh / r2.lh / d1.lh adapters."""

    kv: list[KvNamespaceRow] = field(default_factory=list)
    r2: list[R2BucketRow] = field(default_factory=list)
    d1: list[D1DatabaseRow] = field(default_factory=list)

    def total_bindings(self) -> int:
        return len(self.kv) + len(self.r2) + len(self.d1)

    def is_empty(self) -> bool:
        return self.total_bindings() == 0


def local_kv_namespace_name(app_slug: str, binding: str, cf_id: str) -> str:
    """Dedicated KV namespace on kv-adapter: app + binding + stable id fragment."""
    slug = app_slug.strip().replace(" ", "-").lower()
    frag = cf_id.replace("-", "")[:12] if len(cf_id.replace("-", "")) >= 8 else cf_id
    safe_binding = binding.replace(" ", "_")
    return f"{slug}__{safe_binding}__{frag}"

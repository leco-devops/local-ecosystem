"""Remove KV/R2/D1 resources listed in leco.local-cf.yaml (dashboard → adapters on lh-network)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import yaml

KV_BASE = os.getenv("DASHBOARD_KV_ADAPTER_URL", "http://kv-adapter:8082").rstrip("/")
R2_BASE = os.getenv("DASHBOARD_R2_ADAPTER_URL", "http://r2-adapter:8081").rstrip("/")
D1_BASE = os.getenv("DASHBOARD_D1_ADAPTER_URL", "http://d1-adapter:8083").rstrip("/")
TIMEOUT = 45.0


def teardown_from_leco_local_cf_file(cf_path: Path) -> dict[str, Any]:
    """DELETE resources; idempotent. Returns {ok, removed: {kv,r2,d1}, errors: [...]}."""
    out: dict[str, Any] = {"ok": True, "removed": {"kv": [], "r2": [], "d1": []}, "errors": []}
    if not cf_path.is_file():
        out["ok"] = True
        out["skipped"] = "no leco.local-cf.yaml"
        return out
    try:
        doc = yaml.safe_load(cf_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
        out["ok"] = False
        out["errors"].append(str(e))
        return out
    if not isinstance(doc, dict):
        out["errors"].append("invalid leco.local-cf.yaml")
        out["ok"] = False
        return out

    for row in doc.get("kv") or []:
        if not isinstance(row, dict):
            continue
        ns = row.get("localNamespace") or row.get("local_namespace")
        if not ns:
            continue
        url = f"{KV_BASE}/namespaces/{quote(str(ns), safe='')}"
        try:
            r = requests.delete(url, timeout=TIMEOUT)
            if r.status_code in (200, 404):
                out["removed"]["kv"].append(str(ns))
            else:
                out["errors"].append(f"KV {ns}: HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as e:
            out["errors"].append(f"KV {ns}: {e}")

    for row in doc.get("r2") or []:
        if not isinstance(row, dict):
            continue
        bn = row.get("bucketName") or row.get("bucket_name")
        if not bn:
            continue
        url = f"{R2_BASE}/buckets/{quote(str(bn), safe='')}"
        try:
            r = requests.delete(url, timeout=TIMEOUT)
            if r.status_code in (200, 404):
                out["removed"]["r2"].append(str(bn))
            else:
                out["errors"].append(f"R2 {bn}: HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as e:
            out["errors"].append(f"R2 {bn}: {e}")

    for row in doc.get("d1") or []:
        if not isinstance(row, dict):
            continue
        dn = row.get("databaseName") or row.get("database_name")
        if not dn:
            continue
        url = f"{D1_BASE}/databases/{quote(str(dn), safe='')}"
        try:
            r = requests.delete(url, timeout=TIMEOUT)
            if r.status_code in (200, 404):
                out["removed"]["d1"].append(str(dn))
            else:
                out["errors"].append(f"D1 {dn}: HTTP {r.status_code} {r.text[:120]}")
        except requests.RequestException as e:
            out["errors"].append(f"D1 {dn}: {e}")

    out["ok"] = len(out["errors"]) == 0
    return out

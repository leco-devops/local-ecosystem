"""Delete KV/R2/D1 entries described in leco.local-cf.yaml (urllib, no extra deps)."""

from __future__ import annotations

import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import yaml

Echo = Callable[[str], None]


def _bases() -> dict[str, str]:
    return {
        "kv": os.environ.get("LECO_LOCAL_KV_URL", "https://kv.lh").rstrip("/"),
        "r2": os.environ.get("LECO_LOCAL_R2_URL", "https://r2.lh").rstrip("/"),
        "d1": os.environ.get("LECO_LOCAL_D1_URL", "https://d1.lh").rstrip("/"),
    }


def _delete(url: str, *, timeout: float = 45.0) -> tuple[int, str]:
    req = urllib.request.Request(url, method="DELETE")
    ctx = None
    if os.environ.get("LECO_LOCAL_CF_INSECURE_SSL", "").strip() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")[:200]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200] if e.fp else ""
        return e.code, body
    except Exception as e:
        return -1, str(e)


def teardown_from_leco_local_cf_path(cf_path: Path, echo: Echo | None = None) -> int:
    """Returns number of failures."""
    log = echo or (lambda _m: None)
    if not cf_path.is_file():
        log("No leco.local-cf.yaml — skipping shared resource cleanup.")
        return 0
    try:
        doc = yaml.safe_load(cf_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
        log(f"Could not read {cf_path}: {e}")
        return 1
    if not isinstance(doc, dict):
        return 1
    bases = _bases()
    failed = 0

    for row in doc.get("kv") or []:
        if not isinstance(row, dict):
            continue
        ns = row.get("localNamespace") or row.get("local_namespace")
        if not ns:
            continue
        url = f"{bases['kv']}/namespaces/{quote(str(ns), safe='')}"
        code, _body = _delete(url)
        if code in (200, 404):
            log(f"  KV namespace removed (or absent): {ns}")
        else:
            log(f"  KV namespace delete failed {ns}: HTTP {code}")
            failed += 1

    for row in doc.get("r2") or []:
        if not isinstance(row, dict):
            continue
        bn = row.get("bucketName") or row.get("bucket_name")
        if not bn:
            continue
        url = f"{bases['r2']}/buckets/{quote(str(bn), safe='')}"
        code, _body = _delete(url)
        if code in (200, 404):
            log(f"  R2 bucket removed (or absent): {bn}")
        else:
            log(f"  R2 bucket delete failed {bn}: HTTP {code}")
            failed += 1

    for row in doc.get("d1") or []:
        if not isinstance(row, dict):
            continue
        dn = row.get("databaseName") or row.get("database_name")
        if not dn:
            continue
        url = f"{bases['d1']}/databases/{quote(str(dn), safe='')}"
        code, _body = _delete(url)
        if code in (200, 404):
            log(f"  D1 database removed (or absent): {dn}")
        else:
            log(f"  D1 database delete failed {dn}: HTTP {code}")
            failed += 1

    return failed

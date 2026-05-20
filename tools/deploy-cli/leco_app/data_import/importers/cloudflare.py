"""D1 / R2 / KV import via leco.local-cf.yaml adapter HTTP APIs."""

from __future__ import annotations

import gzip
import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from leco_app.data_import.context import ImportContext


def _http_post(url: str, payload: dict[str, Any], timeout: int = 120) -> tuple[bool, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx_ssl = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx_ssl) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return True, body
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", errors="replace")[:2000]
    except Exception as exc:
        return False, str(exc)


def _http_put(url: str, body: bytes, timeout: int = 120) -> tuple[bool, str]:
    req = urllib.request.Request(url, data=body, method="PUT")
    ctx_ssl = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx_ssl) as resp:
            return True, resp.read().decode("utf-8", errors="replace")[:500]
    except urllib.error.HTTPError as exc:
        return False, exc.read().decode("utf-8", errors="replace")[:2000]
    except Exception as exc:
        return False, str(exc)


def _cf_row(local_cf: dict[str, Any], kind: str, name: str) -> dict[str, Any] | None:
    rows = local_cf.get(kind) or []
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if kind == "d1" and str(row.get("databaseName") or "") == name:
            return row
        if kind == "r2" and str(row.get("bucketName") or "") == name:
            return row
        if kind == "kv" and str(row.get("localNamespace") or row.get("namespace") or "") == name:
            return row
    return rows[0] if len(rows) == 1 and not name else None


def _read_sql(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
            return f.read()
    return path.read_text(encoding="utf-8", errors="replace")


def import_d1(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    if not ctx.local_cf:
        return False, "leco.local-cf.yaml missing — run Deploy with local CF provision first"

    rel = str(entry.get("path") or "").strip()
    src = ctx.data_dir / rel
    if not src.is_file():
        return False, f"SQL file not found: {rel}"

    db_name = str(entry.get("database") or src.stem.replace(".sql", "")).strip()
    row = _cf_row(ctx.local_cf, "d1", db_name)
    if not row:
        return False, f"No D1 database {db_name!r} in leco.local-cf.yaml"

    base = str(row.get("queryUrl") or "").rstrip("/")
    if not base:
        adapters = ctx.local_cf.get("adapters") or {}
        base = f"{adapters.get('d1', 'https://d1.lh')}/databases/{db_name}/execute"
    elif not base.endswith("/execute"):
        base = base.replace("/query", "/execute")

    if ctx.dry_run:
        ctx.log(f"[d1] dry-run: would execute SQL from {rel}")
        return True, ""

    sql = _read_sql(src)
    statements = [s.strip() for s in re.split(r";\s*\n", sql) if s.strip()]
    ctx.log(f"[d1] Executing {len(statements)} statement(s) on {db_name}…")
    for i, stmt in enumerate(statements, 1):
        if not stmt.endswith(";"):
            stmt += ";"
        ok, out = _http_post(base, {"sql": stmt})
        if not ok:
            return False, f"Statement {i} failed: {out[:500]}"
        if i % 10 == 0:
            ctx.log(f"[d1] … {i}/{len(statements)} statements")
    return True, ""


def import_r2(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    if not ctx.local_cf:
        return False, "leco.local-cf.yaml missing — run Deploy with local CF provision first"

    rel = str(entry.get("path") or "").strip()
    src = ctx.data_dir / rel
    if not src.is_dir():
        return False, f"R2 directory not found: {rel}"

    bucket = str(entry.get("bucket") or src.name).strip()
    row = _cf_row(ctx.local_cf, "r2", bucket)
    if not row:
        return False, f"No R2 bucket {bucket!r} in leco.local-cf.yaml"

    prefix = str(row.get("objectsPrefix") or "").rstrip("/") + "/"
    if ctx.dry_run:
        ctx.log(f"[r2] dry-run: would upload tree {rel}")
        return True, ""

    uploaded = 0
    for fp in src.rglob("*"):
        if not fp.is_file():
            continue
        key = str(fp.relative_to(src)).replace("\\", "/")
        url = prefix + key
        body = fp.read_bytes()
        ok, out = _http_put(url, body)
        if not ok:
            return False, f"PUT {key} failed: {out[:300]}"
        uploaded += 1
        if uploaded % 50 == 0:
            ctx.log(f"[r2] … uploaded {uploaded} objects")
    ctx.log(f"[r2] Uploaded {uploaded} object(s) to {bucket}")
    return True, ""


def import_kv(ctx: ImportContext, entry: dict[str, Any]) -> tuple[bool, str]:
    if not ctx.local_cf:
        return False, "leco.local-cf.yaml missing — run Deploy with local CF provision first"

    rel = str(entry.get("path") or "").strip()
    src = ctx.data_dir / rel
    ns = str(entry.get("namespace") or "").strip()

    row = _cf_row(ctx.local_cf, "kv", ns) if ns else None
    if not row:
        rows = ctx.local_cf.get("kv") or []
        row = rows[0] if isinstance(rows, list) and rows else None
    if not row:
        return False, "No KV namespace in leco.local-cf.yaml"

    prefix = str(row.get("putUrlPrefix") or "").rstrip("/") + "/"

    if ctx.dry_run:
        ctx.log(f"[kv] dry-run: would import {rel}")
        return True, ""

    if src.is_file() and src.name == "keys.json":
        data = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False, "keys.json must be an object"
        for key, val in data.items():
            body = val if isinstance(val, (bytes, bytearray)) else str(val).encode("utf-8")
            ok, out = _http_put(prefix + str(key), body)
            if not ok:
                return False, f"KV put {key} failed: {out[:300]}"
        ctx.log(f"[kv] Imported {len(data)} keys")
        return True, ""

    if src.is_dir():
        count = 0
        for fp in src.rglob("*"):
            if not fp.is_file():
                continue
            key = str(fp.relative_to(src)).replace("\\", "/")
            ok, out = _http_put(prefix + key, fp.read_bytes())
            if not ok:
                return False, f"KV put {key} failed: {out[:300]}"
            count += 1
        ctx.log(f"[kv] Imported {count} keys from {rel}")
        return True, ""

    return False, f"Unsupported kv path: {rel}"

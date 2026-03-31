"""Create KV namespaces, R2 buckets, and D1 DBs on local-ecosystem cloudflare-local adapters."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote
from typing import Any, Callable

import yaml

from leco_app.wrangler_cf_resources import (
    WranglerCfResourcePlan,
    local_kv_namespace_name,
    parse_wrangler_cf_resources,
)

Echo = Callable[[str], None]


def _adapter_bases() -> dict[str, str]:
    return {
        "kv": os.environ.get("LECO_LOCAL_KV_URL", "https://kv.lh").rstrip("/"),
        "r2": os.environ.get("LECO_LOCAL_R2_URL", "https://r2.lh").rstrip("/"),
        "d1": os.environ.get("LECO_LOCAL_D1_URL", "https://d1.lh").rstrip("/"),
    }


def _http_post_json(url: str, payload: dict[str, Any], *, timeout: float = 45.0) -> tuple[bool, str, dict[str, Any] | None]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    ctx = None
    if os.environ.get("LECO_LOCAL_CF_INSECURE_SSL", "").strip() in ("1", "true", "yes"):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError:
                return False, f"non-JSON response ({resp.status}): {body[:200]}", None
            if isinstance(parsed, dict) and parsed.get("ok") is True:
                return True, "", parsed
            err = (parsed.get("error") if isinstance(parsed, dict) else None) or body[:300]
            return False, err, parsed if isinstance(parsed, dict) else None
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body.strip() else {}
            err = (parsed.get("error") if isinstance(parsed, dict) else None) or body[:300]
        except Exception:
            err = str(e)
        return False, f"HTTP {e.code}: {err}", None
    except Exception as e:
        return False, str(e), None


def provision_plan(
    plan: WranglerCfResourcePlan,
    *,
    app_slug: str,
    wrangler_env: str | None,
    manifest_path: Path,
    echo: Echo,
) -> int:
    """Returns 0 if all calls succeeded, 1 if any failed."""
    bases = _adapter_bases()
    record: dict[str, Any] = {
        "lecoLocalCfVersion": 1,
        "app": app_slug,
        "wranglerEnv": wrangler_env,
        "adapters": bases,
        "kv": [],
        "r2": [],
        "d1": [],
    }
    failed = 0

    for row in plan.kv:
        local_name = local_kv_namespace_name(app_slug, row.binding, row.cf_id)
        url = f"{bases['kv']}/namespaces"
        ok, err, _ = _http_post_json(url, {"name": local_name})
        if ok:
            echo(f"  KV namespace OK: {local_name} (binding {row.binding})")
            record["kv"].append(
                {
                    "binding": row.binding,
                    "cloudflareId": row.cf_id,
                    "localNamespace": local_name,
                    "putUrlPrefix": f"{bases['kv']}/namespaces/{quote(local_name, safe='')}/values/",
                }
            )
        else:
            echo(f"  KV namespace FAILED ({row.binding}): {err}")
            failed += 1

    for row in plan.r2:
        url = f"{bases['r2']}/buckets"
        ok, err, _ = _http_post_json(url, {"name": row.bucket_name})
        if ok:
            echo(f"  R2 bucket OK: {row.bucket_name} (binding {row.binding})")
            record["r2"].append(
                {
                    "binding": row.binding,
                    "bucketName": row.bucket_name,
                    "objectsPrefix": f"{bases['r2']}/objects/{quote(row.bucket_name, safe='')}/",
                }
            )
        else:
            echo(f"  R2 bucket FAILED ({row.binding}): {err}")
            failed += 1

    for row in plan.d1:
        url = f"{bases['d1']}/databases"
        ok, err, _ = _http_post_json(url, {"name": row.database_name})
        if ok:
            echo(f"  D1 database OK: {row.database_name} (binding {row.binding})")
            record["d1"].append(
                {
                    "binding": row.binding,
                    "databaseName": row.database_name,
                    "queryUrl": f"{bases['d1']}/databases/{quote(row.database_name, safe='')}/query",
                }
            )
        else:
            echo(f"  D1 database FAILED ({row.binding}): {err}")
            failed += 1

    out_path = manifest_path.parent / "leco.local-cf.yaml"
    out_path.write_text(
        yaml.safe_dump(record, default_flow_style=False, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    echo(f"Wrote resource map: {out_path}")
    return 1 if failed else 0


def provision_from_manifest(
    manifest_path: Path,
    *,
    app_slug: str,
    wrangler_env: str | None,
    echo: Echo,
) -> int:
    from leco_app.schema import load_manifest

    m = load_manifest(manifest_path)
    if not m.cloudflare or not m.cloudflare.wrangler_config:
        echo("No cloudflare.wranglerConfig in manifest — skipping local CF provision.")
        return 0
    root = m.resolved_root(manifest_path)
    wp = (root / m.cloudflare.wrangler_config).resolve()
    if not wp.is_file():
        echo(f"Wrangler config not found: {wp}")
        return 1
    env = wrangler_env if wrangler_env is not None else m.cloudflare.wrangler_env
    plan = parse_wrangler_cf_resources(wp, env)
    if not plan.kv and not plan.r2 and not plan.d1:
        echo(f"No KV/R2/D1 tables in {wp.name}" + (f" (env {env!r})" if env else "") + ".")
        return 0
    echo(
        "Provisioning local Cloudflare-local resources (from wrangler, not modifying wrangler.toml)…\n"
        f"  wrangler: {wp}\n"
        f"  env: {env or '(top-level)'}\n"
        f"  KV: {len(plan.kv)} · R2: {len(plan.r2)} · D1: {len(plan.d1)}"
    )
    return provision_plan(plan, app_slug=app_slug, wrangler_env=env, manifest_path=manifest_path, echo=echo)

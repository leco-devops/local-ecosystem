"""Create KV namespaces, R2 buckets, and D1 DBs on local-ecosystem cloudflare-local adapters."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import yaml

from leco_app.local_cf_policy import should_provision_local_cf
from leco_app.resource_plan import LocalCfResourcePlan, local_kv_namespace_name
from leco_app.wrangler_cf_resources import parse_wrangler_cf_resources

Echo = Callable[[str], None]


def adapter_http_bases() -> dict[str, str]:
    """URLs for urllib calls (provision/teardown). Prefer Docker DNS when running inside service-dashboard."""
    return {
        "kv": (
            os.environ.get("LECO_LOCAL_KV_INTERNAL_URL")
            or os.environ.get("LECO_LOCAL_KV_URL")
            or "https://kv.lh"
        ).rstrip("/"),
        "r2": (
            os.environ.get("LECO_LOCAL_R2_INTERNAL_URL")
            or os.environ.get("LECO_LOCAL_R2_URL")
            or "https://r2.lh"
        ).rstrip("/"),
        "d1": (
            os.environ.get("LECO_LOCAL_D1_INTERNAL_URL")
            or os.environ.get("LECO_LOCAL_D1_URL")
            or "https://d1.lh"
        ).rstrip("/"),
    }


def adapter_record_bases_default() -> dict[str, str]:
    """Public bases written to leco.local-cf.yaml (apps / Workers). Ignores *_INTERNAL_URL."""
    return {
        "kv": (os.environ.get("LECO_LOCAL_KV_URL") or "https://kv.lh").rstrip("/"),
        "r2": (os.environ.get("LECO_LOCAL_R2_URL") or "https://r2.lh").rstrip("/"),
        "d1": (os.environ.get("LECO_LOCAL_D1_URL") or "https://d1.lh").rstrip("/"),
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
    plan: LocalCfResourcePlan,
    *,
    app_slug: str,
    wrangler_env: str | None,
    manifest_path: Path,
    echo: Echo,
    http_bases: dict[str, str] | None = None,
    record_bases: dict[str, str] | None = None,
) -> int:
    """Returns 0 if all calls succeeded, 1 if any failed.

    ``http_bases`` — used for POST to adapters (often ``http://kv-adapter:8082`` from service-dashboard).
    ``record_bases`` — written to ``leco.local-cf.yaml`` (usually ``https://kv.lh`` for app containers).
    """
    hb = http_bases if http_bases is not None else adapter_http_bases()
    rb = record_bases if record_bases is not None else adapter_record_bases_default()
    record: dict[str, Any] = {
        "lecoLocalCfVersion": 1,
        "app": app_slug,
        "wranglerEnv": wrangler_env,
        "adapters": rb,
        "kv": [],
        "r2": [],
        "d1": [],
    }
    failed = 0
    total = plan.total_bindings()

    for row in plan.kv:
        local_name = local_kv_namespace_name(app_slug, row.binding, row.cf_id)
        url = f"{hb['kv']}/namespaces"
        ok, err, _ = _http_post_json(url, {"name": local_name})
        if ok:
            echo(f"  KV namespace OK: {local_name} (binding {row.binding})")
            record["kv"].append(
                {
                    "binding": row.binding,
                    "cloudflareId": row.cf_id,
                    "localNamespace": local_name,
                    "putUrlPrefix": f"{rb['kv']}/namespaces/{quote(local_name, safe='')}/values/",
                }
            )
        else:
            echo(f"  KV namespace FAILED ({row.binding}): {err}")
            failed += 1

    for row in plan.r2:
        url = f"{hb['r2']}/buckets"
        ok, err, _ = _http_post_json(url, {"name": row.bucket_name})
        if ok:
            echo(f"  R2 bucket OK: {row.bucket_name} (binding {row.binding})")
            record["r2"].append(
                {
                    "binding": row.binding,
                    "bucketName": row.bucket_name,
                    "objectsPrefix": f"{rb['r2']}/objects/{quote(row.bucket_name, safe='')}/",
                }
            )
        else:
            echo(f"  R2 bucket FAILED ({row.binding}): {err}")
            failed += 1

    for row in plan.d1:
        url = f"{hb['d1']}/databases"
        ok, err, _ = _http_post_json(url, {"name": row.database_name})
        if ok:
            echo(f"  D1 database OK: {row.database_name} (binding {row.binding})")
            record["d1"].append(
                {
                    "binding": row.binding,
                    "databaseName": row.database_name,
                    "queryUrl": f"{rb['d1']}/databases/{quote(row.database_name, safe='')}/query",
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
    ok_n = total - failed
    echo(
        f"Local CF provision summary: {ok_n}/{total} resource(s) OK "
        f"({len(plan.kv)} KV, {len(plan.r2)} R2, {len(plan.d1)} D1)."
    )
    return 1 if failed else 0


def _echo_skip_local_cf(echo: Echo, *, no_provision_cli: bool, ignore_policy: bool) -> None:
    if ignore_policy:
        return
    if no_provision_cli:
        echo("Skipping local CF provision (--no-provision-local-cf).")
        return
    raw = (os.environ.get("LECO_PROVISION_LOCAL_CF") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        echo("Skipping local CF provision (LECO_PROVISION_LOCAL_CF is disabled).")
        return
    echo("Skipping local CF provision (cloudflare.provisionLocalResources: false in manifest).")


def provision_from_manifest(
    manifest_path: Path,
    *,
    app_slug: str,
    wrangler_env: str | None,
    echo: Echo,
    no_provision_local_cf: bool = False,
    ignore_policy: bool = False,
) -> int:
    """
    Parse wrangler from manifest and provision local adapters.

    ``ignore_policy=True`` (e.g. ``leco-app provision-local-cf``) skips manifest/env policy
    and runs whenever ``cloudflare.wranglerConfig`` resolves to a file.
    """
    from leco_app.schema import load_effective_manifest

    m = load_effective_manifest(manifest_path)
    if not m.cloudflare or not (m.cloudflare.wrangler_config or "").strip():
        if ignore_policy:
            echo("No cloudflare.wranglerConfig in manifest — nothing to provision.")
        return 0
    if not ignore_policy and not should_provision_local_cf(m, cli_skip=no_provision_local_cf):
        _echo_skip_local_cf(echo, no_provision_cli=no_provision_local_cf, ignore_policy=ignore_policy)
        return 0
    hb = adapter_http_bases()
    rb = adapter_record_bases_default()
    if m.cloudflare.local_cf_public_prefix:
        pfx = m.cloudflare.local_cf_public_prefix
        rb = {
            "kv": f"https://{pfx}-kv.lh",
            "r2": f"https://{pfx}-r2.lh",
            "d1": f"https://{pfx}-d1.lh",
        }
    root = m.resolved_root(manifest_path)
    wp = (root / m.cloudflare.wrangler_config).resolve()
    if not wp.is_file():
        echo(f"Wrangler config not found: {wp}")
        return 1
    env = wrangler_env if wrangler_env is not None else m.cloudflare.wrangler_env
    plan = parse_wrangler_cf_resources(wp, env)
    if plan.is_empty():
        echo(f"No KV/R2/D1 tables in {wp.name}" + (f" (env {env!r})" if env else "") + ".")
        return 0
    host_line = ""
    if m.cloudflare.local_cf_public_prefix:
        host_line = (
            f"\n  public adapter hosts: {rb['kv']}, {rb['r2']}, {rb['d1']} "
            f"(Traefik must merge localCfPublicPrefix routes; re-run ecosystem-register if needed)"
        )
    echo(
        "Provisioning local Cloudflare-local resources (from wrangler, not modifying wrangler.toml)…\n"
        f"  wrangler: {wp}\n"
        f"  env: {env or '(top-level)'}\n"
        f"  KV: {len(plan.kv)} · R2: {len(plan.r2)} · D1: {len(plan.d1)}"
        f"{host_line}"
    )
    return provision_plan(
        plan,
        app_slug=app_slug,
        wrangler_env=env,
        manifest_path=manifest_path,
        echo=echo,
        http_bases=hb,
        record_bases=rb,
    )

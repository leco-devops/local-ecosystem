"""Hosted apps (leco-registry) API: list, snapshot, logs, insights."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
import yaml

from leco_detect import compute_resolved_paths_for_leco_app_manifest, host_slug_from_app_id
from leco_control import (
    compose_ps_result,
    leco_meta_for_slug,
    leco_stack_runtime,
    leco_target_id_for_slug,
    load_leco_registry_entries,
    materialized_hosting_apps_not_in_registry,
)
from monitor import get_container_metrics, get_docker_client, get_docker_overview

CONTROL_TOKEN = os.getenv("DASHBOARD_CONTROL_TOKEN", "").strip()
HEALTH_PROBES_ENV = "DASHBOARD_HOSTED_APP_HEALTH_PROBES"


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _read_manifest_raw(manifest_path: str) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _empty_manifest_ui() -> dict[str, Any]:
    return {
        "routes": [],
        "health_urls": [],
        "local_host_profile": None,
        "localhost_archetype": None,
        "localhost_urls": [],
        "localhost_lifecycle": {},
        "application_version": None,
        "deploy_fingerprint": None,
        "local_cf": {"present": False},
        "wrangler_expected": {},
        "local_cf_public_prefix": None,
        "local_cf_adapter_hosts": None,
        "dedicated_local_adapters": False,
        "effective_has_docker_compose": False,
        "profile_docker_compose": None,
        "profile_cloudflare": None,
        "main_url": "",
        "main_url_source": "",
        "derived_main_url": "",
        "main_urls": {},
        "derived_main_urls": {},
        "endpoint_urls": [],
        "source_location": "",
        "resolved_paths": {},
    }


def _profile_docker_compose_ui(infra: dict[str, Any]) -> dict[str, Any] | None:
    dc = infra.get("dockerCompose") or infra.get("docker_compose")
    if not isinstance(dc, dict):
        return None
    cf = dc.get("composeFile") or dc.get("compose_file")
    cfm = dc.get("composeFileFromManifest") or dc.get("compose_file_from_manifest")
    cf_s = cf.strip() if isinstance(cf, str) and cf.strip() else ""
    cfm_s = cfm.strip() if isinstance(cfm, str) and cfm.strip() else ""
    if not cf_s and not cfm_s:
        return None
    raw_ex = dc.get("additionalComposeFiles") or dc.get("additional_compose_files")
    extras: list[str] = []
    if isinstance(raw_ex, list):
        extras = [str(x).strip() for x in raw_ex if isinstance(x, str) and str(x).strip()]
    raw_m = dc.get("additionalComposeFilesFromManifest") or dc.get("additional_compose_files_from_manifest")
    man_ex: list[str] = []
    if isinstance(raw_m, list):
        man_ex = [str(x).strip() for x in raw_m if isinstance(x, str) and str(x).strip()]
    return {
        "compose_file": cf_s or None,
        "compose_file_from_manifest": cfm_s or None,
        "additional_compose_files": extras,
        "additional_compose_files_from_manifest": man_ex,
    }


def _profile_cloudflare_ui(infra: dict[str, Any]) -> dict[str, Any] | None:
    cf = infra.get("cloudflare")
    if not isinstance(cf, dict):
        return None
    w = cf.get("wranglerConfig") or cf.get("wrangler_config")
    wc = str(w).strip() if isinstance(w, str) and w.strip() else ""
    if not wc:
        return None
    ded = cf.get("dedicatedLocalAdapters")
    if ded is None:
        ded = cf.get("dedicated_local_adapters")
    return {"wrangler_config": wc, "dedicated_local_adapters": ded is True}


def _merge_localhost_yaml(file_data: dict[str, Any], inline: dict[str, Any]) -> dict[str, Any]:
    out = dict(file_data) if file_data else {}
    if not inline:
        return out
    if inline.get("archetype"):
        out["archetype"] = inline["archetype"]
    elif not out.get("archetype"):
        out["archetype"] = "generic"
    u_f = out.get("urls") if isinstance(out.get("urls"), list) else []
    u_i = inline.get("urls") if isinstance(inline.get("urls"), list) else []
    out["urls"] = u_f + u_i
    lc_f = out.get("lifecycle") if isinstance(out.get("lifecycle"), dict) else {}
    lc_i = inline.get("lifecycle") if isinstance(inline.get("lifecycle"), dict) else {}
    merged_lc: dict[str, Any] = {}
    for k in ("prepare", "build", "preStart"):
        a = lc_f.get(k) if isinstance(lc_f.get(k), list) else []
        b = lc_i.get(k) if isinstance(lc_i.get(k), list) else []
        merged_lc[k] = list(a) + list(b)
    if merged_lc:
        out["lifecycle"] = merged_lc
    n1 = (out.get("notes") or "").strip()
    n2 = (inline.get("notes") or "").strip()
    if n1 and n2:
        out["notes"] = f"{n1}\n\n{n2}"
    elif n2:
        out["notes"] = n2
    return out


def _manifest_deploy_fingerprint(manifest_path: str) -> dict[str, Any] | None:
    p = Path(manifest_path)
    try:
        st = p.stat()
        raw = p.read_bytes()
        if len(raw) > 524_288:
            raw = raw[:524_288]
        short = hashlib.sha256(raw).hexdigest()[:12]
        return {
            "short_hash": short,
            "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
            "size_bytes": st.st_size,
        }
    except OSError:
        return None


def _application_version_from_manifest(data: dict[str, Any], manifest_path: str) -> str | None:
    for key in (
        "applicationVersion",
        "application_version",
        "appVersion",
        "app_version",
        "version",
    ):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    try:
        from leco_app.schema import load_manifest

        m = load_manifest(Path(manifest_path))
        if m.application_version and str(m.application_version).strip():
            return str(m.application_version).strip()
    except Exception:
        pass
    try:
        root_rel = str(data.get("root") or ".").strip() or "."
        mp = Path(manifest_path)
        root = Path(root_rel).resolve() if Path(root_rel).is_absolute() else (mp.parent / root_rel).resolve()
        pj = root / "package.json"
        if pj.is_file():
            pkg = json.loads(pj.read_text(encoding="utf-8"))
            pv = pkg.get("version")
            if isinstance(pv, str) and pv.strip():
                return f"package.json:{pv.strip()}"
    except Exception:
        pass
    return None


def _read_local_cf_ui(manifest_path: str) -> dict[str, Any]:
    p = Path(manifest_path).resolve().parent / "leco.local-cf.yaml"
    base: dict[str, Any] = {"present": False, "path": str(p)}
    if not p.is_file():
        return base
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as e:
        return {**base, "present": True, "error": str(e)[:200]}
    if not isinstance(doc, dict):
        return {**base, "present": True, "error": "not a mapping"}
    kv_rows = doc.get("kv") if isinstance(doc.get("kv"), list) else []
    r2_rows = doc.get("r2") if isinstance(doc.get("r2"), list) else []
    d1_rows = doc.get("d1") if isinstance(doc.get("d1"), list) else []
    return {
        "present": True,
        "path": str(p),
        "app": doc.get("app"),
        "wrangler_env": doc.get("wranglerEnv"),
        "adapters": doc.get("adapters"),
        "kv": [
            {
                "binding": (r.get("binding") if isinstance(r, dict) else None),
                "local_namespace": (r.get("localNamespace") if isinstance(r, dict) else None),
            }
            for r in kv_rows
            if isinstance(r, dict)
        ],
        "r2": [
            {
                "binding": (r.get("binding") if isinstance(r, dict) else None),
                "bucket": (r.get("bucketName") if isinstance(r, dict) else None),
            }
            for r in r2_rows
            if isinstance(r, dict)
        ],
        "d1": [
            {
                "binding": (r.get("binding") if isinstance(r, dict) else None),
                "database": (r.get("databaseName") if isinstance(r, dict) else None),
            }
            for r in d1_rows
            if isinstance(r, dict)
        ],
    }


def _wrangler_resource_expectations(manifest_path: str) -> dict[str, Any]:
    try:
        from leco_app.schema import load_effective_manifest
        from leco_app.wrangler_cf_resources import parse_wrangler_cf_resources
    except ImportError:
        return {
            "available": False,
            "note": "leco_app (deploy-cli) not installed in this environment.",
        }
    mp = Path(manifest_path)
    if not mp.is_file():
        return {"available": False, "note": "manifest file not found"}
    try:
        m = load_effective_manifest(mp)
    except Exception as e:
        return {"available": False, "note": str(e)[:200]}
    if not m.cloudflare or not (m.cloudflare.wrangler_config or "").strip():
        return {
            "available": True,
            "wrangler_configured": False,
            "note": "No cloudflare.wranglerConfig (leco.yaml infrastructure or leco.app.yaml) — KV/R2/D1 local map is N/A.",
        }
    root = m.resolved_root(mp)
    wp = (root / m.cloudflare.wrangler_config).resolve()
    env = m.cloudflare.wrangler_env
    out: dict[str, Any] = {
        "available": True,
        "wrangler_configured": True,
        "wrangler_path": str(wp),
        "wrangler_env": env,
        "provision_local_resources": m.cloudflare.provision_local_resources,
        "expected_kv": [],
        "expected_r2": [],
        "expected_d1": [],
        "browser_binding": None,
    }
    if not wp.is_file():
        out["note"] = f"Wrangler file missing at {wp}"
        return out
    plan = parse_wrangler_cf_resources(wp, env)
    out["expected_kv"] = [{"binding": r.binding, "cf_id": r.cf_id} for r in plan.kv]
    out["expected_r2"] = [{"binding": r.binding, "bucket_name": r.bucket_name} for r in plan.r2]
    out["expected_d1"] = [{"binding": r.binding, "database_name": r.database_name} for r in plan.d1]
    try:
        import tomllib

        td = tomllib.loads(wp.read_text(encoding="utf-8"))
        br = td.get("browser")
        if isinstance(br, dict):
            b = br.get("binding")
            if isinstance(b, str) and b.strip():
                out["browser_binding"] = b.strip()
    except Exception:
        pass
    return out


def _routing_entries_to_rows(entries: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(entries, list):
        return out
    for e in entries:
        if not isinstance(e, dict):
            continue
        hn = _pick(e, "hostname", "hostName")
        if hn:
            row: dict[str, Any] = {"hostname": str(hn)}
            ap = _pick(e, "apiPathPrefix", "api_path_prefix")
            if ap:
                row["api_path_prefix"] = str(ap)
            fe = _pick(e, "frontend", "Frontend")
            if isinstance(fe, dict):
                row["frontend"] = {
                    "host": str(_pick(fe, "host", "Host") or ""),
                    "port": _pick(fe, "port", "Port"),
                }
            be = _pick(e, "apiBackend", "api_backend", "ApiBackend")
            if isinstance(be, dict):
                row["api_backend"] = {
                    "host": str(_pick(be, "host", "Host") or ""),
                    "port": _pick(be, "port", "Port"),
                }
            bh = _pick(e, "backendHost", "backend_host")
            bp = _pick(e, "backendPort", "backend_port")
            if bh:
                row["backend"] = {"host": str(bh), "port": bp}
            out.append(row)
    return out


def manifest_ui_fields(manifest_path: str) -> dict[str, Any]:
    """Safe manifest excerpts for the UI (no secrets)."""
    data = _read_manifest_raw(manifest_path)
    if not data:
        return _empty_manifest_ui()
    routes: list[dict[str, Any]] = []
    routing = _pick(data, "routing", "Routing")
    if isinstance(routing, dict):
        routes.extend(_routing_entries_to_rows(routing.get("entries")))
    health_urls: list[str] = []
    hu = data.get("healthcheckUrls") or data.get("healthcheck_urls")
    if isinstance(hu, list):
        for x in hu:
            if isinstance(x, str) and x.strip():
                health_urls.append(x.strip())

    lhp = _pick(data, "localHostProfile", "local_host_profile")
    local_host_profile = str(lhp).strip() if lhp else None
    mp = Path(manifest_path)
    file_loc: dict[str, Any] = {}
    if local_host_profile:
        lp = mp.parent / local_host_profile
        if lp.is_file():
            try:
                raw_l = yaml.safe_load(lp.read_text(encoding="utf-8"))
                file_loc = raw_l if isinstance(raw_l, dict) else {}
            except (OSError, yaml.YAMLError, UnicodeDecodeError):
                file_loc = {}
    inline_loc = data.get("localhost")
    if not isinstance(inline_loc, dict):
        inline_loc = {}
    merged_loc = _merge_localhost_yaml(file_loc, inline_loc)
    infra_prof = file_loc.get("infrastructure") if isinstance(file_loc.get("infrastructure"), dict) else {}
    rt_prof = infra_prof.get("routing") if isinstance(infra_prof.get("routing"), dict) else {}
    if rt_prof:
        routes.extend(_routing_entries_to_rows(rt_prof.get("entries")))
    lc = merged_loc.get("lifecycle") if isinstance(merged_loc.get("lifecycle"), dict) else {}
    urls_out = merged_loc.get("urls") if isinstance(merged_loc.get("urls"), list) else []
    explicit_urls: list[dict[str, str]] = []
    for u in urls_out:
        if not isinstance(u, dict):
            continue
        pu = str(u.get("publicUrl") or u.get("public_url") or "").strip()
        if not pu:
            continue
        explicit_urls.append(
            {
                "role": str(u.get("role") or "other"),
                "label": str(u.get("label") or "").strip(),
                "public_url": pu,
            }
        )

    app_ver = _application_version_from_manifest(data, manifest_path)
    fp = _manifest_deploy_fingerprint(manifest_path)

    resolved_paths: dict[str, str] = {}
    try:
        resolved_paths = compute_resolved_paths_for_leco_app_manifest(data, mp, file_loc)
    except Exception:
        resolved_paths = {}

    cf_block = data.get("cloudflare") or data.get("Cloudflare")
    if not isinstance(cf_block, dict):
        cf_block = {}
    cf_infra = infra_prof.get("cloudflare") if isinstance(infra_prof.get("cloudflare"), dict) else {}
    lcp: str | None = None
    adapter_hosts: dict[str, str] | None = None
    for blk in (cf_block, cf_infra):
        raw_p = blk.get("localCfPublicPrefix") or blk.get("local_cf_public_prefix")
        if isinstance(raw_p, str) and raw_p.strip():
            lcp = raw_p.strip().lower()
            adapter_hosts = {
                "kv": f"https://{lcp}-kv.lh",
                "r2": f"https://{lcp}-r2.lh",
                "d1": f"https://{lcp}-d1.lh",
            }
            try:
                from platform_config import deployment_mode, public_url_from_lh

                if deployment_mode() == "cloud":
                    adapter_hosts = {k: public_url_from_lh(v) for k, v in adapter_hosts.items()}
            except ImportError:
                pass
            break

    dedicated_local_adapters = False
    effective_has_docker_compose = False
    route_hosts = [str(x.get("hostname") or "").strip() for x in routes if isinstance(x, dict)]
    route_hosts = [x for x in route_hosts if x]
    main_url = ""
    main_url_source = ""
    derived_main_url = ""
    main_urls: dict[str, str] = {}
    derived_main_urls: dict[str, str] = {}
    endpoint_urls: list[dict[str, str]] = []
    source_location = ""
    def _dual_scheme_urls(url: str) -> dict[str, str]:
        u = (url or "").strip()
        if not u:
            return {}
        try:
            p = urlsplit(u)
        except Exception:
            return {}
        if not p.netloc:
            return {}
        https_u = urlunsplit(("https", p.netloc, p.path, p.query, p.fragment))
        http_u = urlunsplit(("http", p.netloc, p.path, p.query, p.fragment))
        return {"https": https_u, "http": http_u}
    frontend_url = next((u["public_url"] for u in explicit_urls if u.get("role") == "frontend"), "")
    if frontend_url:
        main_url = frontend_url
        main_url_source = "localhost.urls.frontend"
    elif explicit_urls:
        main_url = explicit_urls[0]["public_url"]
        main_url_source = "localhost.urls"
    elif route_hosts:
        main_url = f"https://{route_hosts[0]}"
        main_url_source = "routing.entries"
    try:
        host_slug = host_slug_from_app_id(str(_pick(data, "name") or mp.parent.name))
        try:
            from platform_config import deployment_mode, public_hostname

            if deployment_mode() == "cloud":
                host = public_hostname("", slug=host_slug)
                derived_main_url = f"https://{host}"
                derived_main_urls = {"https": f"https://{host}", "http": f"http://{host}"}
            else:
                derived_main_url = f"https://{host_slug}.lh"
                derived_main_urls = {
                    "https": f"https://{host_slug}.lh",
                    "http": f"http://{host_slug}.lh",
                }
        except ImportError:
            derived_main_url = f"https://{host_slug}.lh"
            derived_main_urls = {"https": f"https://{host_slug}.lh", "http": f"http://{host_slug}.lh"}
        if not main_url:
            main_url = derived_main_url
            main_url_source = "derived_slug"
    except ValueError:
        derived_main_url = ""
    endpoint_urls = explicit_urls
    main_urls = _dual_scheme_urls(main_url)
    try:
        from leco_app.schema import docker_compose_is_deployable, load_effective_manifest

        em = load_effective_manifest(mp)
        if em.cloudflare:
            dedicated_local_adapters = bool(em.cloudflare.dedicated_local_adapters)
        effective_has_docker_compose = docker_compose_is_deployable(em.docker_compose)
        source_location = str(em.resolved_root(mp).resolve())
    except Exception:
        pass
    if not source_location and resolved_paths.get("sourceRoot"):
        source_location = str(resolved_paths["sourceRoot"])

    platform_binding: dict[str, Any] = {"dev_stack_id": "", "toolchain": {}}
    try:
        from dev_stack_binding import read_platform_binding

        platform_binding = read_platform_binding(manifest_path)
    except Exception:
        pass
    try:
        from platform_config import deployment_mode, lh_to_public_host

        if deployment_mode() == "cloud":
            for row in routes:
                if isinstance(row, dict) and row.get("hostname"):
                    row["hostname"] = lh_to_public_host(str(row["hostname"]))
            for row in explicit_urls:
                if isinstance(row, dict) and row.get("public_url"):
                    from platform_config import public_url_from_lh

                    row["public_url"] = public_url_from_lh(str(row["public_url"]))
    except ImportError:
        pass

    return {
        "routes": routes,
        "health_urls": health_urls,
        "local_host_profile": local_host_profile,
        "localhost_archetype": merged_loc.get("archetype"),
        "localhost_urls": urls_out,
        "localhost_lifecycle": lc,
        "application_version": app_ver,
        "deploy_fingerprint": fp,
        "local_cf": _read_local_cf_ui(manifest_path),
        "wrangler_expected": _wrangler_resource_expectations(manifest_path),
        "local_cf_public_prefix": lcp,
        "local_cf_adapter_hosts": adapter_hosts,
        "dedicated_local_adapters": dedicated_local_adapters,
        "effective_has_docker_compose": effective_has_docker_compose,
        "profile_docker_compose": _profile_docker_compose_ui(infra_prof),
        "profile_cloudflare": _profile_cloudflare_ui(infra_prof),
        "main_url": main_url,
        "main_url_source": main_url_source,
        "derived_main_url": derived_main_url,
        "main_urls": main_urls,
        "derived_main_urls": derived_main_urls,
        "endpoint_urls": endpoint_urls,
        "source_location": source_location,
        "resolved_paths": resolved_paths,
        "platform": platform_binding,
        "dev_stack_id": platform_binding.get("dev_stack_id") or "",
    }


def _container_name_from_ps_row(row: dict[str, Any]) -> str:
    for key in ("Name", "name", "ContainerName", "container_name"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            # strip optional leading project_
            return v.strip().rsplit("/", 1)[-1]
    return ""


def _service_name_from_ps_row(row: dict[str, Any]) -> str:
    for key in ("Service", "service"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "—"


def _state_from_ps_row(row: dict[str, Any]) -> str:
    return str(_pick(row, "State", "state") or "unknown").lower()


def compute_snapshot_aggregate(meta: dict[str, Any], client) -> dict[str, Any] | None:
    """Aggregate CPU/mem/net for running services in this compose project."""
    rows, code = compose_ps_result(meta)
    if code != 0:
        return None
    overview = get_docker_overview(client) if client else {}
    host = (overview or {}).get("host") or {}
    host_cpus = max(1, int(host.get("cpus") or 0))

    total_cpu = 0.0
    mem_usage = 0
    mem_limit = 0
    net_rx = 0
    net_tx = 0
    blk_r = 0
    blk_w = 0
    running_n = 0

    for row in rows:
        if _state_from_ps_row(row) != "running":
            continue
        name = _container_name_from_ps_row(row)
        if not name:
            continue
        running_n += 1
        m = get_container_metrics(client, name)
        total_cpu += float(m.get("cpu_percent") or 0)
        mem_usage += int(m.get("memory_usage") or 0)
        mem_limit += int(m.get("memory_limit") or 0)
        net_rx += int(m.get("network_rx") or 0)
        net_tx += int(m.get("network_tx") or 0)
        blk_r += int(m.get("blk_read") or 0)
        blk_w += int(m.get("blk_write") or 0)

    mem_pct_limits = (mem_usage / mem_limit) * 100 if mem_limit > 0 else 0.0
    cpu_norm = round(min(100.0, total_cpu / host_cpus), 2)

    return {
        "cpu_sum_raw": round(total_cpu, 2),
        "cpu_percent_normalized": cpu_norm,
        "memory_usage": mem_usage,
        "memory_limit_sum": mem_limit,
        "memory_percent_limits": round(mem_pct_limits, 2),
        "network_rx": net_rx,
        "network_tx": net_tx,
        "blk_read": blk_r,
        "blk_write": blk_w,
        "running_services": running_n,
        "total_services": len(rows),
        "host_cpus": host_cpus,
    }


def build_service_rows(meta: dict[str, Any], client) -> list[dict[str, Any]]:
    rows, code = compose_ps_result(meta)
    if code != 0:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        name = _container_name_from_ps_row(row)
        svc = _service_name_from_ps_row(row)
        st = _state_from_ps_row(row)
        publishers = row.get("Publishers")
        restarts = None
        exit_code = None
        if name and client:
            try:
                c = client.containers.get(name)
                restarts = int(c.attrs.get("RestartCount") or 0)
                exit_code = c.attrs.get("State", {}).get("ExitCode")
            except Exception:
                pass
        metrics: dict[str, Any] = {}
        if st == "running" and name:
            metrics = get_container_metrics(client, name)
        out.append(
            {
                "service": svc,
                "container": name or None,
                "state": st,
                "publishers": publishers if isinstance(publishers, list) else None,
                "restart_count": restarts,
                "exit_code": exit_code,
                "metrics": {
                    "cpu_percent": round(float(metrics.get("cpu_percent") or 0), 2),
                    "memory_usage": int(metrics.get("memory_usage") or 0),
                    "memory_limit": int(metrics.get("memory_limit") or 0),
                    "memory_percent": round(float(metrics.get("memory_percent") or 0), 2),
                    "network_rx": int(metrics.get("network_rx") or 0),
                    "network_tx": int(metrics.get("network_tx") or 0),
                },
            }
        )
    return out


def list_hosted_apps() -> dict[str, Any]:
    apps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in load_leco_registry_entries():
        slug = str(entry.get("id") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        meta = leco_meta_for_slug(slug)
        if not meta:
            continue
        mf = manifest_ui_fields(meta["manifest_path"])
        rt = leco_stack_runtime(meta)
        probe = {"checked": False}
        rt_status = str((rt or {}).get("status") or "").strip().lower() if isinstance(rt, dict) else ""
        if rt_status in ("running", "partial"):
            probe = _probe_main_url(str(mf.get("main_url") or ""))
        apps.append(
            {
                "id": slug,
                "label": meta["label"],
                "target_id": leco_target_id_for_slug(slug),
                "pending_registration": False,
                "runtime": rt,
                "routes": mf["routes"],
                "health_urls": mf["health_urls"],
                "main_url": mf.get("main_url") or "",
                "main_urls": mf.get("main_urls") or {},
                "main_url_probe": probe,
                "local_host_profile": mf.get("local_host_profile"),
                "localhost_archetype": mf.get("localhost_archetype"),
                "localhost_urls": mf.get("localhost_urls") or [],
                "application_version": mf.get("application_version"),
            }
        )
    for cand in materialized_hosting_apps_not_in_registry():
        slug = str(cand.get("slug") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        meta = leco_meta_for_slug(slug)
        if not meta:
            continue
        mf = manifest_ui_fields(meta["manifest_path"])
        rt = leco_stack_runtime(meta)
        probe = {"checked": False}
        rt_status = str((rt or {}).get("status") or "").strip().lower() if isinstance(rt, dict) else ""
        if rt_status in ("running", "partial"):
            probe = _probe_main_url(str(mf.get("main_url") or ""))
        apps.append(
            {
                "id": slug,
                "label": meta["label"],
                "target_id": leco_target_id_for_slug(slug),
                "pending_registration": True,
                "registration_path": f"hosting/app-available/{slug}",
                "runtime": rt,
                "routes": mf["routes"],
                "health_urls": mf["health_urls"],
                "main_url": mf.get("main_url") or "",
                "main_urls": mf.get("main_urls") or {},
                "main_url_probe": probe,
                "local_host_profile": mf.get("local_host_profile"),
                "localhost_archetype": mf.get("localhost_archetype"),
                "localhost_urls": mf.get("localhost_urls") or [],
                "application_version": mf.get("application_version"),
            }
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "token_required": bool(CONTROL_TOKEN),
        "apps": apps,
    }


def snapshot_for_slug(slug: str) -> dict[str, Any]:
    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return {"ok": False, "error": "unknown or invalid app slug"}
    client = get_docker_client()
    rt = leco_stack_runtime(meta)
    rows, code = compose_ps_result(meta)
    services = build_service_rows(meta, client) if code == 0 else []
    agg = compute_snapshot_aggregate(meta, client)
    mf = manifest_ui_fields(meta["manifest_path"])
    url_rows = mf.get("localhost_urls") or []
    urls_for_probe: list[str] = []
    for row in url_rows:
        if not isinstance(row, dict):
            continue
        candidate = row.get("public_url")
        if not candidate:
            candidate = row.get("publicUrl")
        if isinstance(candidate, str) and candidate.strip():
            urls_for_probe.append(candidate.strip())
    url_probes = _probe_url_map(urls_for_probe[:16])
    tail = meta.get("compose_tail") or []
    attached: dict[str, Any] = {"local_dev_only": True, "groups": []}
    try:
        from hosted_app_services import build_attached_services

        attached = build_attached_services(
            meta["manifest_path"],
            compose_ps=services,
            manifest_ui=mf,
            compose_tail=tail,
        )
    except Exception as exc:
        attached = {
            "local_dev_only": True,
            "groups": [],
            "error": str(exc)[:300],
        }
    data_import: dict[str, Any] = {"present": False, "items": [], "warnings": []}
    try:
        from hosted_data_import import data_import_summary_for_slug

        data_import = data_import_summary_for_slug(
            slug.strip(),
            manifest_path=meta["manifest_path"],
            compose_tail=tail,
        )
    except Exception as exc:
        data_import = {"present": False, "error": str(exc)[:300], "items": [], "warnings": []}
    return {
        "ok": True,
        "slug": slug.strip(),
        "runtime": rt,
        "compose_ps_ok": code == 0,
        "compose_docker_args": tail,
        "services": services,
        "aggregate": agg,
        "manifest_ui": mf,
        "attached_services": attached,
        "data_import": data_import,
        "url_probes": url_probes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def logs_for_slug(
    slug: str,
    *,
    tail: int = 400,
    since_seconds: int = 1800,
    service: str | None = None,
    search: str = "",
) -> dict[str, Any]:
    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return {"ok": False, "error": "unknown or invalid app slug"}
    tail = max(50, min(5000, int(tail)))
    since_seconds = max(60, min(86400, int(since_seconds)))
    cmd = ["docker", "compose", *meta["compose_tail"], "logs", "--no-color", "--tail", str(tail)]
    if since_seconds > 0:
        cmd.extend(["--since", f"{since_seconds}s"])
    if service and re.match(r"^[a-zA-Z0-9_.-]+$", service):
        cmd.append(service)
    try:
        p = subprocess.run(
            cmd,
            cwd=meta["root"],
            capture_output=True,
            text=True,
            timeout=min(120, 15 + tail // 50),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)[:200]}
    text = (p.stdout or "") + (p.stderr or "")
    lines = text.splitlines()
    if search.strip():
        needle = search.strip().lower()
        lines = [ln for ln in lines if needle in ln.lower()]
        lines = lines[-tail:]
    return {
        "ok": p.returncode == 0,
        "slug": slug.strip(),
        "returncode": p.returncode,
        "lines": len(lines),
        "log": "\n".join(lines[-5000:]),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def logs_stream_for_slug(
    slug: str,
    *,
    tail: int = 200,
    service: str | None = None,
):
    """Yield lines from `docker compose logs -f` (blocking; caller should run in a streaming response)."""
    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        yield "ERROR: unknown app slug\n"
        return
    tail = max(50, min(2000, int(tail)))
    cmd = ["docker", "compose", *meta["compose_tail"], "logs", "-f", "--tail", str(tail), "--no-color"]
    if service and re.match(r"^[a-zA-Z0-9_.-]+$", service):
        cmd.append(service)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=meta["root"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        yield f"ERROR: {exc}\n"
        return
    if not proc.stdout:
        yield "ERROR: no stdout from compose logs\n"
        return
    try:
        while True:
            line = proc.stdout.readline()
            if line:
                yield line
            elif proc.poll() is not None:
                break
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _health_probes_enabled() -> bool:
    return os.getenv(HEALTH_PROBES_ENV, "1").strip().lower() not in ("0", "false", "no")


def _probe_main_url(url: str) -> dict[str, Any]:
    candidate = str(url or "").strip()
    if not candidate:
        return {"checked": False}
    headers = {"User-Agent": "local-ecosystem-dashboard-main-url-probe/1"}
    request_url = candidate
    is_lh = False
    # Dashboard container usually cannot resolve *.lh via host DNS; probe through the
    # in-network Traefik service while preserving Host-based routing behavior.
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").strip().lower()
        if host.endswith(".lh"):
            is_lh = True
            probe_host = os.getenv("DASHBOARD_TRAEFIK_INTERNAL_HOST", "traefik").strip() or "traefik"
            netloc = probe_host
            if parts.port:
                netloc = f"{probe_host}:{parts.port}"
            request_url = urlunsplit((parts.scheme or "http", netloc, parts.path or "/", parts.query, parts.fragment))
            headers["Host"] = parts.netloc
    except Exception:
        request_url = candidate
    t0 = time.perf_counter()
    try:
        # For *.lh probes, disable redirect following: backends (e.g. FastAPI) may
        # redirect /api → /api/ with a Location header containing the unresolvable
        # *.lh hostname.  A 3xx proves the backend is reachable, which is sufficient.
        r = requests.get(
            request_url,
            timeout=4,
            verify=False,
            allow_redirects=(not is_lh),
            headers=headers,
        )
        ms = int((time.perf_counter() - t0) * 1000)
        code = int(r.status_code)
        return {
            "checked": True,
            "url": candidate,
            "ok": 200 <= code < 400,
            "status_code": code,
            "ms": ms,
        }
    except Exception as exc:
        ms = int((time.perf_counter() - t0) * 1000)
        return {
            "checked": True,
            "url": candidate,
            "ok": False,
            "status_code": None,
            "ms": ms,
            "error": str(exc)[:160],
        }


def _probe_url_map(urls: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for raw in urls:
        u = str(raw or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out[u] = _probe_main_url(u)
    return out


def insights_for_slug(slug: str) -> dict[str, Any]:
    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return {"ok": False, "error": "unknown or invalid app slug"}
    client = get_docker_client()
    mf = manifest_ui_fields(meta["manifest_path"])
    items: list[dict[str, Any]] = []
    rows, code = compose_ps_result(meta)
    total_restarts = 0
    for row in rows:
        name = _container_name_from_ps_row(row)
        if not name or not client:
            continue
        try:
            c = client.containers.get(name)
            rc = int(c.attrs.get("RestartCount") or 0)
            total_restarts += rc
            if rc > 0:
                items.append(
                    {
                        "level": "warn",
                        "title": f"Restarts · {name}",
                        "detail": f"Docker RestartCount={rc}",
                    }
                )
        except Exception:
            pass

    from hosted_app_timeseries import get_history

    hist = get_history(slug.strip(), limit=30)
    pts = hist.get("points") or []
    if len(pts) >= 5:
        recent = pts[-5:]
        older = pts[:-5] if len(pts) > 5 else pts[:1]
        cur_cpu = float((recent[-1].get("app") or {}).get("cpu_sum_raw") or 0)
        avg_old = sum(float((p.get("app") or {}).get("cpu_sum_raw") or 0) for p in older) / max(1, len(older))
        if cur_cpu > avg_old * 1.5 and cur_cpu > 10:
            items.append(
                {
                    "level": "info",
                    "title": "CPU spike vs recent average",
                    "detail": f"Last sample cpu_sum_raw≈{cur_cpu:.1f}% vs prior avg≈{avg_old:.1f}% (raw compose Σ).",
                }
            )

    probes: list[dict[str, Any]] = []
    if _health_probes_enabled() and mf.get("health_urls"):
        for url in mf["health_urls"][:8]:
            t0 = time.perf_counter()
            try:
                r = requests.get(
                    url,
                    timeout=4,
                    verify=False,
                    headers={"User-Agent": "local-ecosystem-dashboard-hosted-app-probe/1"},
                )
                ms = int((time.perf_counter() - t0) * 1000)
                ok = r.status_code < 500
                probes.append(
                    {
                        "url": url,
                        "status_code": r.status_code,
                        "ms": ms,
                        "ok": ok,
                    }
                )
                if not ok:
                    items.append(
                        {
                            "level": "warn",
                            "title": f"Health probe HTTP {r.status_code}",
                            "detail": url,
                        }
                    )
            except Exception as exc:
                ms = int((time.perf_counter() - t0) * 1000)
                probes.append({"url": url, "status_code": None, "ms": ms, "ok": False, "error": str(exc)[:120]})
                items.append(
                    {
                        "level": "warn",
                        "title": "Health probe failed",
                        "detail": f"{url} — {exc}"[:200],
                    }
                )

    if not items and code == 0 and rows:
        items.append(
            {
                "level": "ok",
                "title": "No anomalies flagged",
                "detail": f"{len(rows)} compose service(s); total RestartCount sum={total_restarts}.",
            }
        )

    return {
        "ok": True,
        "slug": slug.strip(),
        "insights": items,
        "health_probes": probes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_aggregate_for_timeseries(slug: str) -> dict[str, Any] | None:
    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return None
    client = get_docker_client()
    agg = compute_snapshot_aggregate(meta, client)
    if not agg:
        return None
    # Approximate net mbps would need prev totals; omit or add second module state — keep null in point
    rows, code = compose_ps_result(meta)
    if code == 0:
        agg["total_services"] = len(rows)
    return agg

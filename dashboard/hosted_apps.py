"""Hosted apps (leco-registry) API: list, snapshot, logs, insights."""

from __future__ import annotations

import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml

from leco_control import (
    compose_ps_result,
    leco_meta_for_slug,
    leco_stack_runtime,
    leco_target_id_for_slug,
    load_leco_registry_entries,
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


def manifest_ui_fields(manifest_path: str) -> dict[str, Any]:
    """Safe manifest excerpts for the UI (no secrets)."""
    data = _read_manifest_raw(manifest_path)
    if not data:
        return {"routes": [], "health_urls": []}
    routes: list[dict[str, Any]] = []
    routing = _pick(data, "routing", "Routing")
    if isinstance(routing, dict):
        entries = routing.get("entries") or []
        if isinstance(entries, list):
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
                    routes.append(row)
    health_urls: list[str] = []
    hu = data.get("healthcheckUrls") or data.get("healthcheck_urls")
    if isinstance(hu, list):
        for x in hu:
            if isinstance(x, str) and x.strip():
                health_urls.append(x.strip())
    return {"routes": routes, "health_urls": health_urls}


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
        apps.append(
            {
                "id": slug,
                "label": meta["label"],
                "target_id": leco_target_id_for_slug(slug),
                "runtime": rt,
                "routes": mf["routes"],
                "health_urls": mf["health_urls"],
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
    return {
        "ok": True,
        "slug": slug.strip(),
        "runtime": rt,
        "compose_ps_ok": code == 0,
        "services": services,
        "aggregate": agg,
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

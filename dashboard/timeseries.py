import threading
import time
from collections import deque
from datetime import datetime, timezone

from host_metrics import (
    host_proc_available,
    proc_metrics_source,
    read_cpu_jiffies,
    read_cpu_temp_celsius_with_source,
    read_disk_io_counters,
    read_meminfo_summary,
    read_net_bytes_total,
)
from monitor import get_container_metrics, get_docker_client, get_docker_overview

MAX_POINTS = 120
MIN_SAMPLE_INTERVAL_SEC = 2.0
_lock = threading.Lock()
_history: deque = deque(maxlen=MAX_POINTS)
_prev_totals: dict | None = None
_prev_ts: float | None = None
_last_append_epoch: float = 0.0

_prev_host_cpu: tuple | None = None
_prev_host_net: int | None = None
_prev_host_disk: dict | None = None


def aggregate_running_container_stats(client):
    """Sum stats for every running container (matches Docker Desktop / full host view)."""
    total_cpu = 0.0
    total_mem_usage = 0
    total_mem_limit = 0
    total_net_rx = 0
    total_net_tx = 0
    total_blk_read = 0
    total_blk_write = 0
    running_count = 0

    try:
        for c in client.containers.list():
            if c.status != "running":
                continue
            running_count += 1
            m = get_container_metrics(client, c)
            total_cpu += float(m.get("cpu_percent") or 0)
            total_mem_usage += int(m.get("memory_usage") or 0)
            total_mem_limit += int(m.get("memory_limit") or 0)
            total_net_rx += int(m.get("network_rx") or 0)
            total_net_tx += int(m.get("network_tx") or 0)
            total_blk_read += int(m.get("blk_read") or 0)
            total_blk_write += int(m.get("blk_write") or 0)
    except Exception:
        pass

    mem_pct_limits = (total_mem_usage / total_mem_limit) * 100 if total_mem_limit > 0 else 0.0
    return {
        "cpu_sum_raw": round(total_cpu, 2),
        "memory_usage": total_mem_usage,
        "memory_limit": total_mem_limit,
        "memory_percent_limits": round(mem_pct_limits, 2),
        "network_rx": total_net_rx,
        "network_tx": total_net_tx,
        "blk_read": total_blk_read,
        "blk_write": total_blk_write,
        "running_container_count": running_count,
    }


def _aggregate_running_containers(client):
    return aggregate_running_container_stats(client)


def compute_docker_totals_aligned(client, docker_overview, agg=None):
    """
    Same Docker CPU/RAM semantics as metrics history (all running containers, CPU ÷ host NCPU,
    RAM ÷ kernel MemTotal when /proc is readable). Use for Overview KPIs so they match Deep metrics.
    """
    if client is None:
        return None
    if agg is None:
        agg = aggregate_running_container_stats(client)
    host = (docker_overview or {}).get("host") or {}
    host_mem = int(host.get("memory_total") or 0)
    host_cpus = int(host.get("cpus") or 0)
    host_cpus_eff = max(1, host_cpus)
    cpu_sum_raw = float(agg["cpu_sum_raw"])
    cpu_percent_normalized = round(min(100.0, cpu_sum_raw / host_cpus_eff), 2)
    proc_ok = host_proc_available()
    meminfo = read_meminfo_summary() if proc_ok else None
    mem_total_bytes = int(meminfo["total_kb"] * 1024) if meminfo else host_mem
    if mem_total_bytes > 0:
        mem_pct_host = round((agg["memory_usage"] / mem_total_bytes) * 100, 2)
    else:
        mem_pct_host = None
    mem_chart = mem_pct_host if mem_pct_host is not None else agg["memory_percent_limits"]
    return {
        "cpu_percent": cpu_percent_normalized,
        "cpu_sum_raw": round(cpu_sum_raw, 2),
        "memory_percent": mem_chart,
        "memory_percent_of_host": mem_pct_host,
        "memory_percent_of_limits": agg["memory_percent_limits"],
        "memory_usage": agg["memory_usage"],
        "memory_limit_sum": agg["memory_limit"],
        "host_memory_total_bytes_effective": mem_total_bytes if mem_total_bytes > 0 else host_mem,
        "running_container_count": agg.get("running_container_count", 0),
        "host_cpus": host_cpus,
    }


def append_snapshot(client=None, docker_overview=None, precomputed_container_agg=None):
    global _prev_totals, _prev_ts, _last_append_epoch
    global _prev_host_cpu, _prev_host_net, _prev_host_disk

    if client is None:
        client = get_docker_client()
    if client is None:
        return

    now_gate = time.time()
    with _lock:
        if now_gate - _last_append_epoch < MIN_SAMPLE_INTERVAL_SEC:
            return
        _last_append_epoch = now_gate

    if docker_overview is None:
        docker_overview = get_docker_overview(client)

    now = time.time()
    agg = precomputed_container_agg if precomputed_container_agg is not None else aggregate_running_container_stats(client)
    host = (docker_overview or {}).get("host") or {}
    disk = (docker_overview or {}).get("disk") or {}

    interval = 1.0
    net_rx_bps = 0.0
    net_tx_bps = 0.0
    blk_read_bps = 0.0
    blk_write_bps = 0.0
    read_iops_est = 0.0
    write_iops_est = 0.0

    if _prev_totals is not None and _prev_ts is not None:
        interval = max(0.5, now - _prev_ts)
        drx = max(0, agg["network_rx"] - _prev_totals["network_rx"])
        dtx = max(0, agg["network_tx"] - _prev_totals["network_tx"])
        dbr = max(0, agg["blk_read"] - _prev_totals["blk_read"])
        dbw = max(0, agg["blk_write"] - _prev_totals["blk_write"])
        net_rx_bps = drx / interval
        net_tx_bps = dtx / interval
        blk_read_bps = dbr / interval
        blk_write_bps = dbw / interval
        read_iops_est = dbr / interval / 4096
        write_iops_est = dbw / interval / 4096

    _prev_totals = {
        "network_rx": agg["network_rx"],
        "network_tx": agg["network_tx"],
        "blk_read": agg["blk_read"],
        "blk_write": agg["blk_write"],
    }
    _prev_ts = now

    host_mem = int(host.get("memory_total") or 0)
    docker_disk = int(disk.get("total_tracked") or 0)
    host_cpus = int(host.get("cpus") or 0)
    host_cpus_eff = max(1, host_cpus)

    # Sum of per-container cpu_percent over-counts on multi-core hosts; normalize to ≈ % of all vCPUs.
    cpu_sum_raw = float(agg["cpu_sum_raw"])
    cpu_percent_normalized = round(min(100.0, cpu_sum_raw / host_cpus_eff), 2)

    proc_ok = host_proc_available()
    proc_src = proc_metrics_source()
    meminfo = read_meminfo_summary() if proc_ok else None
    # Prefer kernel MemTotal (bytes) so Docker % matches the same baseline as /proc mem lines.
    mem_total_bytes = int(meminfo["total_kb"] * 1024) if meminfo else host_mem
    if mem_total_bytes > 0:
        mem_pct_host = round((agg["memory_usage"] / mem_total_bytes) * 100, 2)
    else:
        mem_pct_host = None

    docker_net_total_mbps = round(((net_rx_bps + net_tx_bps) * 8) / 1e6, 4)
    docker_blk_total_mbps = round(((blk_read_bps + blk_write_bps) * 8) / 1e6, 4)
    docker_iops_total = round(read_iops_est + write_iops_est, 2)

    sys_cpu = None
    sys_mem = None
    sys_mem_avail_pct = None
    sys_net_mbps = None
    sys_iops_total = None
    sys_temp_c, sys_temp_src = read_cpu_temp_celsius_with_source()

    if meminfo:
        sys_mem = meminfo["used_percent"]
        sys_mem_avail_pct = meminfo["available_percent"]

    if proc_ok:
        cur_cpu = read_cpu_jiffies()
        if cur_cpu and _prev_host_cpu is not None:
            di = cur_cpu[0] - _prev_host_cpu[0]
            dt = cur_cpu[1] - _prev_host_cpu[1]
            if dt > 0:
                sys_cpu = round(100.0 * (1.0 - (di / dt)), 2)
        if cur_cpu is not None:
            _prev_host_cpu = cur_cpu

        nb = read_net_bytes_total()
        if nb is not None:
            if _prev_host_net is not None and interval > 0:
                dnb = max(0, nb - _prev_host_net)
                sys_net_mbps = round((dnb * 8) / interval / 1e6, 4)
            _prev_host_net = nb

        dsk = read_disk_io_counters()
        if dsk is not None:
            if _prev_host_disk is not None and interval > 0:
                dr = max(0, dsk["reads_completed"] - _prev_host_disk["reads_completed"])
                dw = max(0, dsk["writes_completed"] - _prev_host_disk["writes_completed"])
                sys_iops_total = round((dr + dw) / interval, 2)
            _prev_host_disk = dsk

    point = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "epoch": now,
        "docker": {
            "cpu_percent": cpu_percent_normalized,
            "cpu_sum_raw": agg["cpu_sum_raw"],
            "memory_percent": mem_pct_host if mem_pct_host is not None else agg["memory_percent_limits"],
            "memory_percent_of_host": mem_pct_host,
            "memory_percent_of_limits": agg["memory_percent_limits"],
            "memory_usage": agg["memory_usage"],
            "memory_limit_sum": agg["memory_limit"],
            "net_rx_mbps": round((net_rx_bps * 8) / 1e6, 4),
            "net_tx_mbps": round((net_tx_bps * 8) / 1e6, 4),
            "net_total_mbps": docker_net_total_mbps,
            "blk_read_mbps": round((blk_read_bps * 8) / 1e6, 4),
            "blk_write_mbps": round((blk_write_bps * 8) / 1e6, 4),
            "read_iops_est": round(read_iops_est, 2),
            "write_iops_est": round(write_iops_est, 2),
            "iops_total_est": docker_iops_total,
            "blk_total_mbps": docker_blk_total_mbps,
        },
        "system": {
            "host_memory_total": host_mem,
            "host_memory_total_bytes_effective": mem_total_bytes if mem_total_bytes > 0 else host_mem,
            "host_cpus": host_cpus,
            "docker_disk_tracked": docker_disk,
            "docker_mem_pct_of_host": mem_pct_host,
            "cpu_percent": sys_cpu,
            "memory_percent": sys_mem,
            "memory_percent_available": sys_mem_avail_pct,
            "net_total_mbps": sys_net_mbps,
            "iops_total_est": sys_iops_total,
            "cpu_temp_c_max": sys_temp_c,
            "cpu_temp_source": sys_temp_src,
            "host_proc_available": proc_ok,
            "proc_metrics_source": proc_src,
        },
    }

    with _lock:
        _history.append(point)


def get_history(limit: int | None = None):
    with _lock:
        items = list(_history)
    if limit is not None:
        items = items[-max(1, int(limit)) :]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_points": MAX_POINTS,
        "points": items,
        "notes": (
            "system_* CPU/RAM/Net/IOPS read from DASHBOARD_HOST_PROC when mounted, else the container's /proc "
            "(Docker Desktop: Linux VM stats, not macOS host). "
            "Docker RAM % uses the same MemTotal as /proc/meminfo when available. "
            "RAM chart magenta series is MemAvailable/MemTotal (headroom); cyan is Docker Σ usage / MemTotal — "
            "they are different metrics and should not track 1:1. "
            "Docker IOPS are often 0 on Docker Desktop (no blkio counters). "
            "CPU temperature: DASHBOARD_HOST_CPU_TEMP_FILE (macOS host file; dashboard.sh installs LaunchAgent writer), "
            "else DASHBOARD_HOST_SYS / container /sys/class/thermal."
        ),
    }

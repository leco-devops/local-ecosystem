"""
Host-level metrics from /proc.

Preferred: mounted host /proc at DASHBOARD_HOST_PROC (e.g. -v /proc:/host/proc:ro on Linux bare metal).

Fallback: the container's own /proc (always present in Linux containers). On Docker Desktop that is
the Linux VM's stats — still useful so magenta "System" series render; use host mount for true
bare-metal /proc when running on Linux.

CPU temperature:
- Linux: sysfs thermal zones (DASHBOARD_HOST_SYS or /sys/class/thermal).
- macOS (Docker Desktop): the Linux container cannot see Apple SMC. Set DASHBOARD_HOST_CPU_TEMP_FILE
  to a mounted file that macOS updates (e.g. osx-cpu-temp); see ai-stack/scripts/macos-write-cpu-temp.sh.
"""

import json
import os
import re
from pathlib import Path

_SYS_ROOT = os.getenv("DASHBOARD_HOST_SYS", "").strip()
_HOST_CPU_TEMP_FILE = os.getenv("DASHBOARD_HOST_CPU_TEMP_FILE", "").strip()


def _first_float_in_text(s: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", (s or "").strip())
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _parse_host_cpu_temp_file() -> tuple[float | None, str | None]:
    """
    Read DASHBOARD_HOST_CPU_TEMP_FILE. Returns (celsius, api_source) where api_source is
    host_file, host_file_thermal_proxy, or None.
    """
    if not _HOST_CPU_TEMP_FILE:
        return None, None
    path = Path(_HOST_CPU_TEMP_FILE)
    if not path.is_file():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None, None
    if not raw or raw.startswith("#"):
        return None, None
    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None, None
        v = data.get("cpu_temp_c", data.get("temp_c"))
        if v is None:
            return None, None
        try:
            c = round(float(v), 1)
        except (TypeError, ValueError):
            return None, None
        src = (data.get("source") or "").strip()
        if src == "thermal_pressure":
            return c, "host_file_thermal_proxy"
        return c, "host_file"
    line = raw.splitlines()[0].strip()
    if line.startswith("#"):
        return None, None
    v = _first_float_in_text(line)
    if v is None:
        return None, None
    if v > 200 or v < -50:
        return None, None
    # Legacy plain number: treat as host file (die-style); reject obvious bogus zeros.
    if v <= 0 or v < 15:
        return None, None
    return round(v, 1), "host_file"


def _parse_host_cpu_temp_json_metadata() -> dict | None:
    """If the injected temp file is JSON, return extra fields for the UI (else None)."""
    if not _HOST_CPU_TEMP_FILE:
        return None
    path = Path(_HOST_CPU_TEMP_FILE)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not raw or not raw.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "file_source": (data.get("source") or "").strip() or None,
        "thermal_pressure": (data.get("thermal_pressure") or "").strip() or None,
        "proxy_model": (data.get("proxy_model") or "").strip() or None,
        "proxy_baseline_c": data.get("proxy_baseline_c"),
        "proxy_load_ratio": data.get("proxy_load_ratio"),
        "proxy_load_add_c": data.get("proxy_load_add_c"),
    }


def read_writer_status_dict() -> dict | None:
    """Sidecar JSON from macos-write-cpu-temp.sh (same directory as cpu temp file)."""
    if not _HOST_CPU_TEMP_FILE:
        return None
    p = Path(_HOST_CPU_TEMP_FILE).parent / "writer_status.json"
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore").strip()
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def read_scheduler_meta_dict() -> dict | None:
    """Written by macos-host-metrics-scheduler.sh install (LaunchAgent metadata)."""
    if not _HOST_CPU_TEMP_FILE:
        return None
    p = Path(_HOST_CPU_TEMP_FILE).parent / "scheduler_meta.json"
    if not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore").strip()
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def build_host_temp_insights(
    temp_c: float | None,
    temp_source: str | None,
    meta: dict | None,
    writer: dict | None,
    scheduler: dict | None,
) -> list[str]:
    out: list[str] = []
    if not _HOST_CPU_TEMP_FILE:
        out.append("No DASHBOARD_HOST_CPU_TEMP_FILE — macOS host temp is disabled for this container.")
        return out
    path = Path(_HOST_CPU_TEMP_FILE)
    out.append(f"Host file mount: {path} (exists: {path.is_file()}).")
    if temp_c is None:
        out.append("No readable temperature in the file yet — run the host writer or wait for the LaunchAgent.")
    elif temp_source == "host_file_thermal_proxy":
        out.append(
            "Value is a thermal-pressure proxy (not die temperature). The bucket (Nominal/Fair/…) sets a "
            "baseline; load average can add extra °C so the chart responds to CPU load even in Nominal."
        )
    else:
        out.append("Temperature is read from the host-written file (die-style or tool output).")
    if meta and meta.get("thermal_pressure"):
        out.append(f"Last file payload: Apple thermal pressure = {meta['thermal_pressure']}.")
    if writer:
        if writer.get("success") is False:
            out.append(f"Last writer run failed: {writer.get('message') or 'unknown error'}.")
        elif writer.get("updated_at"):
            out.append(f"Host writer last reported: {writer.get('updated_at')}.")
    if scheduler:
        out.append(
            f"LaunchAgent metadata on disk: {scheduler.get('label')} every {scheduler.get('interval_sec')}s "
            f"(install recorded {scheduler.get('installed_at', '?')})."
        )
    else:
        out.append(
            "No scheduler_meta.json — if you use macOS, run dashboard deploy so "
            "macos-host-metrics-scheduler.sh install runs, or schedule the writer yourself."
        )
    out.append(
        "Manual host refresh: on your Mac, run: bash ai-stack/scripts/macos-write-cpu-temp.sh "
        "(repo path must match your clone)."
    )
    return out


def host_injected_metrics_api_payload() -> dict:
    """For GET /api/host-metrics/injected — dashboard UI for macOS writer + temp file."""
    temp_c, temp_src = _parse_host_cpu_temp_file()
    meta = _parse_host_cpu_temp_json_metadata()
    writer = read_writer_status_dict()
    scheduler = read_scheduler_meta_dict()
    path = Path(_HOST_CPU_TEMP_FILE) if _HOST_CPU_TEMP_FILE else None
    insights = build_host_temp_insights(temp_c, temp_src, meta, writer, scheduler)
    return {
        "configured": bool(_HOST_CPU_TEMP_FILE),
        "path": _HOST_CPU_TEMP_FILE or None,
        "file_exists": path.is_file() if path else False,
        "cpu_temp_c": temp_c,
        "cpu_temp_source": temp_src,
        "file_metadata": meta,
        "writer_status": writer,
        "scheduler_meta": scheduler,
        "insights": insights,
    }


def read_host_injected_cpu_temp_celsius() -> float | None:
    """
    Optional host-written CPU temperature (°C) for macOS / Docker Desktop.
    File path from DASHBOARD_HOST_CPU_TEMP_FILE (mounted into the container).

    Accepted formats:
    - Single line: 45.2 or 45.2°C (legacy; values < 15 or ≤ 0 ignored)
    - JSON: {"cpu_temp_c": 45.2, "source": "osx_cpu_temp"|"smc_die"|"thermal_pressure", ...}
    """
    c, _ = _parse_host_cpu_temp_file()
    return c


def _read_thermal_sysfs_max_celsius() -> float | None:
    roots = []
    if _SYS_ROOT:
        roots.append(Path(_SYS_ROOT))
    roots.append(Path("/sys"))

    max_mc = None
    for sys_root in roots:
        thermal = sys_root / "class" / "thermal"
        if not thermal.is_dir():
            continue
        try:
            for z in sorted(thermal.glob("thermal_zone*/temp")):
                try:
                    raw = z.read_text(encoding="utf-8", errors="ignore").strip()
                    val = int(raw)
                except (OSError, ValueError):
                    continue
                if val <= 0:
                    continue
                max_mc = val if max_mc is None else max(max_mc, val)
        except OSError:
            continue

    if max_mc is None:
        return None
    return round(max_mc / 1000.0, 1)


def read_cpu_temp_celsius_with_source() -> tuple[float | None, str | None]:
    """Returns (temperature °C, source id) for metrics payloads."""
    inj, inj_src = _parse_host_cpu_temp_file()
    if inj is not None and inj_src is not None:
        return inj, inj_src
    sysfs = _read_thermal_sysfs_max_celsius()
    if sysfs is not None:
        return sysfs, "sysfs"
    return None, None


def _explicit_proc_root() -> Path:
    return Path(os.getenv("DASHBOARD_HOST_PROC", "/host/proc").strip() or "/host/proc")


def proc_root() -> Path:
    """Readable /proc tree: host mount if present, else container /proc."""
    exp = _explicit_proc_root()
    try:
        st = exp / "stat"
        if st.is_file() and os.access(st, os.R_OK):
            return exp
    except OSError:
        pass
    return Path("/proc")


def proc_metrics_source() -> str:
    """host_mount | container_proc | none"""
    exp = _explicit_proc_root()
    try:
        if (exp / "stat").is_file() and os.access(exp / "stat", os.R_OK):
            return "host_mount"
    except OSError:
        pass
    try:
        if Path("/proc/stat").is_file():
            return "container_proc"
    except OSError:
        pass
    return "none"


def _proc_path(*parts: str) -> Path:
    return proc_root().joinpath(*parts)


def host_proc_available() -> bool:
    p = _proc_path("stat")
    try:
        return p.is_file() and os.access(p, os.R_OK)
    except OSError:
        return False


def read_cpu_jiffies():
    """Returns (idle jiffies, total jiffies) from first cpu line, or None."""
    path = _proc_path("stat")
    if not path.is_file():
        return None
    try:
        line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not line.startswith("cpu "):
        return None
    parts = line.split()
    try:
        nums = [int(x) for x in parts[1:]]
    except ValueError:
        return None
    if len(nums) < 4:
        return None
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    total = sum(nums)
    return idle, total


def read_meminfo_summary() -> dict | None:
    """
    Single parse of /proc/meminfo. Returns:
      total_kb, available_kb, used_percent (MemTotal-MemAvailable)/MemTotal,
      available_percent MemAvailable/MemTotal.
    Docker Σ / MemTotal should use total_kb * 1024 as denominator when possible so it matches
    the same MemTotal as the kernel (avoids tiny drift vs docker info).
    """
    path = _proc_path("meminfo")
    if not path.is_file():
        return None
    total_kb = None
    avail_kb = None
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                avail_kb = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    if not total_kb or total_kb <= 0 or avail_kb is None:
        return None
    used = total_kb - avail_kb
    return {
        "total_kb": total_kb,
        "available_kb": avail_kb,
        "used_percent": round(100.0 * used / total_kb, 2),
        "available_percent": round(100.0 * avail_kb / total_kb, 2),
    }


def read_mem_percent_used():
    """RAM % used (MemTotal - MemAvailable) / MemTotal, or None."""
    mi = read_meminfo_summary()
    return None if mi is None else mi["used_percent"]


def read_net_bytes_total():
    """Sum rx+tx bytes across non-loopback interfaces."""
    path = _proc_path("net", "dev")
    if not path.is_file():
        return None
    rx = 0
    tx = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[2:]:
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            parts = rest.split()
            if len(parts) < 16:
                continue
            try:
                rx += int(parts[0])
                tx += int(parts[8])
            except ValueError:
                continue
    except OSError:
        return None
    return rx + tx


def read_disk_io_counters():
    """
    Sum read/write completed I/Os and sectors for physical-ish devices.
    Returns dict with reads_completed, writes_completed, sectors_read, sectors_written or None.
    """
    path = _proc_path("diskstats")
    if not path.is_file():
        return None
    reads = writes = sec_r = sec_w = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            try:
                major = int(parts[0])
                name = parts[2]
            except (ValueError, IndexError):
                continue
            if major in (7,):  # loop
                continue
            if name.startswith("loop") or name.startswith("ram"):
                continue
            try:
                reads += int(parts[3])
                sec_r += int(parts[5])
                writes += int(parts[7])
                sec_w += int(parts[9])
            except (ValueError, IndexError):
                continue
    except OSError:
        return None
    return {
        "reads_completed": reads,
        "writes_completed": writes,
        "sectors_read": sec_r,
        "sectors_written": sec_w,
    }


def read_thermal_max_celsius() -> float | None:
    """Prefer host-injected file (macOS), else max sysfs thermal zone (Linux)."""
    t, _ = read_cpu_temp_celsius_with_source()
    return t

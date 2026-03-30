"""
Host-level metrics from a mounted /proc (e.g. -v /proc:/host/proc:ro on Linux).
Optional CPU thermal: mount host /sys read-only and set DASHBOARD_HOST_SYS=/host/sys (Linux).
On Docker Desktop / Mac without mounts, proc/sys readers return None.
"""

import os
from pathlib import Path

PROC_ROOT = os.getenv("DASHBOARD_HOST_PROC", "/host/proc").strip() or "/host/proc"
SYS_ROOT = os.getenv("DASHBOARD_HOST_SYS", "").strip()


def _proc_path(*parts: str) -> Path:
    return Path(PROC_ROOT, *parts)


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


def read_mem_percent_used():
    """RAM % used from MemTotal / MemAvailable, or None."""
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
    return round(100.0 * used / total_kb, 2)


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


def read_thermal_max_celsius():
    """
    Highest temperature from sysfs thermal zones (millidegree C files), or None.
    Order: DASHBOARD_HOST_SYS (host mount, Linux), then container /sys (VM/bare-metal
    kernels often expose thermal zones there — useful when host /sys is not mounted).
    """
    roots = []
    if SYS_ROOT:
        roots.append(Path(SYS_ROOT))
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
                    v = int(raw)
                except (OSError, ValueError):
                    continue
                if v <= 0:
                    continue
                max_mc = v if max_mc is None else max(max_mc, v)
        except OSError:
            continue

    if max_mc is None:
        return None
    return round(max_mc / 1000.0, 1)

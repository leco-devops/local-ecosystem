"""In-memory time series per registered hosted-app (leco) slug for Hosted apps tab charts."""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

MAX_POINTS = 120
MIN_SAMPLE_INTERVAL_SEC = 3.0
_lock = threading.Lock()
_histories: dict[str, deque[dict[str, Any]]] = {}
_last_sample_epoch: dict[str, float] = {}
_global_gate = 0.0
_prev_net: dict[str, tuple[int, int, float]] = {}


def _deque_for(slug: str) -> deque[dict[str, Any]]:
    if slug not in _histories:
        _histories[slug] = deque(maxlen=MAX_POINTS)
    return _histories[slug]


def append_point(slug: str, point: dict[str, Any]) -> None:
    with _lock:
        _deque_for(slug).append(point)


def get_history(slug: str, limit: int | None = None) -> dict[str, Any]:
    with _lock:
        items = list(_deque_for(slug))
    if limit is not None:
        items = items[-max(1, int(limit)) :]
    return {
        "slug": slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "max_points": MAX_POINTS,
        "points": items,
    }


def maybe_append_from_aggregate(slug: str, aggregate: dict[str, Any] | None) -> None:
    """Record one sample if interval elapsed (per-slug) and aggregate is present."""
    if not aggregate:
        return
    now = time.time()
    with _lock:
        last = _last_sample_epoch.get(slug, 0.0)
        if now - last < MIN_SAMPLE_INTERVAL_SEC:
            return
        _last_sample_epoch[slug] = now
        rx = int(aggregate.get("network_rx") or 0)
        tx = int(aggregate.get("network_tx") or 0)
        net_rx_mbps = None
        net_tx_mbps = None
        prev = _prev_net.get(slug)
        if prev is not None:
            prx, ptx, pt = prev
            dt = max(0.5, now - pt)
            net_rx_mbps = round(((max(0, rx - prx) * 8) / dt) / 1e6, 4)
            net_tx_mbps = round(((max(0, tx - ptx) * 8) / dt) / 1e6, 4)
        _prev_net[slug] = (rx, tx, now)
        dq = _deque_for(slug)
        dq.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "epoch": now,
                "app": {
                    "cpu_sum_raw": aggregate.get("cpu_sum_raw"),
                    "cpu_percent": aggregate.get("cpu_percent_normalized"),
                    "memory_usage": aggregate.get("memory_usage"),
                    "memory_limit_sum": aggregate.get("memory_limit_sum"),
                    "memory_percent_limits": aggregate.get("memory_percent_limits"),
                    "net_rx_mbps": net_rx_mbps,
                    "net_tx_mbps": net_tx_mbps,
                    "running_services": aggregate.get("running_services"),
                    "total_services": aggregate.get("total_services"),
                },
            }
        )


def sample_all_registered(build_aggregate_fn) -> None:
    """Call build_aggregate_fn(slug) -> dict|None for each registry id (throttled globally)."""
    global _global_gate
    from leco_control import load_leco_registry_entries

    now = time.time()
    with _lock:
        if now - _global_gate < MIN_SAMPLE_INTERVAL_SEC:
            return
        _global_gate = now

    seen: set[str] = set()
    for entry in load_leco_registry_entries():
        slug = str(entry.get("id") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        try:
            agg = build_aggregate_fn(slug)
        except Exception:
            agg = None
        if agg:
            maybe_append_from_aggregate(slug, agg)

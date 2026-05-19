"""Dev stack URL repair and compose log formatting."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from dev_stack_app_urls import format_compose_log  # noqa: E402


def test_format_compose_log_splits_carriage_returns():
    raw = "Network foo Creating\rNetwork foo Created\rContainer bar Starting\rContainer bar Started"
    out = format_compose_log(raw)
    assert out.splitlines() == [
        "Network foo Creating",
        "Network foo Created",
        "Container bar Starting",
        "Container bar Started",
    ]


def test_format_compose_log_collapses_blank_lines():
    assert format_compose_log("  line one  \n\n\n  line two  ") == "line one\nline two"

"""Service dependency ordering for Control actions."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

from control_targets import COMPOSE_SERVICE_REQUIRES, compose_action_services  # noqa: E402


def test_stop_n8n_stops_postgres_order():
    order = compose_action_services("n8n", "stop", {"n8n": ("postgres",)})
    assert order == ["n8n", "postgres"]


def test_stop_postgres_stops_n8n_first():
    order = compose_action_services("postgres", "stop", {"n8n": ("postgres",)})
    assert order == ["n8n", "postgres"]


def test_start_varnish_starts_nginx_first():
    order = compose_action_services("cache-varnish", "start", COMPOSE_SERVICE_REQUIRES)
    assert order == ["cache-nginx", "cache-varnish"]


def test_stop_nginx_stops_varnish_first():
    order = compose_action_services("cache-nginx", "stop", COMPOSE_SERVICE_REQUIRES)
    assert order == ["cache-varnish", "cache-nginx"]

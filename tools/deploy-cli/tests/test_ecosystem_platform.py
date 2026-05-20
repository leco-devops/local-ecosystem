"""Tests for platform/dev-stack CLI helpers."""

from __future__ import annotations

import pytest

from leco_app.ecosystem_platform import parse_component_specs


def test_parse_component_specs_ok() -> None:
    assert parse_component_specs(["postgres:16", "redis:7"]) == [
        {"id": "postgres", "version": "16"},
        {"id": "redis", "version": "7"},
    ]


def test_parse_component_specs_requires_version() -> None:
    with pytest.raises(ValueError, match="id:version"):
        parse_component_specs(["postgres"])

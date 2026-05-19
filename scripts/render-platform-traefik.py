#!/usr/bin/env python3
"""Render hosting/traefik/01-stack-core.yml from traefik/dynamic.yml + leco-platform.yaml."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ecosystem-stack" / "lib"))

import yaml  # noqa: E402

from platform_config import PLATFORM_FILE, load_platform_config  # noqa: E402

SOURCE = ROOT / "traefik" / "dynamic.yml"
OUT = ROOT / "hosting" / "traefik" / "01-stack-core.yml"


def render(base_domain: str) -> str:
    text = SOURCE.read_text(encoding="utf-8")
    if base_domain == "lh":
        return text
    # Host(`service.lh`) -> Host(`service.example.com`)
    text = re.sub(
        r"Host\(`([a-zA-Z0-9*-]+)\.lh`\)",
        lambda m: f"Host(`{m.group(1)}.{base_domain}`)",
        text,
    )
  # Path rules that mention .lh in comments only — left as-is
    return text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Write hosting/traefik/01-stack-core.yml")
    args = parser.parse_args()
    cfg = load_platform_config()
    dom = str((cfg or {}).get("base_domain") or "lh").strip() or "lh"
    body = render(dom)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(body, encoding="utf-8")
        print(f"Wrote {OUT} (base_domain={dom})")
    else:
        print(body[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

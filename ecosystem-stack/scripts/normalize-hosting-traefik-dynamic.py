#!/usr/bin/env python3
"""Repair hosting/traefik/dynamic.yml so Traefik v3 accepts it (e.g. drop empty http: {})."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEPLOY_CLI = _REPO_ROOT / "tools" / "deploy-cli"
if str(_DEPLOY_CLI) not in sys.path:
    sys.path.insert(0, str(_DEPLOY_CLI))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: normalize-hosting-traefik-dynamic.py <path-to-dynamic.yml>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        return 0

    try:
        import yaml
    except ImportError:
        print("normalize-hosting-traefik-dynamic: PyYAML not installed (exit 3)", file=sys.stderr)
        return 3

    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
    except Exception as e:
        print(f"normalize-hosting-traefik-dynamic: skip invalid YAML {path}: {e}", file=sys.stderr)
        return 0

    if data is None:
        data = {}
    if not isinstance(data, dict):
        print(f"normalize-hosting-traefik-dynamic: skip non-mapping root in {path}", file=sys.stderr)
        return 0

    from leco_app.traefik_dynamic_sanitize import prune_empty_http_maps

    before = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    prune_empty_http_maps(data)
    after = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)

    if before == after:
        return 0

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(after, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        print(f"normalize-hosting-traefik-dynamic: write failed: {e}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return 1

    print(f"normalize-hosting-traefik-dynamic: updated {path} (Traefik v3–safe empty http prune)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

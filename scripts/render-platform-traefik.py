#!/usr/bin/env python3
"""Render hosting/traefik/01-stack-core.yml from traefik/dynamic.yml + leco-platform.yaml.

Local (``base_domain: lh``) is a byte-for-byte copy of ``traefik/dynamic.yml`` — comments and
ordering included. Only a real domain triggers a rewrite, which does three things:

1. ``Host(`svc.lh`)`` → ``Host(`svc.<base_domain>`)``.
2. With ``tls.mode: acme``, names the certificate resolver on every TLS router. Traefik does not
   request a certificate for a router that merely says ``tls: true``; without an explicit
   ``certResolver`` it serves its built-in self-signed cert and the ACME resolver defined in
   ``traefik/traefik-static-acme.yaml`` is never used. The name is read back out of that file so
   the two cannot drift apart.
3. Drops the ``tls.certificates`` block when the platform is not on mkcert. It points at
   ``/certs/wildcard.lh.pem``, which a cloud install never generates, and Traefik logs a hard
   error for a missing certificate file on every config reload.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ecosystem-stack" / "lib"))

import yaml  # noqa: E402

from platform_config import PLATFORM_FILE, load_platform_config  # noqa: E402

SOURCE = ROOT / "traefik" / "dynamic.yml"
OUT = ROOT / "hosting" / "traefik" / "01-stack-core.yml"
ACME_STATIC = ROOT / "traefik" / "traefik-static-acme.yaml"

# Used only if traefik-static-acme.yaml is unreadable; must stay in sync with that file.
FALLBACK_RESOLVER = "lecoacme"


def acme_resolver_name(static_file: Path = ACME_STATIC) -> str:
    """First resolver declared in the ACME static config — the name routers must reference."""
    try:
        raw = yaml.safe_load(static_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return FALLBACK_RESOLVER
    if not isinstance(raw, dict):
        return FALLBACK_RESOLVER
    resolvers = raw.get("certificatesResolvers") or raw.get("certificatesresolvers")
    if isinstance(resolvers, dict):
        for name in resolvers:
            if str(name).strip():
                return str(name).strip()
    return FALLBACK_RESOLVER


def rewrite_hosts(text: str, base_domain: str) -> str:
    """``Host(`a.lh`)`` and ``Host(`panel.a.lh`)`` → the same labels under ``base_domain``."""
    return re.sub(
        r"Host\(`([a-zA-Z0-9*][a-zA-Z0-9.*-]*)\.lh`\)",
        lambda m: f"Host(`{m.group(1)}.{base_domain}`)",
        text,
    )


def apply_tls_policy(text: str, *, resolver: str | None, drop_local_certificates: bool) -> str:
    """
    Set ``tls.certResolver`` on TLS routers and/or drop the mkcert ``tls.certificates`` block.

    Re-serializes the document, so comments in the generated runtime file are lost. That only
    happens off ``lh``; the local path never reaches here.
    """
    if resolver is None and not drop_local_certificates:
        return text
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(f"traefik/dynamic.yml is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise SystemExit("traefik/dynamic.yml did not parse to a mapping")

    if resolver:
        routers = ((doc.get("http") or {}) if isinstance(doc.get("http"), dict) else {}).get("routers")
        if isinstance(routers, dict):
            for _name, router in routers.items():
                if not isinstance(router, dict):
                    continue
                tls: Any = router.get("tls")
                if tls is True:
                    router["tls"] = {"certResolver": resolver}
                elif isinstance(tls, dict):
                    # An explicit resolver already chosen by hand wins.
                    tls.setdefault("certResolver", resolver)

    if drop_local_certificates:
        tls_block = doc.get("tls")
        if isinstance(tls_block, dict):
            tls_block.pop("certificates", None)
            if not tls_block:
                doc.pop("tls", None)

    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, allow_unicode=True)


def render(base_domain: str, tls_mode: str = "mkcert") -> str:
    text = SOURCE.read_text(encoding="utf-8")
    if base_domain == "lh":
        # Local install: unchanged, comments and all.
        return text
    text = rewrite_hosts(text, base_domain)
    return apply_tls_policy(
        text,
        resolver=acme_resolver_name() if tls_mode == "acme" else None,
        drop_local_certificates=tls_mode != "mkcert",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="Write hosting/traefik/01-stack-core.yml")
    args = parser.parse_args()
    cfg = load_platform_config() or {}
    dom = str(cfg.get("base_domain") or "lh").strip() or "lh"
    tls_cfg = cfg.get("tls") if isinstance(cfg.get("tls"), dict) else {}
    tls_mode = str(tls_cfg.get("mode") or "mkcert").strip() or "mkcert"
    body = render(dom, tls_mode)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(body, encoding="utf-8")
        print(f"Wrote {OUT} (base_domain={dom}, tls.mode={tls_mode}, config={PLATFORM_FILE})")
        if dom != "lh" and tls_mode == "acme":
            print(f"  routers now reference certResolver: {acme_resolver_name()}")
    else:
        print(body[:500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Validate manifest / localhost YAML for the registration wizard (LEco DevOps schema)."""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from leco_app.schema import ApplicationManifest, LocalhostProfile


def _fmt_pydantic_errors(exc: ValidationError) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        path = ".".join(str(x) for x in loc) if loc else "(root)"
        out.append(
            {
                "path": path,
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
        )
    return out


def _validate_manifest_blob(raw: str | None) -> dict[str, Any]:
    blob = (raw or "").strip()
    if not blob:
        return {
            "stage": "empty",
            "ok": True,
            "message": "Empty — register will generate from Detect or defaults when omitted.",
            "pydantic_errors": None,
            "preview": None,
        }
    try:
        data = yaml.safe_load(blob)
    except yaml.YAMLError as exc:
        return {
            "stage": "parse_error",
            "ok": False,
            "message": f"YAML parse error: {exc}",
            "pydantic_errors": None,
            "preview": None,
        }
    if not isinstance(data, dict):
        return {
            "stage": "parse_error",
            "ok": False,
            "message": "Top level must be a YAML mapping (object), not a list or scalar.",
            "pydantic_errors": None,
            "preview": None,
        }
    try:
        man = ApplicationManifest.model_validate(data)
    except ValidationError as exc:
        return {
            "stage": "schema_invalid",
            "ok": False,
            "message": "Manifest does not match leco.app.yaml schema.",
            "pydantic_errors": _fmt_pydantic_errors(exc),
            "preview": None,
        }
    return {
        "stage": "ok",
        "ok": True,
        "message": "Manifest matches ApplicationManifest schema.",
        "pydantic_errors": None,
        "preview": {"name": man.name, "root": man.root},
    }


def _validate_localhost_blob(raw: str | None) -> dict[str, Any]:
    blob = (raw or "").strip()
    if not blob:
        return {
            "stage": "empty",
            "ok": True,
            "message": "Empty — optional sidecar; register may write defaults.",
            "pydantic_errors": None,
            "preview": None,
        }
    try:
        data = yaml.safe_load(blob)
    except yaml.YAMLError as exc:
        return {
            "stage": "parse_error",
            "ok": False,
            "message": f"YAML parse error: {exc}",
            "pydantic_errors": None,
            "preview": None,
        }
    if not isinstance(data, dict):
        return {
            "stage": "parse_error",
            "ok": False,
            "message": "Top level must be a YAML mapping (object).",
            "pydantic_errors": None,
            "preview": None,
        }
    try:
        loc = LocalhostProfile.model_validate(data)
    except ValidationError as exc:
        return {
            "stage": "schema_invalid",
            "ok": False,
            "message": "localhost / leco.yaml does not match LocalhostProfile schema.",
            "pydantic_errors": _fmt_pydantic_errors(exc),
            "preview": None,
        }
    return {
        "stage": "ok",
        "ok": True,
        "message": "Sidecar matches LocalhostProfile schema.",
        "pydantic_errors": None,
        "preview": {"archetype": loc.archetype, "schema_version": loc.schema_version},
    }


def validate_registration_yaml(
    manifest_yaml: str | None,
    localhost_yaml: str | None,
) -> dict[str, Any]:
    """
    Return a structured report for the dashboard. ``validation_ok`` is True only when every
    non-empty blob parses and passes Pydantic validation (empty blobs are allowed).
    """
    m = _validate_manifest_blob(manifest_yaml)
    l = _validate_localhost_blob(localhost_yaml)
    validation_ok = bool(m["ok"] and l["ok"])
    lines: list[str] = []
    lines.append(f"Manifest (leco.app.yaml): {m['message']}")
    if m.get("preview"):
        lines.append(f"  → name={m['preview'].get('name')!r}, root={m['preview'].get('root')!r}")
    if m.get("pydantic_errors"):
        for e in m["pydantic_errors"]:
            lines.append(f"  · {e['path']}: {e['message']}")
    lines.append(f"localhost (leco.yaml): {l['message']}")
    if l.get("preview"):
        lines.append(
            f"  → archetype={l['preview'].get('archetype')!r}, schema_version={l['preview'].get('schema_version')}"
        )
    if l.get("pydantic_errors"):
        for e in l["pydantic_errors"]:
            lines.append(f"  · {e['path']}: {e['message']}")
    lines.append("")
    lines.append("PASS — ready to register." if validation_ok else "FAIL — fix issues above.")
    return {
        "validation_ok": validation_ok,
        "report": {"manifest": m, "localhost": l},
        "summary_text": "\n".join(lines),
    }

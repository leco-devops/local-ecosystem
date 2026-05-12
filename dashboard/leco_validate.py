"""Validate manifest / localhost YAML for the registration wizard (LEco DevOps schema)."""

from __future__ import annotations

from pathlib import Path
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


def validate_configuration_on_disk(manifest_abs: str) -> dict[str, Any]:
    """
    Read ``leco.app.yaml`` + profile from disk; schema-validate; check merged manifest references
    (compose files, wrangler, manifest-relative compose overlays).
    """
    mp = Path(manifest_abs).expanduser()
    try:
        mp = mp.resolve()
    except OSError:
        pass
    if not mp.is_file():
        return {
            "ok": False,
            "validation_ok": False,
            "error": f"Manifest not found: {manifest_abs}",
            "manifest_path": str(manifest_abs),
            "localhost_profile_path": "",
            "report": None,
            "reference_errors": [],
            "reference_warnings": [],
            "summary_text": f"FAIL — manifest file not found:\n  {manifest_abs}",
        }
    try:
        my = mp.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "validation_ok": False,
            "error": str(exc),
            "manifest_path": str(mp),
            "localhost_profile_path": "",
            "report": None,
            "reference_errors": [],
            "reference_warnings": [],
            "summary_text": f"FAIL — cannot read manifest: {exc}",
        }

    prof_name = "leco.yaml"
    try:
        header = yaml.safe_load(my)
        if isinstance(header, dict):
            raw_prof = header.get("localHostProfile") or header.get("local_host_profile")
            if isinstance(raw_prof, str) and raw_prof.strip():
                prof_name = raw_prof.strip()
    except yaml.YAMLError:
        pass

    lp = mp.parent / prof_name
    ly = ""
    if lp.is_file():
        try:
            ly = lp.read_text(encoding="utf-8")
        except OSError as exc:
            base = validate_registration_yaml(my, "")
            reference_errors = [f"Cannot read profile {lp}: {exc}"]
            summary = base["summary_text"].rstrip() + "\n\nOn-disk references:\n" + "\n".join(
                f"  ✗ {e}" for e in reference_errors
            )
            summary += "\n\nOVERALL: FAIL — fix issues before registering."
            return {
                "ok": True,
                "validation_ok": False,
                "manifest_path": str(mp),
                "localhost_profile_path": str(lp),
                "report": base["report"],
                "reference_errors": reference_errors,
                "reference_warnings": [],
                "summary_text": summary,
            }
        from leco_detect import ensure_lh_network_hosting_overlay, normalize_profile_compose_backend_hosts

        normalize_profile_compose_backend_hosts(mp)
        ensure_lh_network_hosting_overlay(mp)
        try:
            ly = lp.read_text(encoding="utf-8")
        except OSError as exc:
            base = validate_registration_yaml(my, "")
            reference_errors = [f"Cannot re-read profile after auto-heal {lp}: {exc}"]
            summary = base["summary_text"].rstrip() + "\n\nOn-disk references:\n" + "\n".join(
                f"  ✗ {e}" for e in reference_errors
            )
            summary += "\n\nOVERALL: FAIL — fix issues before registering."
            return {
                "ok": True,
                "validation_ok": False,
                "manifest_path": str(mp),
                "localhost_profile_path": str(lp),
                "report": base["report"],
                "reference_errors": reference_errors,
                "reference_warnings": [],
                "summary_text": summary,
            }
    else:
        base_early = validate_registration_yaml(my, "")
        reference_errors = [f"Profile file not found (expected beside manifest): {lp}"]
        summary = base_early["summary_text"].rstrip() + "\n\nOn-disk references:\n" + "\n".join(
            f"  ✗ {e}" for e in reference_errors
        )
        summary += "\n\nOVERALL: FAIL — fix issues before registering."
        return {
            "ok": True,
            "validation_ok": False,
            "manifest_path": str(mp),
            "localhost_profile_path": str(lp),
            "report": base_early["report"],
            "reference_errors": reference_errors,
            "reference_warnings": [],
            "summary_text": summary,
        }

    base = validate_registration_yaml(my, ly)
    reference_errors: list[str] = []
    reference_warnings: list[str] = []

    try:
        from leco_app.schema import load_merged_manifest

        merged = load_merged_manifest(mp)
        em = merged.manifest
        root = em.resolved_root(mp)
        if em.docker_compose:
            cfm = (em.docker_compose.compose_file_from_manifest or "").strip()
            if cfm:
                p = Path(cfm)
                ap = p.resolve() if p.is_absolute() else (mp.parent / p).resolve()
                if not ap.is_file():
                    reference_errors.append(f"dockerCompose.composeFileFromManifest not found: {ap}")
            else:
                cf = root / Path(em.docker_compose.compose_file)
                if not cf.is_file():
                    reference_errors.append(f"dockerCompose.composeFile not found: {cf}")
            for rel in em.docker_compose.additional_compose_files or []:
                p = Path(str(rel).strip())
                if not str(p):
                    continue
                ap = p.resolve() if p.is_absolute() else (root / p).resolve()
                if not ap.is_file():
                    reference_errors.append(f"additionalComposeFiles not found: {ap}")
            for rel in em.docker_compose.additional_compose_files_from_manifest or []:
                p = Path(str(rel).strip())
                if not str(p):
                    continue
                ap = p.resolve() if p.is_absolute() else (mp.parent / p).resolve()
                if not ap.is_file():
                    reference_errors.append(f"additionalComposeFilesFromManifest not found: {ap}")
        else:
            reference_warnings.append(
                "No dockerCompose in effective manifest (Workers-only or not configured in leco.yaml)."
            )
        if em.cloudflare and em.cloudflare.wrangler_config:
            wc = root / Path(str(em.cloudflare.wrangler_config).strip())
            if not wc.is_file():
                reference_errors.append(f"cloudflare.wranglerConfig not found: {wc}")
    except Exception as exc:
        reference_errors.append(f"Effective manifest / merge: {exc}")

    validation_ok = bool(base["validation_ok"] and not reference_errors)
    parts: list[str] = [base["summary_text"].rstrip()]
    if reference_errors or reference_warnings:
        parts.append("")
        parts.append("On-disk references (compose / wrangler / overlays):")
        for e in reference_errors:
            parts.append(f"  ✗ {e}")
        for w in reference_warnings:
            parts.append(f"  ⚠ {w}")
    parts.append("")
    parts.append(
        "OVERALL: PASS — schemas and expected paths look OK; safe to register."
        if validation_ok
        else "OVERALL: FAIL — fix errors above before registering."
    )

    return {
        "ok": True,
        "validation_ok": validation_ok,
        "manifest_path": str(mp),
        "localhost_profile_path": str(lp.resolve()),
        "report": base["report"],
        "reference_errors": reference_errors,
        "reference_warnings": reference_warnings,
        "summary_text": "\n".join(parts),
    }

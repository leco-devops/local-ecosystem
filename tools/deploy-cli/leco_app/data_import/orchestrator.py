"""Discover and run hosted-app data imports."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from leco_app.data_import.context import ImportContext
from leco_app.data_import.importers import IMPORTERS
from leco_app.data_import.plan import build_import_plan, data_dir_for_manifest, entry_id
from leco_app.compose_runner import compose_subprocess_cwd, run_compose_capture
from leco_app.schema import load_effective_manifest


def compose_context_for_manifest(
    manifest_path: Path,
) -> tuple[Path, list[str], list[dict[str, Any]]]:
    """Return (compose_root, compose_tail, compose_ps rows)."""
    mp = manifest_path.resolve()
    compose_tail: list[str] = []
    compose_root = mp.parent
    try:
        import sys

        er = mp.parent.parent.parent
        dash = er / "dashboard"
        if dash.is_dir() and str(dash) not in sys.path:
            sys.path.insert(0, str(dash))
        from hosted_app_services import list_compose_file_paths, load_merged_compose_services

        for cf in list_compose_file_paths(mp):
            compose_tail.extend(["-f", str(cf)])
        compose_root = mp.parent
        try:
            m = load_effective_manifest(mp)
            compose_root = compose_subprocess_cwd(m, mp)
        except Exception:
            pass
        services = load_merged_compose_services(mp, compose_tail=compose_tail)
        _ = services
    except Exception:
        pass

    compose_ps: list[dict[str, Any]] = []
    if compose_tail:
        code, out = run_compose_capture(compose_tail, ["ps", "--format", "json"], cwd=compose_root)
        if code == 0 and out.strip():
            import json as _json

            try:
                parsed = _json.loads(out)
                compose_ps = parsed if isinstance(parsed, list) else [parsed]
            except _json.JSONDecodeError:
                for line in out.splitlines():
                    line = line.strip()
                    if line:
                        try:
                            row = _json.loads(line)
                            if isinstance(row, dict):
                                compose_ps.append(row)
                        except _json.JSONDecodeError:
                            pass
    return compose_root, compose_tail, compose_ps


def _load_local_cf(manifest_path: Path) -> dict[str, Any]:
    p = manifest_path.resolve().parent / "leco.local-cf.yaml"
    if not p.is_file():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def discover_data_import(
    manifest_path: Path,
    *,
    services: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build discovery payload for snapshot / GET discover."""
    plan = build_import_plan(manifest_path, services=services)
    plan["suggested_cli"] = _suggested_cli_snippets(plan)
    return plan


def _suggested_cli_snippets(plan: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for item in plan.get("items") or []:
        if item.get("kind") != "mongodb":
            continue
        db = item.get("database") or "<database>"
        out.append(
            f'mongodump --uri="mongodb://localhost:27017" --db={db} --archive '
            f'| mongorestore --uri="mongodb://127.0.0.1:<host-port>/{db}" --archive --drop'
        )
        out.append(
            'mongodump --uri="mongodb://localhost:27017" --archive '
            '| mongorestore --uri="mongodb://127.0.0.1:<host-port>" --archive --drop'
        )
        break
    return out


def run_import_plan_stream(
    manifest_path: Path,
    *,
    compose_root: Path,
    compose_tail: list[str],
    services: dict[str, dict[str, Any]],
    compose_ps: list[dict[str, Any]],
    reimport: bool = False,
    dry_run: bool = False,
    selected_ids: list[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield NDJSON events: log, progress, done."""
    mp = manifest_path.resolve()
    data_dir = data_dir_for_manifest(mp)
    plan = build_import_plan(mp, services=services)
    items = list(plan.get("items") or [])
    warnings = list(plan.get("warnings") or [])

    for w in warnings:
        yield {"type": "log", "text": f"Warning: {w}\n"}

    if not plan.get("present"):
        yield {
            "type": "done",
            "result": {
                "ok": False,
                "error": f"No data folder at {data_dir}",
                "imported": 0,
                "failed": 0,
            },
        }
        return

    if not items:
        yield {
            "type": "done",
            "result": {
                "ok": False,
                "error": "data/ exists but nothing to import (add manifest.yaml or dump files)",
                "imported": 0,
                "failed": 0,
            },
        }
        return

    if selected_ids is not None:
        sel = {str(x).strip() for x in selected_ids if str(x).strip()}
        if not sel:
            yield {
                "type": "done",
                "result": {
                    "ok": False,
                    "error": "No import steps selected",
                    "imported": 0,
                    "failed": 0,
                },
            }
            return
        filtered = [e for e in items if entry_id(e) in sel]
        skipped = len(items) - len(filtered)
        if not filtered:
            yield {
                "type": "done",
                "result": {
                    "ok": False,
                    "error": "Selected steps do not match the current import plan",
                    "imported": 0,
                    "failed": 0,
                },
            }
            return
        if skipped:
            yield {
                "type": "log",
                "text": f"Importing {len(filtered)} of {len(items)} step(s) (skipped {skipped} not selected)\n",
            }
        items = filtered

    ctx = ImportContext(
        slug=mp.parent.name,
        manifest_path=mp,
        data_dir=data_dir,
        compose_root=compose_root,
        compose_tail=compose_tail,
        services=services,
        compose_ps=compose_ps,
        local_cf=_load_local_cf(mp),
        reimport=reimport,
        dry_run=dry_run,
    )

    logs: list[str] = []
    ctx._log = lambda t: logs.append(t)  # noqa: SLF001

    imported = 0
    failed = 0
    total = len(items)

    yield {"type": "log", "text": f"Import plan: {total} step(s) from {data_dir}\n"}

    for step, entry in enumerate(items, 1):
        kind = str(entry.get("kind") or "")
        label = str(entry.get("label") or kind)
        yield {"type": "progress", "step": step, "total": total, "label": label}
        yield {"type": "log", "text": f"\n=== [{step}/{total}] {label} ===\n"}

        fn = IMPORTERS.get(kind)
        if not fn:
            failed += 1
            yield {"type": "log", "text": f"Unknown kind: {kind}\n"}
            continue

        try:
            ok, detail = fn(ctx, entry)
        except Exception as exc:
            ok = False
            detail = str(exc)

        for line in logs:
            yield {"type": "log", "text": line}
        logs.clear()

        if detail:
            yield {"type": "log", "text": detail[-4000:] + ("\n" if not detail.endswith("\n") else "")}

        if ok:
            imported += 1
            yield {"type": "log", "text": f"OK: {label}\n"}
        else:
            failed += 1
            yield {"type": "log", "text": f"FAILED: {label}\n"}

    yield {
        "type": "done",
        "result": {
            "ok": failed == 0,
            "imported": imported,
            "failed": failed,
            "log": "".join(logs)[-12000:],
        },
    }

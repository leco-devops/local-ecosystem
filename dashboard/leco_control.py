"""
Dynamic Control targets for leco-app style compose projects.

Registry: config/leco-registry.yaml under PROJECT_ROOT (override DASHBOARD_LECO_REGISTRY).
Manifest paths are relative to PROJECT_ROOT or absolute; must resolve under PROJECT_ROOT
or its parent directory (sibling repos).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

try:
    from leco_app.compose_runner import path_for_docker_daemon
except ImportError:  # local dev without pip install -e tools/deploy-cli

    def path_for_docker_daemon(container_path: Path) -> Path:  # type: ignore[misc]
        try:
            return container_path.resolve()
        except OSError:
            return container_path


PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")
LECO_REGISTRY_PATH = os.getenv(
    "DASHBOARD_LECO_REGISTRY",
    os.path.join(PROJECT_ROOT, "config", "leco-registry.yaml"),
)

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _slug(s: str) -> str:
    t = _SLUG_RE.sub("-", (s or "").strip().lower()).strip("-")
    return t or "app"


def _workspace_parent_mount() -> str:
    """Host parent of PROJECT_ROOT mounted here (see dashboard.sh). Sibling app repos live under this path."""
    return os.getenv("DASHBOARD_WORKSPACE_PARENT", "").strip()


def _allowed_path(abs_path: str) -> bool:
    try:
        rp = os.path.realpath(abs_path)
        pr = os.path.realpath(PROJECT_ROOT)
        if rp == pr or rp.startswith(pr + os.sep):
            return True
        wp = _workspace_parent_mount()
        if wp:
            wpr = os.path.realpath(wp)
            if os.path.isdir(wpr) and (rp == wpr or rp.startswith(wpr + os.sep)):
                return True
        parent = os.path.realpath(os.path.join(pr, ".."))
        return rp.startswith(parent + os.sep)
    except OSError:
        return False


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def load_leco_registry_entries() -> list[dict[str, Any]]:
    if not os.path.isfile(LECO_REGISTRY_PATH):
        return []
    try:
        raw = yaml.safe_load(Path(LECO_REGISTRY_PATH).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(raw, dict):
        return []
    apps = raw.get("apps")
    if not isinstance(apps, list):
        return []
    out: list[dict[str, Any]] = []
    for a in apps:
        if isinstance(a, dict) and a.get("id") and a.get("manifest"):
            out.append(a)
    return out


def resolve_manifest_path(manifest_field: str) -> str | None:
    """Resolve manifest path for host or dashboard container.

    Registry entries often use ``../OtherRepo/leco.app.yaml``. On the host,
    :func:`Path.resolve` follows ``..`` out of the repo. Inside Docker only
    ``/project`` is mounted, so ``Path('/project/../OtherRepo').resolve()``
    breaks; use :envvar:`DASHBOARD_WORKSPACE_PARENT` (repo parent's mount).
    """
    mf = (manifest_field or "").strip()
    if not mf:
        return None
    p = Path(mf)
    if p.is_absolute():
        try:
            abs_path = str(p.resolve())
        except OSError:
            return None
        if not _allowed_path(abs_path) or not os.path.isfile(abs_path):
            return None
        return os.path.realpath(abs_path)

    parts = list(Path(mf).parts)
    up = 0
    for part in parts:
        if part == "..":
            up += 1
        else:
            break
    rest = parts[up:]

    direct = Path(PROJECT_ROOT) / mf
    try:
        abs_direct = str(direct.resolve())
    except OSError:
        abs_direct = ""
    if abs_direct and _allowed_path(abs_direct) and os.path.isfile(abs_direct):
        return os.path.realpath(abs_direct)

    if not rest:
        return None

    wp = _workspace_parent_mount()
    if up >= 1:
        if wp and os.path.isdir(wp):
            base = Path(wp).resolve()
            for _ in range(up - 1):
                base = base.parent
        else:
            base = Path(PROJECT_ROOT).resolve()
            for _ in range(up):
                base = base.parent
        cand = base.joinpath(*rest)
        try:
            abs_c = str(cand.resolve())
        except OSError:
            return None
        if _allowed_path(abs_c) and os.path.isfile(abs_c):
            return os.path.realpath(abs_c)
    return None


def parse_leco_manifest_for_compose(manifest_path: str) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    dc = _pick(data, "dockerCompose", "docker_compose")
    if not isinstance(dc, dict):
        return None
    compose_file = _pick(dc, "composeFile", "compose_file")
    if not compose_file:
        return None
    root_rel = _pick(data, "root") or "."
    manifest_dir = os.path.dirname(os.path.realpath(manifest_path))
    root = os.path.realpath(os.path.join(manifest_dir, str(root_rel)))
    if not _allowed_path(root):
        return None
    cf = Path(compose_file)
    compose_abs = str(cf.resolve()) if cf.is_absolute() else str((Path(root) / compose_file).resolve())
    if not _allowed_path(compose_abs) or not os.path.isfile(compose_abs):
        return None

    # Docker Desktop (dashboard in container + socket): bind sources must be host paths, not
    # /workspace-parent/... which exists only in the dashboard container.
    root_c = Path(root).resolve()
    compose_c = Path(compose_abs).resolve()
    root_for_compose = str(path_for_docker_daemon(root_c))
    compose_for_daemon = str(path_for_docker_daemon(compose_c))

    tail: list[str] = ["-f", compose_for_daemon]
    extras = dc.get("additionalComposeFiles") or dc.get("additional_compose_files") or []
    if isinstance(extras, list):
        for ex in extras:
            if not isinstance(ex, str) or not ex.strip():
                continue
            er = Path(ex.strip())
            ex_abs = str(er.resolve()) if er.is_absolute() else str((Path(root) / er).resolve())
            if not _allowed_path(ex_abs) or not os.path.isfile(ex_abs):
                return None
            tail.extend(["-f", str(path_for_docker_daemon(Path(ex_abs).resolve()))])
    env_file = _pick(dc, "envFile", "env_file")
    if env_file:
        ef = Path(env_file)
        ef_abs = str(ef.resolve()) if ef.is_absolute() else str((Path(root) / env_file).resolve())
        if os.path.isfile(ef_abs):
            tail.extend(["--env-file", str(path_for_docker_daemon(Path(ef_abs).resolve()))])
    pn = _pick(dc, "projectName", "project_name")
    if pn:
        tail.extend(["-p", str(pn)])
    for prof in dc.get("profiles") or []:
        if prof:
            tail.extend(["--profile", str(prof)])

    name = _pick(data, "name")
    slug = _slug(str(name)) if name else _slug(Path(manifest_path).parent.name)

    return {
        "manifest_path": manifest_path,
        "root": root_for_compose,
        "compose_tail": tail,
        "slug": slug,
    }


def parse_leco_effective_manifest_for_compose(manifest_path: str) -> dict[str, Any] | None:
    """Compose metadata from bridge + ``leco.yaml`` (v3 profile ``infrastructure.dockerCompose``)."""
    try:
        from leco_app.schema import load_effective_manifest
    except ImportError:
        return None
    mp = Path(manifest_path)
    try:
        m = load_effective_manifest(mp)
    except Exception:
        return None
    dc = m.docker_compose
    if dc is None:
        return None
    compose_rel = (dc.compose_file or "").strip()
    if not compose_rel:
        return None
    root = m.resolved_root(mp)
    root_s = str(root.resolve())
    if not _allowed_path(root_s):
        return None
    cf = Path(compose_rel)
    compose_abs = str(cf.resolve()) if cf.is_absolute() else str((root / compose_rel).resolve())
    if not _allowed_path(compose_abs) or not os.path.isfile(compose_abs):
        return None

    root_c = Path(root).resolve()
    compose_c = Path(compose_abs).resolve()
    root_for_compose = str(path_for_docker_daemon(root_c))
    compose_for_daemon = str(path_for_docker_daemon(compose_c))

    tail: list[str] = ["-f", compose_for_daemon]
    for extra in dc.additional_compose_files or []:
        es = str(extra).strip()
        if not es:
            continue
        er = Path(es)
        ex_abs = str(er.resolve()) if er.is_absolute() else str((root / er).resolve())
        if not _allowed_path(ex_abs) or not os.path.isfile(ex_abs):
            return None
        tail.extend(["-f", str(path_for_docker_daemon(Path(ex_abs).resolve()))])
    env_file = dc.env_file
    if env_file:
        ef = Path(str(env_file).strip())
        ef_abs = str(ef.resolve()) if ef.is_absolute() else str((Path(root) / env_file).resolve())
        if os.path.isfile(ef_abs):
            tail.extend(["--env-file", str(path_for_docker_daemon(Path(ef_abs).resolve()))])
    pn = dc.project_name
    if pn:
        tail.extend(["-p", str(pn)])
    for prof in dc.profiles or []:
        if prof:
            tail.extend(["--profile", str(prof)])

    name = m.name
    slug = _slug(str(name)) if name else _slug(Path(manifest_path).parent.name)

    return {
        "manifest_path": manifest_path,
        "root": root_for_compose,
        "compose_tail": tail,
        "slug": slug,
    }


def _compose_meta_worker_only(manifest_path: str) -> dict[str, Any] | None:
    """Registry row with no ``dockerCompose`` (Wrangler-only): still list in dashboard / Control."""
    mp = Path(manifest_path)
    try:
        from leco_app.schema import load_effective_manifest

        m = load_effective_manifest(mp)
    except Exception:
        try:
            data = yaml.safe_load(mp.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        root_rel = _pick(data, "root") or "."
        manifest_dir = os.path.dirname(os.path.realpath(manifest_path))
        root = os.path.realpath(os.path.join(manifest_dir, str(root_rel)))
        if not _allowed_path(root):
            return None
        root_for = str(path_for_docker_daemon(Path(root).resolve()))
        name = _pick(data, "name")
        slug = _slug(str(name)) if name else _slug(Path(manifest_path).parent.name)
        return {
            "manifest_path": manifest_path,
            "root": root_for,
            "compose_tail": [],
            "slug": slug,
        }

    root = m.resolved_root(mp)
    root_s = str(root.resolve())
    if not _allowed_path(root_s):
        return None
    root_for = str(path_for_docker_daemon(Path(root_s).resolve()))
    name = m.name
    slug = _slug(str(name)) if name else _slug(mp.parent.name)
    return {
        "manifest_path": manifest_path,
        "root": root_for,
        "compose_tail": [],
        "slug": slug,
    }


def leco_meta_for_slug(slug: str) -> dict[str, Any] | None:
    for entry in load_leco_registry_entries():
        if str(entry.get("id") or "").strip() != slug:
            continue
        mp = resolve_manifest_path(str(entry["manifest"]).strip())
        if not mp:
            return None
        parsed = (
            parse_leco_manifest_for_compose(mp)
            or parse_leco_effective_manifest_for_compose(mp)
            or _compose_meta_worker_only(mp)
        )
        if not parsed:
            return None
        return {
            "leco_slug": slug,
            "label": str(entry.get("label") or slug),
            "manifest_path": mp,
            "root": parsed["root"],
            "compose_tail": parsed["compose_tail"],
        }
    return None


def leco_target_id_for_slug(slug: str) -> str:
    return f"leco-stack-{slug}"


def list_leco_control_targets(dc) -> list[dict[str, Any]]:
    _ = dc  # reserved for future per-container hints
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in load_leco_registry_entries():
        slug = str(entry.get("id") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        meta = leco_meta_for_slug(slug)
        if not meta:
            continue
        tid = leco_target_id_for_slug(slug)
        rt = leco_stack_runtime(meta)
        out.append(
            {
                "id": tid,
                "label": meta["label"],
                "group": "leco-apps",
                "container": None,
                "actions": sorted(
                    {
                        "start",
                        "stop",
                        "restart",
                        "deploy",
                        "recreate",
                        "remove",
                        "reset",
                        "pause",
                        "unpause",
                    }
                ),
                "runtime": rt,
            }
        )
    return out


def rows_from_compose_ps_stdout(stdout: str) -> list[dict[str, Any]]:
    """Parse `docker compose ps --format json` output (single JSON array, one object, or NDJSON)."""
    stdout = (stdout or "").strip()
    if not stdout:
        return []
    try:
        data = json.loads(stdout)
        rows = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        rows = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [r for r in rows if isinstance(r, dict)]


def _parse_compose_ps(stdout: str) -> tuple[int, int]:
    rows = rows_from_compose_ps_stdout(stdout)
    total = len(rows)
    running = 0
    for r in rows:
        st = str(r.get("State") or r.get("state") or "").lower()
        if st == "running":
            running += 1
    return running, total


def compose_ps_result(meta: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    """Run compose ps -a --format json. Returns (rows, returncode). returncode -1 on exception."""
    import subprocess

    tail = meta.get("compose_tail") or []
    if not tail:
        return [], 0

    try:
        p = subprocess.run(
            ["docker", "compose", *tail, "ps", "-a", "--format", "json"],
            cwd=meta["root"],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [], -1
    rows = rows_from_compose_ps_stdout(p.stdout or "")
    return rows, p.returncode


def compose_ps_rows(meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Parsed compose ps rows; empty if compose failed."""
    rows, code = compose_ps_result(meta)
    if code != 0:
        return []
    return rows


def leco_stack_runtime(meta: dict[str, Any]) -> dict[str, Any]:
    if not meta.get("compose_tail"):
        return {
            "kind": "stack",
            "status": "no_compose",
            "label": "No Docker Compose (Wrangler-only or compose only in leco.yaml)",
            "running": None,
        }
    rows, code = compose_ps_result(meta)
    if code == -1:
        return {
            "kind": "stack",
            "status": "unknown",
            "label": "Compose status error (timeout or spawn failed)",
            "running": None,
        }
    if code != 0:
        return {
            "kind": "stack",
            "status": "compose",
            "label": "Compose project (ps failed)",
            "running": None,
        }
    total = len(rows)
    running = sum(
        1 for r in rows if str(r.get("State") or r.get("state") or "").lower() == "running"
    )
    if total == 0:
        return {
            "kind": "stack",
            "status": "stopped",
            "label": "No containers / stack down",
            "running": False,
        }
    if running == total:
        return {
            "kind": "stack",
            "status": "running",
            "label": f"{total} service(s) running",
            "running": True,
        }
    if running == 0:
        return {
            "kind": "stack",
            "status": "stopped",
            "label": f"0/{total} running",
            "running": False,
        }
    return {
        "kind": "stack",
        "status": "partial",
        "label": f"{running}/{total} running",
        "running": True,
    }


def resolve_leco_target(target_id: str) -> dict[str, Any] | None:
    if not target_id.startswith("leco-stack-"):
        return None
    slug = target_id[len("leco-stack-") :].strip()
    if not slug:
        return None
    return leco_meta_for_slug(slug)

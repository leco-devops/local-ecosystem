"""Read-only app directory scan for registration wizard (mirrors LEco DevOps detect without deploy-cli)."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import yaml

from hosting_layout import HOSTING_SOURCE_LINK_NAME, compute_source_target

_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def slugify_app_id(s: str) -> str:
    """Registry / hosting slug. Never returns ``.`` or ``..`` — those would resolve to parent dirs under ``hosting/app-*``."""
    raw = (s or "").strip().lower()
    t = _SLUG_RE.sub("-", raw).strip("-")
    if not t or t in (".", "..") or set(t) <= {"."}:
        return "app"
    return t


def require_registration_app_id(app_id: str) -> str:
    """Validate operator-supplied id before Generate/Save/Register; then :func:`slugify_app_id`."""
    raw = (app_id or "").strip()
    if not raw or raw in (".", "..") or set(raw) <= {"."}:
        raise ValueError(
            "app_id must be a non-empty slug (e.g. my-app or 1note). "
            "The values '.' and '..' are not allowed — they break hosting/app-available paths."
        )
    if not re.search(r"[A-Za-z0-9]", raw):
        raise ValueError("app_id must contain at least one letter or number.")
    aid = slugify_app_id(app_id)
    if not aid:
        raise ValueError("app_id required")
    # Enforce that app id can always derive a host-safe main URL default.
    host_slug_from_app_id(aid)
    return aid


def host_slug_from_app_id(app_id: str) -> str:
    """
    Host-safe slug for default main URL (`https://<slug>.lh`).

    Normalizes app id to lowercase DNS-label characters and validates label length/shape.
    """
    raw = slugify_app_id(app_id).replace(".", "-").replace("_", "-").lower()
    s = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if not s:
        raise ValueError("app_id cannot derive a host-safe URL label.")
    if len(s) > 63:
        raise ValueError("app_id is too long for host labels (max 63 chars after normalization).")
    if not _HOST_LABEL_RE.match(s):
        raise ValueError("app_id cannot derive a valid host label; use letters, numbers, dots, dashes or underscores.")
    return s


def main_url_from_app_id(app_id: str) -> str:
    return main_urls_from_app_id(app_id)["https"]


def main_urls_from_app_id(app_id: str) -> dict[str, str]:
    host = host_slug_from_app_id(app_id)
    return {
        "https": f"https://{host}.lh",
        "http": f"http://{host}.lh",
    }

COMPOSE_NAMES = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

WRANGLER_PATHS = (
    Path("wrangler.toml"),
    Path("cloudflare") / "wrangler.toml",
)

# Shown in detect signals; local provision still expects TOML (see deploy-cli wrangler_cf_resources).
WRANGLER_JSON_HINT_PATHS = (
    Path("wrangler.json"),
    Path("wrangler.jsonc"),
    Path("cloudflare") / "wrangler.json",
    Path("cloudflare") / "wrangler.jsonc",
)

_SCAN_PRUNE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "coverage",
    ".venv",
    "venv",
    "__pycache__",
}


def _parse_ports(ports_val: Any) -> list[int]:
    out: list[int] = []
    if ports_val is None:
        return out
    items = ports_val if isinstance(ports_val, list) else [ports_val]
    for p in items:
        if isinstance(p, int):
            out.append(p)
            continue
        if not isinstance(p, str):
            continue
        part = p.strip().split(":")[-2] if p.count(":") >= 2 else p.split(":")[0]
        try:
            out.append(int(part))
        except ValueError:
            pass
    return out


def _scan_compose_ports(root: Path, rel: Path) -> list[int]:
    path = root / rel
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    services = data.get("services") or {}
    if not isinstance(services, dict):
        return []
    host_ports: list[int] = []
    for spec in services.values():
        if isinstance(spec, dict):
            host_ports.extend(_parse_ports(spec.get("ports")))
    return sorted(set(host_ports))


def _list_compose_files(root: Path) -> list[Path]:
    r = root.resolve()
    found: list[Path] = []
    for name in COMPOSE_NAMES:
        rel = Path(name)
        if (r / rel).is_file():
            found.append(rel)
    docker_dir = r / "docker"
    if docker_dir.is_dir():
        for name in COMPOSE_NAMES:
            rel = Path("docker") / name
            if (r / rel).is_file():
                found.append(rel)
    return found


def _pick_primary_compose(files: list[Path]) -> Path | None:
    for rel in files:
        if rel.name == "docker-compose.yml" and rel.parent == Path("."):
            return rel
    return files[0] if files else None


def _detect_wrangler_shallow(root: Path) -> Path | None:
    r = root.resolve()
    for rel in WRANGLER_PATHS:
        if (r / rel).is_file():
            return rel
    return None


def _detect_wrangler(root: Path) -> Path | None:
    r = root.resolve()
    shallow = _detect_wrangler_shallow(r)
    if shallow is not None:
        return shallow
    for dirpath, dirs, files in os.walk(r):
        rp = Path(dirpath)
        try:
            depth = len(rp.relative_to(r).parts)
        except ValueError:
            continue
        dirs[:] = [d for d in dirs if d not in _SCAN_PRUNE_DIRS]
        if depth > 6:
            dirs[:] = []
            continue
        if "wrangler.toml" in files:
            return (rp / "wrangler.toml").relative_to(r)
    return None


def compute_hosting_source_symlink_target(orig_root: Path, manifest_dict: dict[str, Any]) -> Path:
    """Directory the hosting ``source`` symlink should point at.

    ``root: source`` on the bridge names the symlink under ``app-available/<id>/``, not a path
    under ``orig_root`` — treat it like ``.`` when resolving the registration tree.

    If the user registered ``.../repo/source`` but Wrangler or Compose files live in ``.../repo/``,
    point ``source`` at the repo root so ``wrangler.toml`` / ``docker-compose.yml`` resolve without
    brittle ``..`` paths.
    """
    m = dict(manifest_dict)
    r = m.get("root", ".")
    if isinstance(r, str) and r.strip() == HOSTING_SOURCE_LINK_NAME:
        m["root"] = "."
    base = compute_source_target(orig_root, m).resolve()
    if not base.is_dir():
        return base
    if base.name != "source":
        return base
    parent = base.parent
    if not parent.is_dir() or parent == base:
        return base
    wr_here = _detect_wrangler(base) is not None
    wr_parent = _detect_wrangler_shallow(parent) is not None
    compose_here = bool(_list_compose_files(base))
    compose_parent = bool(_list_compose_files(parent))
    if wr_parent and not wr_here:
        return parent.resolve()
    if compose_parent and not compose_here:
        return parent.resolve()
    return base


def registration_scan_root(root: Path) -> Path:
    """
    Best-effort app tree for Detect/Generate when path points at hosting materialization.

    When operators pass ``hosting/app-available/<id>`` (which often contains ``root: source``),
    scan the manifest-resolved root instead of the staging directory itself.
    """
    r = root.resolve()
    man = r / "leco.app.yaml"
    if not man.is_file():
        return r
    try:
        parsed = yaml.safe_load(man.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return r
    if not isinstance(parsed, dict):
        return r
    try:
        target = compute_source_target(r, parsed).resolve()
    except OSError:
        return r
    if not target.is_dir():
        return r
    return target if target != r else r


def _detect_wrangler_relpath_from_base(base: Path, max_up: int = 6) -> str | None:
    """Return path relative to ``base`` (may use ``..``) to the nearest wrangler file walking up."""
    b = base.resolve()
    try_root: Path | None = b
    for _ in range(max_up):
        if try_root is None or not try_root.is_dir():
            break
        wrel = _detect_wrangler(try_root) if try_root == b else _detect_wrangler_shallow(try_root)
        if wrel is not None:
            abs_p = (try_root / wrel).resolve()
            try:
                rel_to_base = os.path.relpath(str(abs_p), str(b))
            except ValueError:
                return None
            return rel_to_base.replace("\\", "/")
        if try_root.parent == try_root:
            break
        try_root = try_root.parent
    return None


def _detect_wrangler_json_hint(root: Path) -> str | None:
    r = root.resolve()
    for rel in WRANGLER_JSON_HINT_PATHS:
        if (r / rel).is_file():
            return rel.as_posix()
    for dirpath, dirs, files in os.walk(r):
        rp = Path(dirpath)
        try:
            depth = len(rp.relative_to(r).parts)
        except ValueError:
            continue
        dirs[:] = [d for d in dirs if d not in _SCAN_PRUNE_DIRS]
        if depth > 6:
            dirs[:] = []
            continue
        for name in ("wrangler.json", "wrangler.jsonc"):
            if name in files:
                return (rp / name).relative_to(r).as_posix()
    return None


def _suggest_local_cf_public_prefix(app_id: str) -> str | None:
    try:
        return host_slug_from_app_id(app_id)
    except ValueError:
        return None


def _has_file(root: Path, rel: Path) -> bool:
    return (root.resolve() / rel).is_file()


def collect_config_signals(root: Path) -> dict[str, Any]:
    """Broad presence hints for UI (compose / wrangler / common stacks / deploy configs)."""
    r = root.resolve()

    def hf(*parts: str) -> bool:
        return _has_file(r, Path(*parts))

    vite = hf("vite.config.ts") or hf("vite.config.js") or hf("vite.config.mjs")
    nuxt = hf("nuxt.config.ts") or hf("nuxt.config.js") or hf("nuxt.config.mjs")
    nextc = hf("next.config.js") or hf("next.config.mjs") or hf("next.config.ts")
    k8s = hf("k8s", "deployment.yaml") or hf("kubernetes", "deployment.yaml") or hf("deploy", "deployment.yaml")

    return {
        "dockerfile": hf("Dockerfile") or hf("docker", "Dockerfile"),
        "package_json": hf("package.json"),
        "pnpm_workspace": hf("pnpm-workspace.yaml"),
        "composer_json": hf("composer.json"),
        "pyproject_toml": hf("pyproject.toml"),
        "requirements_txt": hf("requirements.txt"),
        "go_mod": hf("go.mod"),
        "cargo_toml": hf("Cargo.toml"),
        "gemfile": hf("Gemfile"),
        "vite_config": vite,
        "nuxt_config": nuxt,
        "next_config": nextc,
        "svelte_config": hf("svelte.config.js"),
        "angular_json": hf("angular.json"),
        "remix_config": hf("remix.config.js"),
        "astro_config": hf("astro.config.mjs") or hf("astro.config.ts"),
        "fly_toml": hf("fly.toml"),
        "railway_toml": hf("railway.toml"),
        "vercel_json": hf("vercel.json"),
        "netlify_toml": hf("netlify.toml"),
        "capacitor_config": hf("capacitor.config.json") or hf("capacitor.config.ts"),
        "helm_chart": hf("Chart.yaml") or hf("chart", "Chart.yaml"),
        "kubernetes_deployment_yaml": k8s,
        "wrangler_json_present": _detect_wrangler_json_hint(r) is not None,
        "wrangler_json_path": _detect_wrangler_json_hint(r),
    }


def detect_archetype(root: Path) -> str:
    r = root.resolve()
    if (r / "wp-config.php").is_file() or (r / "wp-config-sample.php").is_file():
        return "wordpress"
    if (r / "bin" / "magento").is_file() or (r / "app" / "etc" / "env.php").is_file():
        return "magento2"
    for name in ("next.config.js", "next.config.mjs", "next.config.ts"):
        if (r / name).is_file():
            return "nextjs"
    comp = r / "composer.json"
    if comp.is_file():
        try:
            data = json.loads(comp.read_text(encoding="utf-8"))
            req = data.get("require") or {}
            if isinstance(req, dict) and any("laravel/framework" in str(k) for k in req):
                return "laravel"
        except (OSError, json.JSONDecodeError, TypeError):
            pass
        return "php-fpm"
    pkg = r / "package.json"
    if pkg.is_file():
        return "node"
    if (r / "pom.xml").is_file() or (r / "build.gradle").is_file():
        return "java"
    if list(r.glob("*.csproj")):
        return "dotnet"
    if (r / "index.html").is_file():
        return "static"
    return "generic"


def _humanize_project_label(raw: str) -> str:
    """Turn package / worker / compose project tokens into a short display title."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("@") and "/" in s:
        s = s.split("/", 1)[-1].strip()
    parts = [p for p in re.split(r"[-_.\s]+", s) if p]
    if not parts:
        return raw.strip()
    return " ".join((p[0].upper() + p[1:].lower()) if len(p) > 1 else p.upper() for p in parts)


def _read_package_json_name(root: Path) -> str | None:
    p = root / "package.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    n = data.get("name")
    return str(n).strip() if isinstance(n, str) and str(n).strip() else None


def _read_composer_json_name(root: Path) -> str | None:
    p = root / "composer.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    n = data.get("name")
    if not isinstance(n, str) or not n.strip():
        return None
    s = n.strip()
    if "/" in s:
        s = s.split("/", 1)[-1].strip()
    return s or None


def _read_wrangler_toml_name(root: Path) -> str | None:
    wrel = _detect_wrangler(root)
    if wrel is None:
        return None
    path = root / wrel
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for pat in (r'^\s*name\s*=\s*"([^"]+)"', r"^\s*name\s*=\s*'([^']+)'"):
        m = re.search(pat, text, re.MULTILINE)
        if m:
            t = m.group(1).strip()
            return t if t else None
    return None


def _read_compose_top_level_name(root: Path) -> str | None:
    files = _list_compose_files(root)
    if not files:
        return None
    primary = _pick_primary_compose(files)
    if primary is None:
        return None
    path = root / primary
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    n = data.get("name")
    return str(n).strip() if isinstance(n, str) and str(n).strip() else None


def infer_suggested_label(root: Path) -> str | None:
    """Best-effort display name from repo config (registry label), not the Traefik hostname."""
    r = root.resolve()
    for raw in (
        _read_package_json_name(r),
        _read_wrangler_toml_name(r),
        _read_compose_top_level_name(r),
        _read_composer_json_name(r),
    ):
        if not raw:
            continue
        human = _humanize_project_label(raw)
        return human if human else raw
    return None


def scan_app_directory(root: Path) -> dict[str, Any]:
    root = root.resolve()
    compose_files = _list_compose_files(root)
    primary = _pick_primary_compose(compose_files)
    host_ports = _scan_compose_ports(root, primary) if primary else []
    wc = _detect_wrangler(root)
    arch = detect_archetype(root)
    manifest_path = root / "leco.app.yaml"
    return {
        "root": str(root),
        "has_wrangler": wc is not None,
        "wrangler_config": wc.as_posix() if wc else None,
        "compose_files": [p.as_posix() for p in compose_files],
        "host_ports": host_ports,
        "suggested_archetype": arch,
        "suggested_label": infer_suggested_label(root),
        "existing_manifest": manifest_path.is_file(),
        "manifest_path": manifest_path.resolve().as_posix(),
        "config_signals": collect_config_signals(root),
    }


def allowed_registration_bases() -> list[Path]:
    project = Path(os.environ.get("DASHBOARD_PROJECT_ROOT", "/project")).resolve()
    bases = [project]
    wsp = (os.environ.get("DASHBOARD_WORKSPACE_PARENT") or "").strip()
    if wsp:
        bases.append(Path(wsp).resolve())
    return bases


def registration_path_field_for_ui(root: Path) -> str:
    """Canonical path string for the register wizard (project-relative or ``wsp:…``)."""
    bases = allowed_registration_bases()
    project = bases[0].resolve()
    r = root.resolve()
    try:
        rel = r.relative_to(project)
        s = rel.as_posix()
        return s if s != "." else "."
    except ValueError:
        pass
    if len(bases) >= 2:
        wsp = bases[1].resolve()
        try:
            rel = r.relative_to(wsp)
            s = rel.as_posix()
            return f"wsp:{s}" if s else "wsp:"
        except ValueError:
            pass
    return str(r)


def _safe_rel_segments(rel: str) -> tuple[str, ...]:
    parts: list[str] = []
    for p in Path((rel or "").replace("\\", "/")).parts:
        if p in ("", ".", ".."):
            raise ValueError("Invalid path segment")
        parts.append(p)
    return tuple(parts)


def _posixify_path_str(s: str) -> str:
    return Path((s or "").replace("\\", "/")).as_posix()


def map_host_path_to_registration_field(raw: str) -> str:
    """If the user pasted a host absolute path, map it to project-relative or ``wsp:…``.

    Docker mounts the repo at ``/project`` and (when set) the workspace parent at
    ``/workspace-parent``. The host paths for those mounts are passed as
    ``DASHBOARD_PROJECT_ROOT_HOST`` and ``DASHBOARD_WORKSPACE_PARENT_HOST`` so Finder-style
    paths still resolve inside the container.
    """
    r = (raw or "").strip()
    if not r:
        return r
    rp = _posixify_path_str(r)
    host_proj = (os.environ.get("DASHBOARD_PROJECT_ROOT_HOST") or "").strip()
    if host_proj:
        pfx = _posixify_path_str(host_proj).rstrip("/")
        if rp == pfx or rp.startswith(pfx + "/"):
            rel = rp[len(pfx) :].lstrip("/")
            return rel if rel else "."
    host_wsp = (os.environ.get("DASHBOARD_WORKSPACE_PARENT_HOST") or "").strip()
    if host_wsp:
        pfx = _posixify_path_str(host_wsp).rstrip("/")
        if rp == pfx or rp.startswith(pfx + "/"):
            rel = rp[len(pfx) :].lstrip("/")
            return f"wsp:{rel}" if rel else "wsp:"
    return r


def resolve_registration_path(user_path: str) -> Path:
    """Resolve a path that must stay under project or workspace-parent.

    Use prefix ``wsp:`` for directories under ``DASHBOARD_WORKSPACE_PARENT`` when mounted
    (e.g. ``wsp:MySiblingRepo``). Paths under the ecosystem repo stay relative to
    ``DASHBOARD_PROJECT_ROOT`` without ``..`` segments.
    """
    raw = map_host_path_to_registration_field((user_path or "").strip())
    if not raw:
        raise ValueError("Invalid path")
    bases = allowed_registration_bases()
    project = bases[0].resolve()

    if raw.startswith("wsp:"):
        if len(bases) < 2:
            raise ValueError("Workspace parent is not mounted (DASHBOARD_WORKSPACE_PARENT)")
        wsp_base = bases[1].resolve()
        rest = raw[4:].strip().strip("/")
        # ``wsp:.`` / ``wsp:./`` must mean workspace root, not a segment named ``.`` (invalid for _safe_rel_segments).
        if not rest or rest == "." or rest == "./":
            cand = wsp_base
        else:
            segs = _safe_rel_segments(rest)
            cand = wsp_base.joinpath(*segs).resolve()
        try:
            cand.relative_to(wsp_base)
        except ValueError as exc:
            raise ValueError("Path must stay under workspace-parent mount") from exc
        if not cand.is_dir():
            raise ValueError("Not a directory")
        return cand

    if ".." in Path(raw).parts:
        raise ValueError("Invalid path")
    if os.path.isabs(raw):
        cand = Path(raw).resolve()
    else:
        cand = (project / raw).resolve()
    for base in bases:
        try:
            cand.relative_to(base)
            if cand.is_dir():
                return cand
        except ValueError:
            continue
    raise ValueError("Path must be a directory under the ecosystem repo or workspace-parent")


def browse_leco_directories(root_kind: str, subpath: str) -> dict[str, Any]:
    """List immediate subdirectories under project or workspace-parent (read-only)."""
    bases = allowed_registration_bases()
    if root_kind == "project":
        base = bases[0].resolve()
    elif root_kind == "wsp":
        if len(bases) < 2:
            return {
                "ok": False,
                "error": "Workspace parent not mounted",
                "entries": [],
                "root_kind": root_kind,
                "subpath": "",
            }
        base = bases[1].resolve()
    else:
        return {"ok": False, "error": "root must be project or wsp", "entries": [], "root_kind": root_kind, "subpath": ""}

    rel_clean = ""
    try:
        if subpath.strip():
            segs = _safe_rel_segments(subpath.strip().strip("/"))
            rel_clean = "/".join(segs)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "entries": [], "root_kind": root_kind, "subpath": ""}

    cur = base.joinpath(rel_clean).resolve() if rel_clean else base
    try:
        cur.relative_to(base)
    except ValueError:
        return {"ok": False, "error": "path escapes allowed root", "entries": [], "root_kind": root_kind, "subpath": rel_clean}

    if not cur.is_dir():
        return {"ok": False, "error": "not a directory", "entries": [], "root_kind": root_kind, "subpath": rel_clean}

    entries: list[dict[str, str]] = []
    try:
        for child in sorted(cur.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                child_rel = f"{rel_clean}/{child.name}" if rel_clean else child.name
                if root_kind == "wsp":
                    path_field = f"wsp:{child_rel}"
                else:
                    path_field = child_rel
                entries.append({"name": child.name, "path_field": path_field, "rel": child_rel})
    except OSError as exc:
        return {"ok": False, "error": str(exc), "entries": [], "root_kind": root_kind, "subpath": rel_clean}

    parent_rel = ""
    if rel_clean:
        parent = str(Path(rel_clean).parent)
        parent_rel = "" if parent == "." else parent

    if root_kind == "wsp":
        current_path_field = f"wsp:{rel_clean}" if rel_clean else "wsp:"
    else:
        current_path_field = rel_clean if rel_clean else ""

    return {
        "ok": True,
        "root_kind": root_kind,
        "subpath": rel_clean,
        "parent_subpath": parent_rel,
        "current_path_field": current_path_field,
        "entries": entries[:500],
    }


_MAX_YAML_READ = 256_000


def read_existing_registration_yaml(root: Path) -> tuple[str | None, str | None]:
    """Return raw text of leco.app.yaml and sidecar profile (leco.yaml / localhost.yaml) if present."""
    manifest_text: str | None = None
    loc_text: str | None = None
    mp = root / "leco.app.yaml"
    try:
        if mp.is_file():
            sz = mp.stat().st_size
            if sz <= _MAX_YAML_READ:
                manifest_text = mp.read_text(encoding="utf-8", errors="replace")
    except OSError:
        pass
    for name in ("leco.yaml", "localhost.yaml", "leco.localhost.yaml"):
        lp = root / name
        try:
            if lp.is_file():
                sz = lp.stat().st_size
                if sz <= _MAX_YAML_READ:
                    loc_text = lp.read_text(encoding="utf-8", errors="replace")
                    break
        except OSError:
            pass
    return manifest_text, loc_text


def manifest_has_docker_compose(manifest: dict[str, Any]) -> bool:
    dc = manifest.get("dockerCompose") or manifest.get("docker_compose")
    if not isinstance(dc, dict):
        return False
    if (dc.get("composeFileFromManifest") or dc.get("compose_file_from_manifest") or "").strip():
        return True
    return bool((dc.get("composeFile") or dc.get("compose_file") or "").strip())


def manifest_has_wrangler_config(manifest: dict[str, Any]) -> bool:
    cf = manifest.get("cloudflare")
    if not isinstance(cf, dict):
        return False
    w = cf.get("wranglerConfig") or cf.get("wrangler_config")
    return bool(w and str(w).strip())


def ensure_wrangler_in_profile_infrastructure(
    profile_dict: dict[str, Any],
    orig_root: Path,
    bridge_manifest_dict: dict[str, Any],
    *,
    app_tree_base: Path | None = None,
) -> None:
    """Set or fix ``infrastructure.cloudflare.wranglerConfig`` relative to the app tree (walk-up)."""
    infra = profile_dict.setdefault("infrastructure", {})
    base = (
        app_tree_base.resolve()
        if app_tree_base is not None
        else compute_source_target(orig_root, bridge_manifest_dict).resolve()
    )
    cf = infra.get("cloudflare")
    if isinstance(cf, dict):
        existing = (cf.get("wranglerConfig") or cf.get("wrangler_config") or "").strip()
        if existing:
            try:
                if (base / existing).resolve().is_file():
                    return
            except (OSError, ValueError):
                pass
    rel = _detect_wrangler_relpath_from_base(base)
    if rel is None:
        return
    if isinstance(cf, dict):
        cf["wranglerConfig"] = rel
    else:
        infra["cloudflare"] = {"wranglerConfig": rel}


def ensure_docker_compose_in_profile_infrastructure(
    profile_dict: dict[str, Any],
    orig_root: Path,
    bridge_manifest_dict: dict[str, Any],
    *,
    app_tree_base: Path | None = None,
    allow_compose_discovery: bool = False,
    manifest_parent: Path | None = None,
) -> None:
    """
    Optionally infer ``infrastructure.dockerCompose.composeFile`` by walking up the tree.

    - **Registry / Save** (``allow_compose_discovery=False``): Trust ``leco.yaml`` only. If there is
      no ``dockerCompose`` block, do not inject one (Workers-only apps stay compose-free). If
      ``composeFile`` is set, never replace it when the path fails to resolve (operator path is
      authoritative).
    - **Generate default** (``allow_compose_discovery=True``): When compose is missing or has no
      file path, try to find a compose file under the app tree (convenience for new materialization).
    """
    infra = profile_dict.setdefault("infrastructure", {})
    base = (
        app_tree_base.resolve()
        if app_tree_base is not None
        else compute_source_target(orig_root, bridge_manifest_dict).resolve()
    )
    dc = infra.get("dockerCompose") or infra.get("docker_compose")
    preserved: dict[str, Any] = {}

    def _dir_with_bridge_manifest() -> Path | None:
        if manifest_parent is not None:
            try:
                return manifest_parent.resolve()
            except OSError:
                return None
        o = orig_root.resolve()
        if (o / "leco.app.yaml").is_file():
            return o
        return None

    if isinstance(dc, dict):
        cfm_early = (dc.get("composeFileFromManifest") or dc.get("compose_file_from_manifest") or "").strip()
        if cfm_early:
            md = _dir_with_bridge_manifest()
            if md is not None:
                try:
                    if (md / cfm_early).is_file():
                        # Hosting entry (include + ports !reset) must stay the sole primary -f; never
                        # inject composeFile from walk-up (would re-bind host :80 / :5432).
                        return
                except OSError:
                    pass
        existing = (dc.get("composeFile") or dc.get("compose_file") or "").strip()
        if existing:
            try:
                if (base / existing).resolve().is_file():
                    return
            except (OSError, ValueError):
                pass
            # Explicit path in leco.yaml — do not overwrite with walk-up discovery.
            return
        for k, v in dc.items():
            lk = str(k).lower()
            if lk in ("composefile", "compose_file"):
                continue
            if v is not None and v != []:
                preserved[k] = v

    if not allow_compose_discovery:
        return

    try_root: Path | None = base
    for _ in range(6):
        if try_root is None or not try_root.is_dir():
            break
        files = _list_compose_files(try_root)
        if files:
            rel_primary = _pick_primary_compose(files)
            if rel_primary is not None:
                compose_abs = (try_root / rel_primary).resolve()
                try:
                    rel_to_base = os.path.relpath(str(compose_abs), str(base))
                except ValueError:
                    rel_to_base = ""
                if rel_to_base:
                    out = {**preserved, "composeFile": rel_to_base.replace("\\", "/")}
                    infra["dockerCompose"] = out
                    return
        if try_root.parent == try_root:
            break
        try_root = try_root.parent


def _compose_service_internal_port(spec: dict[str, Any]) -> int | None:
    """Best-effort container port from compose service (prefer expose, then ports target)."""
    expose = spec.get("expose")
    if isinstance(expose, list):
        for item in expose:
            s = str(item).strip()
            if s.isdigit():
                v = int(s)
                if 1 <= v <= 65535:
                    return v
    ports = spec.get("ports")
    if isinstance(ports, list):
        for p in ports:
            s = str(p).strip()
            if not s:
                continue
            # Examples: "3000:3000", "127.0.0.1:8001:8001", "3000"
            if "/" in s:
                s = s.split("/", 1)[0]
            parts = [x for x in s.split(":") if x]
            cand = parts[-1] if parts else s
            if cand.isdigit():
                v = int(cand)
                if 1 <= v <= 65535:
                    return v
    return None


def _compose_project_name(dc: dict[str, Any], host_slug: str) -> str:
    raw = (dc.get("projectName") or dc.get("project_name") or "").strip()
    if raw:
        return raw
    return host_slug


def _compose_service_backend_host(
    service_name: str,
    spec: dict[str, Any],
    project_name: str,
) -> str:
    """Resolve a stable backend host on lh-network for a compose service.

    Default Docker Compose (v2+) names containers ``{project}-{service}-{replica}``
    (e.g. ``cv-frontend-1``). Traefik on ``lh-network`` must use that DNS name, not
    the shortened ``cv-frontend`` form.
    """
    cn = (spec.get("container_name") or "").strip()
    if cn:
        return cn
    nets = spec.get("networks")
    if isinstance(nets, dict):
        for net_name, net_cfg in nets.items():
            if str(net_name).strip() != "lh-network":
                continue
            if isinstance(net_cfg, dict):
                aliases = net_cfg.get("aliases")
                if isinstance(aliases, list):
                    for a in aliases:
                        sa = str(a).strip()
                        if sa:
                            return sa
    return f"{project_name}-{service_name}-1"


_LECO_HOSTING_OVERLAY_COMPOSE = "docker-compose.leco-hosting.yml"


def _primary_public_hostname_from_routing(entries: list[Any]) -> str | None:
    for row in entries:
        if not isinstance(row, dict):
            continue
        hn = str(row.get("hostname") or "").strip()
        if hn.endswith(".lh"):
            return hn
    return None


def _lh_overlay_env_for_service(service_name: str, public_hostname: str) -> dict[str, str]:
    """
    Standard env merges for split Traefik apps: browser uses same origin on *.lh; API allows CORS.
    Framework-specific keys are additive (unset backend URL lets CrawlerVision api.js use origin).
    """
    origin_https = f"https://{public_hostname}"
    origin_http = f"http://{public_hostname}"
    sk = str(service_name)
    out: dict[str, str] = {}
    if sk in ("frontend", "web", "ui", "app", "nginx"):
        out["REACT_APP_SITE_URL"] = origin_https
        out["REACT_APP_BACKEND_URL"] = ""
        out["VITE_API_URL"] = ""
    if sk in ("backend", "api", "server"):
        out["CORS_ORIGINS"] = (
            f"{origin_https},{origin_http},http://localhost:3000,http://127.0.0.1:3000"
        )
        out["FRONTEND_URL"] = origin_https
        out["SITE_URL"] = origin_https
        out["GEO_IP_DEV_COUNTRY"] = "${GEO_IP_DEV_COUNTRY:-US}"
    return out


def _service_spec_uses_lh_network(spec: dict[str, Any]) -> bool:
    """True if the compose service is attached to external ``lh-network`` (Traefik edge)."""
    nets = spec.get("networks")
    if nets is None:
        return False
    if isinstance(nets, list):
        return any(str(x).strip() == "lh-network" for x in nets)
    if isinstance(nets, dict):
        return any(str(k).strip() == "lh-network" for k in nets)
    return False


def _service_name_from_traefik_compose_host(
    host: str,
    project_name: str,
    services: dict[str, Any],
) -> str | None:
    """
    Map a Traefik ``loadBalancer`` host (compose default DNS or ``container_name``) back to a
    compose service key so we can attach ``lh-network`` via a hosting overlay merge file.
    """
    return _compose_service_key_from_routing_host(host, project_name, services)


def _compose_services_needing_lh_network_overlay(
    routing_entries: list[Any],
    project_name: str,
    services: dict[str, Any],
) -> list[str]:
    """Compose service keys referenced by Traefik routing that are not yet on ``lh-network``."""
    names: set[str] = set()
    for row in routing_entries:
        if not isinstance(row, dict):
            continue
        fe = row.get("frontend")
        ab = row.get("apiBackend")
        if isinstance(fe, dict) and isinstance(ab, dict):
            for h in (fe.get("host"), ab.get("host")):
                sn = _service_name_from_traefik_compose_host(str(h or ""), project_name, services)
                if sn:
                    names.add(sn)
            continue
        bh = str(row.get("backendHost") or "").strip()
        if bh:
            sn = _service_name_from_traefik_compose_host(bh, project_name, services)
            if sn:
                names.add(sn)
    need: list[str] = []
    for s in sorted(names):
        spec = services.get(s)
        if isinstance(spec, dict) and not _service_spec_uses_lh_network(spec):
            need.append(s)
    return need


def _compose_service_key_from_routing_host(
    host: str,
    project_name: str,
    services: dict[str, Any],
) -> str | None:
    """
    Resolve a Traefik/backend hostname to a compose *service* key.

    Handles default DNS (``{project}-{service}-1``), bare service names, ``container_name``,
    and stale hosts pasted from another stack (wrong project prefix, e.g. ``cv-frontend`` for
    project ``cvision``).
    """
    h = (host or "").strip()
    if not h:
        return None
    pn = str(project_name).strip()
    for sk_raw in sorted(services.keys(), key=lambda x: len(str(x)), reverse=True):
        sk = str(sk_raw)
        spec = services.get(sk_raw)
        if not isinstance(spec, dict):
            continue
        if h == sk:
            return sk
        if h == f"{pn}-{sk}-1" or h == f"{pn}-{sk}":
            return sk
        cn = (spec.get("container_name") or "").strip()
        if cn and h == cn:
            return sk
        suf1 = f"-{sk}-1"
        if len(h) > len(suf1) and h.endswith(suf1):
            prefix = h[: -len(suf1)]
            if prefix:
                return sk
        suf2 = f"-{sk}"
        if len(h) > len(suf2) and h.endswith(suf2) and not h.endswith(suf1):
            prefix = h[: -len(suf2)]
            if prefix:
                return sk
    return None


def _remap_stale_compose_dns_host(
    host: str,
    project_name: str,
    services: dict[str, Any],
) -> str | None:
    """If *host* is not the canonical DNS name for its service under *project_name*, return canonical."""
    h = (host or "").strip()
    if not h:
        return None
    sk = _compose_service_key_from_routing_host(h, project_name, services)
    if not sk:
        return None
    spec = services.get(sk)
    if not isinstance(spec, dict):
        return None
    canonical = _compose_service_backend_host(sk, spec, project_name)
    if h == canonical:
        return None
    cn = (spec.get("container_name") or "").strip()
    if cn and h == cn:
        return None
    return canonical


def _load_compose_services_for_localhost(
    localhost: dict[str, Any],
    root: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    base = compute_source_target(root, manifest).resolve()
    infra = localhost.get("infrastructure")
    if not isinstance(infra, dict):
        return None
    dc = infra.get("dockerCompose")
    if not isinstance(dc, dict):
        return None
    cf = (dc.get("composeFile") or dc.get("compose_file") or "").strip()
    if not cf:
        return None
    compose_abs = (base / cf).resolve()
    if not compose_abs.is_file():
        return None
    try:
        raw = yaml.safe_load(compose_abs.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    services = raw.get("services")
    if not isinstance(services, dict):
        return None
    return services, dc, infra


def _infer_compose_routing_entries(
    localhost: dict[str, Any], root: Path, manifest: dict[str, Any], host_slug: str
) -> list[dict[str, Any]]:
    """
    Default routing for compose apps with separate UI + API containers:
      - Single hostname ``<slug>.lh`` with ``apiPathPrefix`` (default ``/api``): Traefik sends
        ``PathPrefix`` traffic to the API container and the rest to the frontend (same pattern
        as ``leco-app traefik-fragment`` split mode and default ``urls``).
    """
    loaded = _load_compose_services_for_localhost(localhost, root, manifest)
    if not loaded:
        return []
    services, dc, _infra = loaded
    project_name = _compose_project_name(dc, host_slug)

    def _pick_service(candidates: tuple[str, ...]) -> tuple[str, int, str] | None:
        keys = {str(k).lower(): str(k) for k in services.keys()}
        for c in candidates:
            k = keys.get(c.lower())
            if not k:
                continue
            spec = services.get(k)
            if not isinstance(spec, dict):
                continue
            port = _compose_service_internal_port(spec)
            if port is None:
                continue
            return k, port, _compose_service_backend_host(k, spec, project_name)
        return None

    frontend = _pick_service(("frontend", "web", "ui", "app", "nginx"))
    backend = _pick_service(("backend", "api", "server"))
    if not backend:
        backend = _pick_service(("app",))
    if not frontend or not backend:
        return []
    if frontend[0] == backend[0]:
        return []
    return [
        {
            "hostname": f"{host_slug}.lh",
            "apiPathPrefix": "/api",
            "frontend": {"host": frontend[2], "port": frontend[1]},
            "apiBackend": {"host": backend[2], "port": backend[1]},
        }
    ]


def _normalize_compose_routing_backend_hosts(
    localhost: dict[str, Any],
    root: Path,
    manifest: dict[str, Any],
    host_slug: str,
) -> int:
    """
    Replace ambiguous backend hosts like ``frontend``/``backend`` with app-scoped compose hosts,
    fix ``{project}-{service}`` values missing the Compose replica suffix (``-1``), and fix
    stale hostnames copied from another stack (e.g. ``cv-frontend`` under project ``cvision``).
    Returns number of routing entries updated.
    """
    loaded = _load_compose_services_for_localhost(localhost, root, manifest)
    if not loaded:
        return 0
    services, dc, infra = loaded
    routing = infra.get("routing")
    if not isinstance(routing, dict):
        return 0
    entries = routing.get("entries")
    if not isinstance(entries, list) or not entries:
        return 0
    project_name = _compose_project_name(dc, host_slug)
    meta_changed = False
    if not (dc.get("projectName") or dc.get("project_name")):
        dc["projectName"] = host_slug
        meta_changed = True
    updated = 0
    for row in entries:
        if not isinstance(row, dict):
            continue
        bh = str(row.get("backendHost") or "").strip()
        if bh:
            remapped_b = _remap_stale_compose_dns_host(bh, project_name, services)
            if remapped_b and remapped_b != bh:
                row["backendHost"] = remapped_b
                updated += 1
        for key in ("frontend", "apiBackend"):
            tgt = row.get(key)
            if not isinstance(tgt, dict):
                continue
            th = str(tgt.get("host") or "").strip()
            if not th:
                continue
            remapped = _remap_stale_compose_dns_host(th, project_name, services)
            if remapped and remapped != th:
                tgt["host"] = remapped
                updated += 1
    return updated + (1 if meta_changed else 0)


def _apply_default_routing(localhost: dict[str, Any], root: Path, manifest: dict[str, Any], host_slug: str, *, has_wrangler: bool) -> None:
    """Set routing defaults only when profile has no explicit routing."""
    infra = localhost.get("infrastructure")
    if not isinstance(infra, dict):
        return
    existing = infra.get("routing")
    if isinstance(existing, dict):
        entries = existing.get("entries")
        if isinstance(entries, list) and entries:
            return

    compose_entries = _infer_compose_routing_entries(localhost, root, manifest, host_slug)
    if compose_entries:
        infra["routing"] = {"entries": compose_entries}
        return
    if has_wrangler:
        infra["routing"] = {
            "entries": [
                {
                    "hostname": f"{host_slug}.lh",
                    "apiPathPrefix": "/api",
                    "frontend": {"host": "workers-runtime", "port": 8787},
                    "apiBackend": {"host": "workers-runtime", "port": 8787},
                }
            ]
        }


def ensure_wrangler_in_manifest(manifest: dict[str, Any], app_root: Path) -> None:
    """
    If ``wrangler.toml`` (or ``cloudflare/wrangler.toml``) exists under app_root but the manifest
    has no ``cloudflare.wranglerConfig``, set it. Used on register so Wrangler-only trees get a
    correct manifest without hand-editing.
    """
    if manifest_has_wrangler_config(manifest):
        return
    rel = _detect_wrangler(app_root.resolve())
    if rel is None:
        return
    key = rel.as_posix()
    existing = manifest.get("cloudflare")
    if isinstance(existing, dict):
        existing["wranglerConfig"] = key
    else:
        manifest["cloudflare"] = {"wranglerConfig": key}


def ensure_docker_compose_in_manifest(manifest: dict[str, Any], orig_root: Path) -> None:
    """
    If manifest has no dockerCompose, walk up from the manifest logical root for compose
    (e.g. registered from .../cloudflare with root ``..`` → repo root, or root ``.`` with compose in parent).
    ``composeFile`` is relative to :func:`hosting_layout.compute_source_target` (same as leco-app ``resolved_root``).
    """
    if manifest_has_docker_compose(manifest):
        return
    base = compute_source_target(orig_root, manifest).resolve()
    try_root: Path | None = base
    for _ in range(5):
        if try_root is None or not try_root.is_dir():
            break
        files = _list_compose_files(try_root)
        if files:
            rel_primary = _pick_primary_compose(files)
            if rel_primary is not None:
                compose_abs = (try_root / rel_primary).resolve()
                try:
                    rel_to_base = os.path.relpath(str(compose_abs), str(base))
                except ValueError:
                    rel_to_base = ""
                if rel_to_base:
                    manifest["dockerCompose"] = {"composeFile": rel_to_base.replace("\\", "/")}
                    return
        if try_root.parent == try_root:
            break
        try_root = try_root.parent


_CONFIG_REF_RESOLVED_KEYS = (
    "wranglerConfig",
    "dockerComposeFile",
    "composeOverrideFile",
    "envFile",
    "dockerfile",
    "packageJson",
    "wordpressConfigPhp",
    "nginxConfig",
    "varnishVcl",
    "phpFpmPool",
    "mysqlInit",
    "mongoInit",
    "redisConfig",
)


def _dashboard_project_root_for_paths() -> str:
    return (os.getenv("DASHBOARD_PROJECT_ROOT") or os.getenv("LECO_ECOSYSTEM_ROOT") or "").strip()


def _path_if_exists(p: Path) -> str | None:
    try:
        r = p.resolve()
        if r.exists():
            return str(r)
    except OSError:
        return None
    return None


def _resolved_path_for_config_ref(raw: str, *, app_root: Path) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    p = Path(s)
    if p.is_absolute():
        hit = _path_if_exists(p)
        if hit:
            return hit
        sp = str(p)
        if sp.startswith("/project/") or sp == "/project":
            root = _dashboard_project_root_for_paths()
            if root:
                suffix = sp[len("/project") :].lstrip("/\\")
                alt = Path(root) / suffix if suffix else Path(root)
                hit2 = _path_if_exists(alt)
                if hit2:
                    return hit2
        return None
    try:
        joined = (app_root / p).resolve()
    except OSError:
        return None
    return _path_if_exists(joined)


def compute_resolved_paths_for_leco_app_manifest(
    manifest: dict[str, Any],
    manifest_path: Path,
    profile_from_file: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build ``resolvedPaths`` for ``leco.app.yaml``: absolute host paths for root, manifest, profile, configs."""
    mp = manifest_path.resolve()
    try:
        from leco_app.schema import ApplicationManifest as _AM

        am = _AM.model_validate(manifest)
        app_root = am.resolved_root(mp)
    except Exception:
        app_root = compute_source_target(mp.parent, manifest)
    try:
        app_root = app_root.resolve()
    except OSError:
        pass

    out: dict[str, str] = {
        "sourceRoot": str(app_root),
        "manifestPath": str(mp),
    }

    lhp_raw = manifest.get("localHostProfile") or manifest.get("local_host_profile") or "leco.yaml"
    if isinstance(lhp_raw, str) and lhp_raw.strip():
        lp = Path(lhp_raw.strip())
        try:
            prof_path = lp.resolve() if lp.is_absolute() else (mp.parent / lp).resolve()
        except OSError:
            prof_path = mp.parent / lp
        pr = _path_if_exists(prof_path)
        if pr:
            out["localHostProfile"] = pr

    cfg = manifest.get("configRefs") or manifest.get("config_refs")
    if isinstance(cfg, dict):
        for key in _CONFIG_REF_RESOLVED_KEYS:
            v = cfg.get(key)
            if not isinstance(v, str) or not v.strip():
                continue
            rp = _resolved_path_for_config_ref(v, app_root=app_root)
            if rp:
                out[key] = rp

    mcf = manifest.get("cloudflare")
    if isinstance(mcf, dict) and "wranglerConfig" not in out:
        wr = mcf.get("wranglerConfig") or mcf.get("wrangler_config")
        if isinstance(wr, str) and wr.strip():
            rp = _resolved_path_for_config_ref(wr, app_root=app_root)
            if rp:
                out["wranglerConfig"] = rp

    prof = profile_from_file if isinstance(profile_from_file, dict) else {}
    infra = prof.get("infrastructure")
    if isinstance(infra, dict):
        dc = infra.get("dockerCompose") or infra.get("docker_compose")
        if isinstance(dc, dict):
            if "dockerComposeFile" not in out:
                cf = dc.get("composeFile") or dc.get("compose_file")
                if isinstance(cf, str) and cf.strip():
                    rp = _resolved_path_for_config_ref(cf, app_root=app_root)
                    if rp:
                        out["dockerComposeFile"] = rp
            if "envFile" not in out:
                ef = dc.get("envFile") or dc.get("env_file")
                if isinstance(ef, str) and ef.strip():
                    rp = _resolved_path_for_config_ref(ef, app_root=app_root)
                    if rp:
                        out["envFile"] = rp
        cfb = infra.get("cloudflare")
        if isinstance(cfb, dict) and "wranglerConfig" not in out:
            wr = cfb.get("wranglerConfig") or cfb.get("wrangler_config")
            if isinstance(wr, str) and wr.strip():
                rp = _resolved_path_for_config_ref(wr, app_root=app_root)
                if rp:
                    out["wranglerConfig"] = rp

    return out


def fill_resolved_paths_in_manifest(
    manifest: dict[str, Any],
    manifest_path: Path,
    profile_from_file: dict[str, Any] | None = None,
) -> None:
    """Set ``manifest['resolvedPaths']`` for YAML persistence."""
    manifest["resolvedPaths"] = compute_resolved_paths_for_leco_app_manifest(
        manifest, manifest_path, profile_from_file
    )


def build_default_manifest_and_localhost(
    root: Path,
    app_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate bridge manifest (v3) + ``leco.yaml`` dict with ``infrastructure`` as source of truth."""
    host_slug = host_slug_from_app_id(app_id)
    main_urls = main_urls_from_app_id(app_id)
    api_urls = {
        "https": f"https://{host_slug}.lh/api",
        "http": f"http://{host_slug}.lh/api",
    }
    scan = scan_app_directory(root)
    compose_files = scan.get("compose_files") or []
    compose_file = compose_files[0] if compose_files else None
    compose_file_str = ""
    if isinstance(compose_file, Path):
        compose_file_str = compose_file.as_posix()
    elif isinstance(compose_file, str):
        compose_file_str = compose_file.strip()
    env_file = None
    r = root.resolve()
    if (r / "docker" / ".env").is_file() or (r / "docker" / "env.example").is_file():
        env_file = "docker/.env"

    manifest: dict[str, Any] = {
        "lecoAppVersion": "3",
        "name": app_id,
        "root": ".",
        "localHostProfile": "leco.yaml",
    }

    config_refs: dict[str, str] = {}
    if scan.get("wrangler_config"):
        config_refs["wranglerConfig"] = str(scan["wrangler_config"])
    if compose_file_str:
        config_refs["dockerComposeFile"] = compose_file_str
    if (r / ".env").is_file():
        config_refs["envFile"] = ".env"
    elif env_file:
        config_refs["envFile"] = env_file
    if (r / "docker-compose.override.yml").is_file():
        config_refs["composeOverrideFile"] = "docker-compose.override.yml"
    if (r / "Dockerfile").is_file():
        config_refs["dockerfile"] = "Dockerfile"
    if (r / "package.json").is_file():
        config_refs["packageJson"] = "package.json"
    if (r / "wp-config.php").is_file():
        config_refs["wordpressConfigPhp"] = "wp-config.php"
    for cand in ("nginx.conf", "docker/nginx/default.conf", "config/nginx.conf"):
        if (r / cand).is_file():
            config_refs["nginxConfig"] = cand.replace("\\", "/")
            break
    for cand in ("default.vcl", "docker/varnish/default.vcl", "config/varnish/default.vcl"):
        if (r / cand).is_file():
            config_refs["varnishVcl"] = cand.replace("\\", "/")
            break
    for cand in ("docker/mysql/init", "mysql/init", "database/init"):
        if (r / cand).is_dir() or (r / cand).is_file():
            config_refs["mysqlInit"] = cand.replace("\\", "/")
            break
    for cand in ("docker/mongo/init", "mongo/init"):
        if (r / cand).is_dir():
            config_refs["mongoInit"] = cand.replace("\\", "/")
            break
    for cand in ("redis.conf", "docker/redis/redis.conf"):
        if (r / cand).is_file():
            config_refs["redisConfig"] = cand.replace("\\", "/")
            break
    if config_refs:
        manifest["configRefs"] = config_refs

    infrastructure: dict[str, Any] = {}
    if compose_file_str:
        dc: dict[str, Any] = {"composeFile": compose_file_str}
        dc["projectName"] = host_slug
        if env_file:
            dc["envFile"] = env_file
        infrastructure["dockerCompose"] = dc
    has_wrangler = bool(scan.get("has_wrangler") and scan.get("wrangler_config"))
    if has_wrangler:
        cf: dict[str, Any] = {"wranglerConfig": scan["wrangler_config"]}
        pfx = _suggest_local_cf_public_prefix(app_id)
        if pfx:
            cf["localCfPublicPrefix"] = pfx
        infrastructure["cloudflare"] = cf

    localhost: dict[str, Any] = {
        "schemaVersion": 2,
        "archetype": scan.get("suggested_archetype") or "generic",
        "infrastructure": infrastructure,
        "urls": [
            {"role": "frontend", "label": "Main app (HTTPS)", "publicUrl": main_urls["https"]},
            {"role": "frontend", "label": "Main app (HTTP)", "publicUrl": main_urls["http"]},
            {"role": "api", "label": "API (HTTPS)", "publicUrl": api_urls["https"]},
            {"role": "api", "label": "API (HTTP)", "publicUrl": api_urls["http"]},
        ],
        "lifecycle": {"prepare": [], "build": [], "preStart": []},
        "notes": (
            "Next: open Hosted apps → Register (or run leco-app ecosystem-register) so "
            "config/leco-registry.yaml lists this app — the tab then shows Deploy / Remove and the manifest summary. "
            "Optional hosting-only compose: docker-compose.leco-hosting.yml beside leco.app.yaml + "
            "infrastructure.dockerCompose.additionalComposeFilesFromManifest."
        ),
    }

    ensure_docker_compose_in_profile_infrastructure(
        localhost, root, manifest, allow_compose_discovery=True
    )
    ensure_wrangler_in_profile_infrastructure(localhost, root, manifest)
    _apply_default_routing(localhost, root, manifest, host_slug, has_wrangler=has_wrangler)
    _normalize_compose_routing_backend_hosts(localhost, root, manifest, host_slug)
    enrich_infrastructure_wrangler_binding_preview(localhost.get("infrastructure") or {}, root)

    fill_resolved_paths_in_manifest(manifest, root / "leco.app.yaml", localhost)

    return manifest, localhost


def enrich_infrastructure_wrangler_binding_preview(infrastructure: dict[str, Any], app_root: Path) -> None:
    """
    Fill ``infrastructure.wranglerBindingPreview`` from ``wrangler.toml`` so operators see KV/R2/D1 rows.
    Provisioning still uses the wrangler file (single source of truth).
    """
    if not isinstance(infrastructure, dict):
        return
    cf = infrastructure.get("cloudflare")
    if not isinstance(cf, dict):
        return
    wr = cf.get("wranglerConfig") or cf.get("wrangler_config")
    if not wr or not str(wr).strip():
        return
    wp = (app_root.resolve() / str(wr).strip()).resolve()
    if not wp.is_file():
        return
    env_raw = cf.get("wranglerEnv")
    wenv = str(env_raw).strip() if isinstance(env_raw, str) and str(env_raw).strip() else None
    try:
        from leco_app.wrangler_cf_resources import parse_wrangler_cf_resources

        plan = parse_wrangler_cf_resources(wp, wenv)
    except Exception:
        return
    infrastructure["wranglerBindingPreview"] = {
        "note": (
            "Each kv[] row becomes one local KV namespace; each r2[] one R2 bucket; each d1[] one D1 database. "
            "leco-app reads wrangler.toml (cloudflare.wranglerConfig) — this block is an informational mirror."
        ),
        "wranglerEnv": wenv or "",
        "kv": [{"binding": r.binding, "cfId": r.cf_id} for r in plan.kv],
        "r2": [{"binding": r.binding, "bucketName": r.bucket_name} for r in plan.r2],
        "d1": [{"binding": r.binding, "databaseName": r.database_name} for r in plan.d1],
    }


def preview_registration_yaml(root: Path, app_id: str) -> tuple[str, str]:
    """YAML strings for dashboard detect preview (same defaults as register without custom YAML)."""
    m, lo = build_default_manifest_and_localhost(root, slugify_app_id(app_id))
    dump_kw: dict[str, Any] = {
        "default_flow_style": False,
        "sort_keys": False,
        "allow_unicode": True,
    }
    return yaml.safe_dump(m, **dump_kw), yaml.safe_dump(lo, **dump_kw)


def ensure_lh_network_hosting_overlay(manifest_abs: Path) -> dict[str, Any]:
    """
    Ensure a hosting-only merge file exists beside ``leco.app.yaml`` and that
    ``dockerCompose.additionalComposeFilesFromManifest`` references it.

    The generated overlay does two things for hosted apps:
    - attach Traefik-targeted services to ``lh-network`` so ``*.lh`` routes work
    - strip upstream host ``ports`` publishes with ``!reset`` so hosted apps do not collide with
      Traefik / local databases on ``:80`` / ``:3000`` / ``:5432`` / ...
    """
    mp = manifest_abs.resolve()
    try:
        manifest = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"ok": False, "error": "read manifest"}
    if not isinstance(manifest, dict):
        return {"ok": False, "error": "invalid manifest"}
    prof = (manifest.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
    prof_path = (mp.parent / prof).resolve()
    try:
        localhost = yaml.safe_load(prof_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"ok": False, "error": "read localhost profile"}
    if not isinstance(localhost, dict):
        return {"ok": False, "error": "invalid localhost profile"}
    host_slug = host_slug_from_app_id(str(manifest.get("name") or mp.parent.name))
    infra_pre = localhost.get("infrastructure")
    if isinstance(infra_pre, dict):
        dc_pre = infra_pre.get("dockerCompose") or infra_pre.get("docker_compose")
        if isinstance(dc_pre, dict):
            cfm0 = (dc_pre.get("composeFileFromManifest") or dc_pre.get("compose_file_from_manifest") or "").strip()
            if cfm0 and (mp.parent / cfm0).is_file():
                return {"ok": True, "skipped": "composeFileFromManifest — lh-network belongs in hosting entry compose"}

    loaded = _load_compose_services_for_localhost(localhost, mp.parent, manifest)
    if not loaded:
        return {"ok": True, "skipped": "no compose file or services"}
    services, dc, infra = loaded
    routing = infra.get("routing")
    if not isinstance(routing, dict):
        return {"ok": True, "skipped": "no routing"}
    entries = routing.get("entries")
    if not isinstance(entries, list) or not entries:
        return {"ok": True, "skipped": "no routing entries"}
    project_name = _compose_project_name(dc, host_slug)
    port_reset_services = [
        str(name)
        for name, spec in services.items()
        if isinstance(spec, dict) and _service_has_publishable_ports(spec)
    ]
    need = _compose_services_needing_lh_network_overlay(entries, project_name, services)
    if not need and not port_reset_services:
        return {"ok": True, "skipped": "no lh-network or host-port overlay changes needed"}
    need_set = set(need)
    port_reset_set = set(port_reset_services)

    overlay_path = mp.parent / _LECO_HOSTING_OVERLAY_COMPOSE
    profile_dirty = False
    if not overlay_path.is_file():
        public_hn = _primary_public_hostname_from_routing(entries)
        header = (
            "# LEco hosting overlay — attaches Traefik upstream services to the ecosystem edge network.\n"
            "# Referenced from leco.yaml → infrastructure.dockerCompose.additionalComposeFilesFromManifest.\n"
            "# Includes *.lh-oriented env (CORS, REACT_APP_*) when a routing hostname ends with .lh.\n"
            "# Also strips upstream host publishes with ports: !reset [] so hosted apps do not collide\n"
            "# with Traefik or other local stacks on :80 / :3000 / :5432 / ... .\n"
            "# Safe to commit under hosting/app-available/<slug>/ without editing the upstream app repo.\n\n"
        )
        lines = [header, "services:\n"]
        overlay_services = sorted(need_set | port_reset_set, key=lambda x: x.lower())
        for s in overlay_services:
            lines.append(f"  {s}:\n")
            if s in port_reset_set:
                lines.append("    ports: !reset []\n")
            if s in need_set:
                lines.append("    networks:\n")
                lines.append("      - lh-network\n")
                if public_hn:
                    env = _lh_overlay_env_for_service(s, public_hn)
                    if env:
                        lines.append("    environment:\n")
                        for k, v in env.items():
                            vv = yaml.safe_dump(v, default_flow_style=True, allow_unicode=True).strip()
                            lines.append(f"      {k}: {vv}\n")
            lines.append("\n")
        if need_set:
            lines.append("networks:\n")
            lines.append("  lh-network:\n")
            lines.append("    external: true\n")
        try:
            overlay_path.write_text(
                "".join(lines),
                encoding="utf-8",
            )
        except OSError:
            return {"ok": False, "error": "write overlay"}
        profile_dirty = True

    if not isinstance(infra.get("dockerCompose"), dict):
        infra["dockerCompose"] = dc
    raw_ex = (
        dc.get("additionalComposeFilesFromManifest")
        or dc.get("additional_compose_files_from_manifest")
        or []
    )
    extras = [str(x).strip() for x in raw_ex if str(x).strip()]
    if _LECO_HOSTING_OVERLAY_COMPOSE not in extras:
        extras.append(_LECO_HOSTING_OVERLAY_COMPOSE)
        dc["additionalComposeFilesFromManifest"] = extras
        if "additional_compose_files_from_manifest" in dc:
            del dc["additional_compose_files_from_manifest"]
        profile_dirty = True

    if profile_dirty:
        try:
            prof_path.write_text(
                yaml.safe_dump(localhost, default_flow_style=False, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError:
            return {"ok": False, "error": "write profile"}
    return {
        "ok": True,
        "overlay_path": str(overlay_path),
        "profile_path": str(prof_path),
        "services": need,
        "ports_reset_services": port_reset_services,
        "profile_updated": profile_dirty,
    }


def normalize_profile_compose_backend_hosts(manifest_abs: Path) -> dict[str, Any]:
    """
    Auto-heal routing host collisions for multi-app setups.
    Rewrites localhost.routing.entries backendHost values when they point to ambiguous compose service names.
    """
    mp = manifest_abs.resolve()
    try:
        manifest = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"ok": False, "updated": 0}
    if not isinstance(manifest, dict):
        return {"ok": False, "updated": 0}
    prof = (manifest.get("localHostProfile") or "leco.yaml").strip() or "leco.yaml"
    prof_path = (mp.parent / prof).resolve()
    try:
        localhost = yaml.safe_load(prof_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {"ok": False, "updated": 0}
    if not isinstance(localhost, dict):
        return {"ok": False, "updated": 0}
    host_slug = host_slug_from_app_id(str(manifest.get("name") or mp.parent.name))
    updated = _normalize_compose_routing_backend_hosts(localhost, mp.parent, manifest, host_slug)
    if updated <= 0:
        return {"ok": True, "updated": 0, "profile_path": str(prof_path)}
    try:
        prof_path.write_text(
            yaml.safe_dump(localhost, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError:
        return {"ok": False, "updated": 0}
    return {"ok": True, "updated": updated, "profile_path": str(prof_path)}


def register_yaml_samples() -> list[dict[str, Any]]:
    """Preset YAML pairs for the registration wizard (documentation-oriented)."""
    return [
        {
            "id": "compose-minimal",
            "title": "Compose — bridge + infra in leco.yaml (v3)",
            "description": "leco.app.yaml is only the bridge; dockerCompose lives under leco.yaml infrastructure.",
            "manifest_yaml": """lecoAppVersion: "3"
name: my-app
root: "."
localHostProfile: leco.yaml
configRefs:
  dockerComposeFile: docker-compose.yml
  envFile: .env
""",
            "localhost_yaml": """schemaVersion: 2
archetype: generic
infrastructure:
  dockerCompose:
    composeFile: docker-compose.yml
urls: []
lifecycle:
  prepare: []
  build: []
  preStart: []
notes: "Edit urls and lifecycle commands for your stack."
""",
        },
        {
            "id": "wrangler-worker",
            "title": "Cloudflare Worker (Wrangler)",
            "description": "Wrangler + optional compose under leco.yaml infrastructure; leco.app.yaml is the bridge.",
            "manifest_yaml": """lecoAppVersion: "3"
name: my-worker
root: "."
localHostProfile: leco.yaml
configRefs:
  wranglerConfig: wrangler.toml
""",
            "localhost_yaml": """schemaVersion: 2
archetype: generic
infrastructure:
  cloudflare:
    wranglerConfig: wrangler.toml
    wranglerEnv: ""
urls:
  - role: other
    label: Worker (production)
    publicUrl: https://example.workers.dev
lifecycle:
  prepare: []
  build: []
  preStart: []
notes: ""
""",
        },
        {
            "id": "nextjs-node",
            "title": "Next.js / Node — lifecycle example",
            "description": "leco.yaml with npm ci prepare step; compose path under infrastructure.",
            "manifest_yaml": """lecoAppVersion: "3"
name: web-app
root: "."
localHostProfile: leco.yaml
configRefs:
  dockerComposeFile: docker-compose.yml
  packageJson: package.json
""",
            "localhost_yaml": """schemaVersion: 2
archetype: nextjs
infrastructure:
  dockerCompose:
    composeFile: docker-compose.yml
urls:
  - role: frontend
    label: App
    publicUrl: http://localhost:3000
lifecycle:
  prepare:
    - command: npm ci
      shell: true
      timeoutSec: 600
  build: []
  preStart: []
notes: ""
""",
        },
        {
            "id": "wordpress",
            "title": "WordPress — URLs table",
            "description": "Archetype + frontend/admin URLs; add routing under leco.yaml infrastructure when needed.",
            "manifest_yaml": """lecoAppVersion: "3"
name: wordpress-site
root: "."
localHostProfile: leco.yaml
""",
            "localhost_yaml": """schemaVersion: 2
archetype: wordpress
infrastructure:
  dockerCompose:
    composeFile: docker-compose.yml
urls:
  - role: frontend
    label: Site
    publicUrl: https://wp.lh
  - role: admin
    label: WP Admin
    publicUrl: https://wp.lh/wp-admin
lifecycle:
  prepare: []
  build: []
  preStart: []
notes: ""
""",
        },
    ]


def _service_has_publishable_ports(spec: Any) -> bool:
    if not isinstance(spec, dict):
        return False
    ports = spec.get("ports")
    if ports is None:
        return False
    if isinstance(ports, dict):
        return bool(ports)
    if isinstance(ports, list):
        return len(ports) > 0
    return bool(ports)


def infer_single_backend_routing_from_services(
    services: dict[str, Any],
    host_slug: str,
    project_name: str,
) -> list[dict[str, Any]] | None:
    """One Traefik legacy route (single backend) for stacks like Headwind (hmdm → 8080)."""
    candidates = ("hmdm", "web", "frontend", "app", "ui", "nginx", "portal", "server")
    keys_lower = {str(k).lower(): str(k) for k in services}
    for c in candidates:
        k = keys_lower.get(c.lower())
        if not k:
            continue
        spec = services.get(k)
        if not isinstance(spec, dict):
            continue
        port = _compose_service_internal_port(spec)
        if port is None:
            continue
        bh = _compose_service_backend_host(k, spec, project_name)
        return [{"hostname": f"{host_slug}.lh", "backendHost": bh, "backendPort": port}]
    for k, spec in services.items():
        if not isinstance(spec, dict):
            continue
        port = _compose_service_internal_port(spec)
        if port not in (8080, 80, 8443, 443, 3000, 8000):
            continue
        bh = _compose_service_backend_host(str(k), spec, project_name)
        return [{"hostname": f"{host_slug}.lh", "backendHost": bh, "backendPort": port}]
    return None


_ENTRY_COMPOSE_NAME = "docker-compose.leco-entry.yml"


def ensure_hosting_compose_entry_for_register(manifest_abs: Path) -> dict[str, Any]:
    """
    Opt-in helper (not auto-called by register).
    
    If leco.yaml only uses composeFile against upstream compose that publishes host ports, write
    ``docker-compose.leco-entry.yml`` (include + ``ports: !reset []`` + ``lh-network``) and switch
    the profile to ``composeFileFromManifest``. Adds ``routing.entries`` when missing so Traefik
    merge runs. Idempotent when a composeFileFromManifest entry file already exists.
    
    Destructive: replaces the entire ``dockerCompose`` block on the localhost profile (drops
    ``additionalComposeFiles*``, ``profiles``, etc.) and removes ``cloudflare`` when
    ``wranglerConfig`` is empty. The register wizard does not call this anymore; copy the sample
    under ``hosting/samples/sample-hosting-compose-entry/`` and edit ``leco.yaml`` by hand when
    you need this pattern.
    """
    mp = manifest_abs.resolve()
    if not mp.is_file():
        return {"applied": False, "reason": "manifest not found"}
    try:
        from leco_app.schema import load_effective_manifest
    except ImportError:
        return {"applied": False, "reason": "leco_app.schema not available"}

    try:
        m = load_effective_manifest(mp)
    except Exception as exc:
        return {"applied": False, "reason": f"effective manifest: {exc}"}

    if not m.docker_compose:
        return {"applied": False, "reason": "no dockerCompose"}

    dc = m.docker_compose
    cfm = (dc.compose_file_from_manifest or "").strip()
    if cfm and (mp.parent / cfm).is_file():
        return {"applied": False, "reason": "composeFileFromManifest already active"}

    root = m.resolved_root(mp)
    try:
        root_r = root.resolve()
    except OSError:
        return {"applied": False, "reason": "bad root"}

    compose_rel = (dc.compose_file or "").strip()
    if not compose_rel:
        return {"applied": False, "reason": "no composeFile to include from"}

    compose_abs = (Path(compose_rel) if Path(compose_rel).is_absolute() else (root_r / compose_rel)).resolve()
    if not compose_abs.is_file():
        return {"applied": False, "reason": f"compose file missing: {compose_abs}"}

    try:
        raw_c = yaml.safe_load(compose_abs.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        return {"applied": False, "reason": f"read compose: {exc}"}

    svc_specs = raw_c.get("services")
    if not isinstance(svc_specs, dict):
        return {"applied": False, "reason": "no services in compose"}

    to_reset = [str(name) for name, spec in svc_specs.items() if _service_has_publishable_ports(spec)]
    if not to_reset:
        return {"applied": False, "reason": "no host ports in compose"}

    host_slug = host_slug_from_app_id(str(m.name or mp.parent.name))
    project_name = (dc.project_name or "").strip() or host_slug
    inferred_routing = infer_single_backend_routing_from_services(svc_specs, host_slug, project_name)
    lh_attach: set[str] = set()
    if inferred_routing:
        bh = str(inferred_routing[0].get("backendHost") or "")
        for name, spec in svc_specs.items():
            if not isinstance(spec, dict):
                continue
            if _compose_service_backend_host(str(name), spec, project_name) == bh:
                lh_attach.add(str(name))
                break
    if not lh_attach:
        lh_attach = set(to_reset)

    try:
        rel_inc = os.path.relpath(str(compose_abs), str(mp.parent))
    except ValueError:
        rel_inc = str(compose_abs)

    rel_inc_yaml = rel_inc.replace("\\", "/")

    lines = [
        "# Auto-generated by LEco DevOps Register — host ports removed; upstream repo unchanged.\n",
        f"include:\n  - path: {rel_inc_yaml}\n\n",
        "services:\n",
    ]
    for svc in sorted(to_reset, key=lambda x: x.lower()):
        lines.append(f"  {svc}:\n")
        lines.append("    ports: !reset []\n")
        if svc in lh_attach:
            lines.append("    networks:\n")
            lines.append("      - default\n")
            lines.append("      - lh-network\n\n")
        else:
            lines.append("\n")
    if lh_attach:
        lines.append("networks:\n  lh-network:\n    external: true\n")

    entry_path = mp.parent / _ENTRY_COMPOSE_NAME
    try:
        entry_path.write_text("".join(lines), encoding="utf-8")
    except OSError as exc:
        return {"applied": False, "reason": f"write entry: {exc}"}

    prof_name = "leco.yaml"
    try:
        bridge = yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        bridge = {}
    if isinstance(bridge, dict):
        lhp = bridge.get("localHostProfile") or bridge.get("local_host_profile") or "leco.yaml"
        if isinstance(lhp, str) and lhp.strip():
            prof_name = lhp.strip()

    prof_path = mp.parent / prof_name
    try:
        loc = yaml.safe_load(prof_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
        return {"applied": False, "reason": f"read profile: {exc}", "partial": str(entry_path)}

    if not isinstance(loc, dict):
        return {"applied": False, "reason": "invalid profile", "partial": str(entry_path)}

    infra = loc.get("infrastructure")
    if not isinstance(infra, dict):
        infra = {}
    loc["infrastructure"] = infra

    new_dc: dict[str, Any] = {
        "composeFileFromManifest": _ENTRY_COMPOSE_NAME,
        "projectName": project_name,
    }
    if dc.env_file and str(dc.env_file).strip():
        new_dc["envFile"] = str(dc.env_file).strip()

    infra["dockerCompose"] = new_dc

    routing = infra.get("routing")
    entries_missing = (
        not isinstance(routing, dict)
        or not isinstance(routing.get("entries"), list)
        or len(routing.get("entries") or []) == 0
    )
    if entries_missing and inferred_routing:
        infra["routing"] = {"entries": inferred_routing}

    cf = infra.get("cloudflare")
    if isinstance(cf, dict):
        wc = cf.get("wranglerConfig") or cf.get("wrangler_config")
        if wc is None or (isinstance(wc, str) and not str(wc).strip()):
            infra.pop("cloudflare", None)

    try:
        prof_path.write_text(
            yaml.safe_dump(loc, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    except OSError as exc:
        return {"applied": False, "reason": f"write profile: {exc}", "partial": str(entry_path)}

    return {
        "applied": True,
        "reason": "hosting compose entry + composeFileFromManifest + routing if needed",
        "entry": str(entry_path),
        "services_reset": to_reset,
    }

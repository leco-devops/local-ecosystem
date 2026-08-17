"""Structured infrastructure facts for a repository path — evidence, not guesses.

``leco_detect`` answers *what kind of app is this*. This module answers the questions an
agent has to answer correctly before it can write a manifest that routes:

* which compose **service** owns which port, what its **container** is called, and what each
  published port maps to **inside** the container (``{published, target}``, never a flat list);
* which Cloudflare Workers exist and what they are called;
* whether the repository *declares* its ports somewhere as data (this project's target case
  declares them in ``infra/dev/topology.mjs``) and, if so, which name owns which port;
* what the package scripts actually run;
* and — the point of the whole module — **what could not be determined**.

The last one is deliberate. ``docs/AI-ONBOARDING-FINDINGS.md`` records an onboarding run whose
model wrote, in its own trace, *"We don't know the port of the control server. We'll leave it
as 3000 arbitrarily."* It was not being careless; it was never shown the answer. Every field
here therefore names the file it came from, and anything unresolved goes into ``unknowns``
rather than being filled with something plausible. A named gap is worth more than a
confident-looking manifest that routes production traffic to a dead port.

Three entry points:

``collect_evidence(root)``      structured facts for a repository path (read-only)
``validate_compose_merge(...)`` ``docker compose config`` on a file + overlays, resolved
``verify_urls(...)``            probe declared URLs and classify why each one fails
"""

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

PROJECT_ROOT = os.getenv("DASHBOARD_PROJECT_ROOT", "/project")

# Traefik as seen from wherever this process runs. Inside the dashboard container that is the
# container name on lh-network; the ports are Traefik's, not the host publish.
TRAEFIK_HOST = os.getenv("DASHBOARD_TRAEFIK_HOST", "traefik").strip() or "traefik"
TRAEFIK_HTTP_PORT = int(os.getenv("DASHBOARD_TRAEFIK_HTTP_PORT", "80") or 80)
TRAEFIK_HTTPS_PORT = int(os.getenv("DASHBOARD_TRAEFIK_HTTPS_PORT", "443") or 443)

HOSTING_TRAEFIK_DIR = os.getenv(
    "DASHBOARD_HOSTING_TRAEFIK_DIR", os.path.join(PROJECT_ROOT, "hosting", "traefik")
)

#: ``docker compose config`` is pure parsing — it never contacts the daemon — but a compose
#: file may reference an interpolated environment that hangs a shell-out, so it stays bounded.
COMPOSE_CONFIG_TIMEOUT_DEFAULT = 60
COMPOSE_CONFIG_TIMEOUT_MAX = 180

VERIFY_TIMEOUT_DEFAULT = 8.0
VERIFY_TIMEOUT_MAX = 30.0
MAX_VERIFY_URLS = 40

_PRUNE_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "__pycache__",
        ".next",
        ".nuxt",
        ".svelte-kit",
        ".wrangler",
        "dist",
        "build",
        "out",
        "coverage",
        ".turbo",
        ".cache",
        "target",
    }
)

_SCRIPT_FILE_EXTS = (
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".py",
    ".sh",
    ".rb",
    ".go",
    ".php",
    ".yml",
    ".yaml",
)

#: Files that plausibly hold a declared port table as *data*. Names only — the parser below
#: still refuses to guess when it cannot read one.
_PORT_TABLE_NAMES = re.compile(
    r"^(topology|ports|services|workers|endpoints)\.(mjs|cjs|js|mts|ts|json|jsonc)$", re.I
)


# ---------------------------------------------------------------------------
# YAML that survives compose merge directives
# ---------------------------------------------------------------------------


class _TolerantLoader(yaml.SafeLoader):
    """SafeLoader that does not die on compose merge tags.

    A hosting overlay legitimately contains ``ports: !override [...]`` — ``!override`` and
    ``!reset`` are Compose's own merge directives. ``yaml.safe_load`` raises
    ``ConstructorError`` on them, which would make this module blind to exactly the file it
    exists to explain. **Local** tags (``!something``) are therefore constructed as their
    underlying dict/list/scalar and the tag itself is recovered separately by
    :func:`_merge_directives`.

    Only the ``!`` prefix is registered, so this stays as safe as ``SafeLoader``: the
    ``tag:yaml.org,2002:python/…`` family that would construct arbitrary Python objects is
    left unhandled and still raises. Nothing here can instantiate a type.
    """


def _construct_unknown(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_TolerantLoader.add_multi_constructor("!", _construct_unknown)

_MERGE_DIRECTIVE_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*:\s*(![A-Za-z_][A-Za-z0-9_-]*)", re.M)


def _merge_directives(text: str) -> list[dict[str, str]]:
    """``ports: !override`` rows, so a reader can see which merge semantics were asked for.

    ``!override`` **replaces** the upstream list. ``!reset`` **clears** it — an overlay that
    writes ``ports: !reset`` followed by entries publishes nothing at all. That distinction
    cost real debugging time on this repository's own test case, so it is reported rather
    than left for the next person to rediscover.
    """
    out: list[dict[str, str]] = []
    for key, tag in _MERGE_DIRECTIVE_RE.findall(text or ""):
        row = {"key": key, "directive": tag}
        if tag == "!reset":
            row["effect"] = "clears the inherited list — entries written under it are dropped"
        elif tag == "!override":
            row["effect"] = "replaces the inherited list with the entries written under it"
        out.append(row)
    return out


def _load_yaml_tolerant(path: Path) -> tuple[Any, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"could not read: {exc}"
    try:
        return yaml.load(text, Loader=_TolerantLoader), None
    except yaml.YAMLError as exc:
        return None, f"invalid YAML: {exc}"


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------


def _int_or_str(raw: Any) -> Any:
    s = str(raw).strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return s


def parse_port_entry(entry: Any) -> dict[str, Any]:
    """One compose ``ports:`` entry as ``{published, target, protocol, host_ip, mode}``.

    Both the short (``"18787:8787"``) and long (``{target: 8787, published: "18787"}``)
    syntaxes are handled. ``target`` is the port **inside** the container — the one Traefik
    must be pointed at, because Traefik reaches the container over ``lh-network`` where the
    host-side publish plays no part. Routing to ``published`` is the single most common way
    to produce a stack that builds, starts, and serves nothing.
    """
    if isinstance(entry, dict):
        return {
            "published": _int_or_str(entry.get("published")) if entry.get("published") is not None else None,
            "target": _int_or_str(entry.get("target")),
            "protocol": str(entry.get("protocol") or "tcp"),
            "host_ip": str(entry.get("host_ip") or "") or None,
            "mode": str(entry.get("mode") or "") or None,
            "raw": entry,
            "syntax": "long",
        }

    raw = str(entry).strip()
    body, _, proto = raw.partition("/")
    protocol = proto.strip().lower() or "tcp"

    host_ip: str | None = None
    rest = body
    # ``[::1]:8080:80`` — strip a bracketed IPv6 host before splitting on ':'.
    if rest.startswith("["):
        close = rest.find("]")
        if close > 0:
            host_ip = rest[1:close]
            rest = rest[close + 1 :].lstrip(":")

    parts = rest.split(":")
    published: Any = None
    target: Any = None
    if host_ip is None and len(parts) == 3:
        host_ip = parts[0] or None
        published = _int_or_str(parts[1])
        target = _int_or_str(parts[2])
    elif len(parts) >= 2:
        published = _int_or_str(parts[-2])
        target = _int_or_str(parts[-1])
    elif len(parts) == 1:
        # ``"8787"`` publishes an ephemeral host port for container port 8787.
        target = _int_or_str(parts[0])

    return {
        "published": published,
        "target": target,
        "protocol": protocol,
        "host_ip": host_ip,
        "mode": None,
        "raw": raw,
        "syntax": "short",
    }


def _service_facts(name: str, spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        return {"service_name": name, "error": "service is not a mapping"}
    ports_raw = spec.get("ports")
    port_pairs: list[dict[str, Any]] = []
    if isinstance(ports_raw, list):
        for item in ports_raw:
            port_pairs.append(parse_port_entry(item))
    elif ports_raw is not None:
        port_pairs.append({"published": None, "target": None, "raw": ports_raw,
                           "error": "ports is not a list"})

    networks = spec.get("networks")
    if isinstance(networks, dict):
        net_names = sorted(str(k) for k in networks)
    elif isinstance(networks, list):
        net_names = [str(n) for n in networks]
    else:
        net_names = []

    build = spec.get("build")
    if isinstance(build, dict):
        build_out: Any = {k: v for k, v in build.items() if k in ("context", "dockerfile", "target")}
    elif build is not None:
        build_out = str(build)
    else:
        build_out = None

    expose = spec.get("expose")
    depends = spec.get("depends_on")
    if isinstance(depends, dict):
        depends_out = sorted(str(k) for k in depends)
    elif isinstance(depends, list):
        depends_out = [str(d) for d in depends]
    else:
        depends_out = []

    return {
        "service_name": name,
        "container_name": str(spec.get("container_name") or "") or None,
        "image": str(spec.get("image") or "") or None,
        "build": build_out,
        "networks": net_names,
        "port_pairs": port_pairs,
        "expose": [str(e) for e in expose] if isinstance(expose, list) else [],
        "depends_on": depends_out,
        "has_healthcheck": isinstance(spec.get("healthcheck"), dict),
        "profiles": [str(p) for p in spec.get("profiles") or []]
        if isinstance(spec.get("profiles"), list)
        else [],
    }


def _compose_file_facts(root: Path, rel: Path) -> dict[str, Any]:
    path = root / rel
    data, err = _load_yaml_tolerant(path)
    out: dict[str, Any] = {"file": rel.as_posix(), "abs_path": str(path)}
    try:
        out["merge_directives"] = _merge_directives(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        out["merge_directives"] = []
    if err:
        out["error"] = err
        out["services"] = []
        return out
    if not isinstance(data, dict):
        out["error"] = "compose root is not a mapping"
        out["services"] = []
        return out
    out["project_name"] = str(data.get("name") or "") or None
    services = data.get("services")
    rows: list[dict[str, Any]] = []
    if isinstance(services, dict):
        for name in services:
            rows.append(_service_facts(str(name), services[name]))
    elif services is not None:
        out["error"] = "services is not a mapping"
    out["services"] = rows
    nets = data.get("networks")
    out["networks"] = (
        {str(k): (v if isinstance(v, dict) else {}) for k, v in nets.items()}
        if isinstance(nets, dict)
        else {}
    )
    vols = data.get("volumes")
    out["volumes"] = sorted(str(k) for k in vols) if isinstance(vols, dict) else []
    return out


def _list_compose_files(root: Path) -> list[Path]:
    """Compose files under ``root``, best-first — shared with ``leco_detect``.

    Imported rather than reimplemented so "which compose files exist" cannot drift between
    what detect reports and what evidence explains.
    """
    try:
        from leco_detect import _list_compose_files as _detect_list  # type: ignore[attr-defined]

        return list(_detect_list(root))
    except Exception:  # noqa: BLE001 - evidence must still work outside the dashboard process
        names = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
        subs = ("", "docker", "infra/docker", "infra", "deploy", "ops", ".docker")
        found: list[Path] = []
        for sub in subs:
            for name in names:
                rel = Path(sub) / name if sub else Path(name)
                if (root / rel).is_file():
                    found.append(rel)
        return found


def collect_compose_evidence(root: Path) -> dict[str, Any]:
    files = _list_compose_files(root)
    return {
        "count": len(files),
        "files": [_compose_file_facts(root, rel) for rel in files],
    }


#: LEco-owned overlays that live beside ``leco.app.yaml`` in a hosting slot, not in the app
#: repo. They are not named ``docker-compose.yml``, so ordinary compose discovery misses them.
LECO_OVERLAY_NAMES = (
    "docker-compose.leco-hosting.yml",
    "docker-compose.leco-runtime.yml",
)


def collect_overlay_evidence(hosting_dir: Path) -> dict[str, Any]:
    """LEco overlay files in a hosting slot, with their compose merge directives.

    Reported separately from the app's own compose because they are separately owned: the app
    repo is never modified, and the overlay is the only file LEco writes into the merge.
    """
    rows: list[dict[str, Any]] = []
    for name in LECO_OVERLAY_NAMES:
        if (hosting_dir / name).is_file():
            rows.append(_compose_file_facts(hosting_dir, Path(name)))
    return {"dir": str(hosting_dir), "count": len(rows), "files": rows}


# ---------------------------------------------------------------------------
# Workers / runtimes
# ---------------------------------------------------------------------------


def collect_worker_evidence(root: Path) -> dict[str, Any]:
    """Wrangler configs with the worker ``name`` read from inside each file.

    Delegates to ``leco_wrangler_paths.enumerate_wrangler_workers`` — the same discovery the
    rest of LEco uses, including ``.jsonc``, which is what ``wrangler init`` has generated by
    default since 2024 and which an earlier collector did not look for at all.
    """
    try:
        from leco_wrangler_paths import enumerate_wrangler_workers
    except ImportError as exc:  # pragma: no cover - dashboard always has it
        return {"count": 0, "configs": [], "error": f"wrangler discovery unavailable: {exc}"}

    entries = enumerate_wrangler_workers(root)
    named = [e for e in entries if e.get("name")]
    return {
        "count": len(entries),
        "named_count": len(named),
        "configs": entries,
        "worker_names": sorted({str(e["name"]) for e in named}),
    }


# ---------------------------------------------------------------------------
# Declared port tables
# ---------------------------------------------------------------------------

_JS_LINE_COMMENT = re.compile(r"//[^\n]*")
_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_JS_CONST_NUMBER = re.compile(
    r"(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(\d{2,5})\s*;", re.M
)
_JS_OBJECT = re.compile(r"\{[^{}]*\}", re.S)
_JS_NAME = re.compile(r"\bname\s*:\s*['\"`]([^'\"`]+)['\"`]")
_JS_PORT = re.compile(r"\b(port|inspectorPort|httpPort|listenPort)\s*:\s*([A-Za-z_$][\w$]*|\d+)")


def _strip_js_comments(text: str) -> str:
    return _JS_LINE_COMMENT.sub("", _JS_BLOCK_COMMENT.sub("", text))


def parse_js_port_table(text: str) -> dict[str, Any]:
    """Parse ``{ name, port }`` object literals out of a JS/TS module, conservatively.

    Only three things are trusted: a string ``name``, a numeric ``port``, and a numeric
    ``export const NAME = 1234`` that a ``port`` may reference by identifier (this
    repository's test case writes ``port: FRONT_DOOR_PORT``). Anything else — a computed
    port, a spread, an imported constant — is reported under ``unresolved`` with the name it
    belongs to. Nothing is inferred from position or ordering.
    """
    body = _strip_js_comments(text or "")
    consts: dict[str, int] = {
        name: int(value) for name, value in _JS_CONST_NUMBER.findall(body)
    }

    entries: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for block in _JS_OBJECT.findall(body):
        m = _JS_NAME.search(block)
        if not m:
            continue
        name = m.group(1)
        found = {key: val for key, val in _JS_PORT.findall(block)}
        if "port" not in found:
            continue
        row: dict[str, Any] = {"name": name}
        bad: list[str] = []
        for key, raw in found.items():
            if raw.isdigit():
                row[_snake(key)] = int(raw)
            elif raw in consts:
                row[_snake(key)] = consts[raw]
                row.setdefault("resolved_from", {})[_snake(key)] = raw
            else:
                bad.append(f"{key}={raw}")
        if "port" not in row:
            unresolved.append({"name": name, "reason": f"port is not a literal or known const ({', '.join(bad)})"})
            continue
        if bad:
            row["partial"] = f"could not resolve {', '.join(bad)}"
        entries.append(row)

    return {"constants": consts, "entries": entries, "unresolved": unresolved}


def _snake(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _parse_json_port_table(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return {"entries": [], "unresolved": [], "error": f"invalid JSON: {exc}"}
    entries: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("name") and isinstance(item.get("port"), int):
                entries.append({"name": str(item["name"]), "port": int(item["port"])})
    elif isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, int):
                entries.append({"name": str(key), "port": value})
            elif isinstance(value, dict) and isinstance(value.get("port"), int):
                entries.append({"name": str(key), "port": int(value["port"])})
    if not entries:
        return {"entries": [], "unresolved": [], "error": "no {name, port} rows recognised"}
    return {"entries": entries, "unresolved": []}


def _find_port_table_files(root: Path, *, max_depth: int = 3) -> list[Path]:
    out: list[Path] = []
    root = root.resolve()

    def walk(directory: Path, depth: int) -> None:
        if depth > max_depth or len(out) >= 12:
            return
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_dir():
                if child.name in _PRUNE_DIRS or child.name.startswith("."):
                    continue
                walk(child, depth + 1)
            elif _PORT_TABLE_NAMES.match(child.name):
                try:
                    out.append(child.relative_to(root))
                except ValueError:
                    continue

    walk(root, 0)
    return out


def collect_declared_ports(root: Path) -> dict[str, Any]:
    """Port tables the repository declares as data, attributed to the file they came from.

    This is the field whose absence produced the invented ``3000`` in
    ``docs/AI-ONBOARDING-FINDINGS.md``. Every port here carries its source file; when a file
    looks like a port table but cannot be parsed, that is reported as a failure rather than
    filled in.
    """
    sources: list[dict[str, Any]] = []
    for rel in _find_port_table_files(root):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            sources.append({"file": rel.as_posix(), "error": f"could not read: {exc}",
                            "entries": [], "unresolved": []})
            continue
        if path.suffix.lower() in (".json", ".jsonc"):
            parsed = _parse_json_port_table(text)
        else:
            parsed = parse_js_port_table(text)
        if not parsed.get("entries") and not parsed.get("unresolved") and not parsed.get("error"):
            # Nothing that looks like a port table — do not report a file we learned nothing from.
            continue
        row: dict[str, Any] = {"file": rel.as_posix(), "entries": parsed.get("entries") or [],
                               "unresolved": parsed.get("unresolved") or []}
        if parsed.get("error"):
            row["error"] = parsed["error"]
        if parsed.get("constants"):
            row["constants"] = parsed["constants"]
        sources.append(row)

    total = sum(len(s.get("entries") or []) for s in sources)
    return {"source_count": len(sources), "entry_count": total, "sources": sources}


def _port_owner_index(declared: dict[str, Any]) -> dict[int, dict[str, str]]:
    index: dict[int, dict[str, str]] = {}
    for source in declared.get("sources") or []:
        for entry in source.get("entries") or []:
            port = entry.get("port")
            if isinstance(port, int) and port not in index:
                index[port] = {"name": str(entry.get("name")), "source": str(source.get("file"))}
    return index


def attribute_ports(compose: dict[str, Any], declared: dict[str, Any]) -> dict[str, Any]:
    """Join compose container ports to declared owners — the link an agent otherwise guesses.

    A container publishing ten ports needs one route per *container* port, and nothing in the
    compose file says which of ten workers answers on 8789. The declared table does. Ports
    with no declared owner are listed separately instead of being paired by position, which
    is what an earlier heuristic did and is why the previous run's routes were plausible and
    wrong.
    """
    index = _port_owner_index(declared)
    attributed: list[dict[str, Any]] = []
    unattributed: list[dict[str, Any]] = []
    for file_row in compose.get("files") or []:
        for service in file_row.get("services") or []:
            for pair in service.get("port_pairs") or []:
                target = pair.get("target")
                row = {
                    "compose_file": file_row.get("file"),
                    "service_name": service.get("service_name"),
                    "container_name": service.get("container_name"),
                    "published": pair.get("published"),
                    "container_port": target,
                }
                owner = index.get(target) if isinstance(target, int) else None
                if owner:
                    row["owner"] = owner["name"]
                    row["owner_source"] = owner["source"]
                    attributed.append(row)
                else:
                    unattributed.append(row)
    return {"attributed": attributed, "unattributed": unattributed}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

_SCRIPT_TOKEN = re.compile(r"[A-Za-z0-9_@./\\-]+")


def _files_named_by(command: str, root: Path) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for token in _SCRIPT_TOKEN.findall(command or ""):
        if not token.lower().endswith(_SCRIPT_FILE_EXTS):
            continue
        rel = token.lstrip("./")
        if not rel or rel in seen or ".." in Path(rel).parts:
            continue
        seen.add(rel)
        hits.append({"file": rel, "exists": (root / rel).is_file()})
    return hits


def collect_entry_points(root: Path) -> dict[str, Any]:
    """``package.json`` scripts and the files those scripts actually name.

    Reading the script *names* and never the files they point at is how an earlier collector
    concluded that a dev control panel was the primary service. The file each command names
    is resolved here and reported with whether it exists on disk.
    """
    pkg_path = root / "package.json"
    out: dict[str, Any] = {"package_json": None, "scripts": {}, "referenced_files": [],
                           "engines": None, "package_manager": None, "main": None, "bin": None}
    if not pkg_path.is_file():
        return out
    try:
        data = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        out["error"] = f"package.json unreadable: {exc}"
        return out
    if not isinstance(data, dict):
        out["error"] = "package.json is not an object"
        return out

    out["package_json"] = "package.json"
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    out["scripts"] = {str(k): str(v) for k, v in scripts.items()}
    out["engines"] = data.get("engines") if isinstance(data.get("engines"), dict) else None
    out["package_manager"] = str(data.get("packageManager") or "") or None
    out["main"] = str(data.get("main") or "") or None
    out["bin"] = data.get("bin")

    referenced: list[dict[str, Any]] = []
    for name, command in out["scripts"].items():
        files = _files_named_by(command, root)
        if files:
            referenced.append({"script": name, "command": command, "files": files})
    out["referenced_files"] = referenced
    return out


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def _link_name_to(slot: Path, target: Path) -> str | None:
    """Name of the child of ``slot`` that resolves to ``target`` (usually ``source``)."""
    try:
        children = sorted(slot.iterdir())
    except OSError:
        return None
    for child in children:
        try:
            if child.is_dir() and child.resolve() == target:
                return child.name
        except OSError:
            continue
    return None


def collect_evidence(root: Path, *, hosting_dir: Path | None = None) -> dict[str, Any]:
    """Everything above, plus an explicit account of what is still unknown.

    ``hosting_dir`` is the LEco hosting slot when it differs from the scanned app root — the
    directory holding ``leco.app.yaml`` and any ``docker-compose.leco-*.yml`` overlay.
    """
    root = Path(root).resolve()
    compose = collect_compose_evidence(root)
    workers = collect_worker_evidence(root)
    declared = collect_declared_ports(root)
    entries = collect_entry_points(root)
    attribution = attribute_ports(compose, declared)

    unknowns: list[str] = []

    if compose["count"] == 0:
        unknowns.append(
            "No compose file was found. Nothing states how this application runs as "
            "containers, which services exist, or what ports they publish."
        )
    for file_row in compose["files"]:
        if file_row.get("error"):
            unknowns.append(f"{file_row['file']}: {file_row['error']}")
        for service in file_row.get("services") or []:
            if not service.get("container_name"):
                unknowns.append(
                    f"{file_row['file']}: service '{service.get('service_name')}' declares no "
                    "container_name — Traefik cannot address it by a stable name until one is set "
                    "(compose otherwise names it <project>-<service>-1)."
                )
            for pair in service.get("port_pairs") or []:
                if pair.get("target") is None:
                    unknowns.append(
                        f"{file_row['file']}: service '{service.get('service_name')}' has a port "
                        f"entry {pair.get('raw')!r} whose container port could not be parsed."
                    )

    if declared["source_count"] == 0:
        unknowns.append(
            "No declared port table was found (searched for topology/ports/services/workers/"
            "endpoints files up to 3 levels deep). Which process owns which port is not stated "
            "anywhere machine-readable in this repository — do not infer it from ordering."
        )
    for source in declared["sources"]:
        if source.get("error"):
            unknowns.append(f"{source['file']}: {source['error']}")
        for bad in source.get("unresolved") or []:
            unknowns.append(
                f"{source['file']}: '{bad.get('name')}' — {bad.get('reason')}"
            )

    for config in workers.get("configs") or []:
        if config.get("error"):
            unknowns.append(f"{config['config']}: {config['error']}")
        elif not config.get("name"):
            unknowns.append(f"{config['config']}: declares no worker name.")

    if attribution["unattributed"]:
        listing = ", ".join(
            f"{r['container_name'] or r['service_name']}:{r['container_port']}"
            for r in attribution["unattributed"][:12]
        )
        unknowns.append(
            f"{len(attribution['unattributed'])} published container port(s) have no declared "
            f"owner: {listing}. Nothing in the repository says what answers on them; route them "
            "only after confirming with the application's own documentation or a live probe."
        )

    if workers["count"] > 1 and compose["count"] and not attribution["attributed"]:
        unknowns.append(
            f"{workers['count']} Worker configs and no worker→port mapping could be resolved. "
            "Pairing workers to ports by list order is a guess and has produced wrong routes here "
            "before."
        )

    for ref in entries.get("referenced_files") or []:
        for file_row in ref.get("files") or []:
            if not file_row.get("exists"):
                unknowns.append(
                    f"package.json script '{ref['script']}' names {file_row['file']}, which does "
                    "not exist at that path."
                )

    overlays: dict[str, Any] | None = None
    if hosting_dir is not None and Path(hosting_dir).resolve() != root:
        slot = Path(hosting_dir).resolve()
        # A hosting slot reaches the app through a link (conventionally ``source``). Naming it
        # turns every compose path into one usable verbatim by leco_compose_validate, which
        # resolves relative to the slot — otherwise the caller has to guess the prefix, and
        # guessing prefixes is what this module exists to stop.
        prefix = _link_name_to(slot, root)
        for file_row in compose["files"]:
            if prefix:
                file_row["path_from_hosting_dir"] = f"{prefix}/{file_row['file']}"
            elif str(root).startswith(str(slot)):
                file_row["path_from_hosting_dir"] = os.path.relpath(
                    file_row["abs_path"], str(slot)
                )
        overlays = collect_overlay_evidence(slot)
        overlays["app_root_link"] = prefix
        for file_row in overlays["files"]:
            if file_row.get("error"):
                unknowns.append(f"{file_row['file']}: {file_row['error']}")
            for directive in file_row.get("merge_directives") or []:
                if directive.get("directive") == "!reset":
                    unknowns.append(
                        f"{file_row['file']}: '{directive['key']}: !reset' clears the inherited "
                        "list rather than replacing it. Entries written under !reset are dropped "
                        "by the merge — use !override to replace. Confirm with "
                        "leco_compose_validate before deploying."
                    )

    return {
        "root": str(root),
        "compose": compose,
        "overlays": overlays,
        "workers": workers,
        "declared_ports": declared,
        "port_attribution": attribution,
        "entry_points": entries,
        "unknowns": unknowns,
        "summary": {
            "compose_files": compose["count"],
            "compose_services": sum(len(f.get("services") or []) for f in compose["files"]),
            "published_port_pairs": len(attribution["attributed"]) + len(attribution["unattributed"]),
            "wrangler_configs": workers["count"],
            "declared_port_entries": declared["entry_count"],
            "unknown_count": len(unknowns),
        },
    }


# ---------------------------------------------------------------------------
# docker compose config
# ---------------------------------------------------------------------------


_ROOT_ENV_NAMES = (
    "DASHBOARD_PROJECT_ROOT",
    "DASHBOARD_WORKSPACE_PARENT",
    "DASHBOARD_PROJECT_ROOT_HOST",
    "DASHBOARD_WORKSPACE_PARENT_HOST",
)


def allowed_roots() -> list[Path]:
    """Real directories a compose file is allowed to live under.

    Both the in-container mount points and their host paths count, because a hosting slot
    reaches its application through a ``source`` symlink that resolves to the host path — and
    that host path is mounted at the same location precisely so ``docker compose`` can read
    it. Anything outside this set is refused rather than parsed.
    """
    roots: list[Path] = []
    seen: set[str] = set()
    for name in _ROOT_ENV_NAMES:
        raw = (os.getenv(name) or "").strip()
        if not raw:
            continue
        try:
            resolved = Path(raw).resolve()
        except OSError:
            continue
        key = str(resolved)
        if key in seen or not resolved.is_dir():
            continue
        seen.add(key)
        roots.append(resolved)
    if not roots:
        roots.append(Path(PROJECT_ROOT).resolve())
    return roots


def _confine(base: Path, candidate: str, roots: list[Path]) -> Path:
    """Resolve ``candidate`` relative to ``base`` and require it to land inside ``roots``.

    ``..`` is rejected outright; symlinks are followed and the *resolved* location is what is
    checked, so a link pointing outside the allowed roots cannot smuggle a file in.
    """
    raw = (candidate or "").strip()
    if not raw:
        raise ValueError("empty path")
    path = Path(raw)
    if ".." in path.parts:
        raise ValueError(f"{raw!r} contains '..'")
    resolved = (path if path.is_absolute() else (base / path)).resolve()
    if not any(resolved == r or r in resolved.parents for r in roots):
        raise ValueError(
            f"{raw!r} resolves to {resolved}, which is outside the allowed roots "
            f"({', '.join(str(r) for r in roots)})"
        )
    if not resolved.is_file():
        raise ValueError(f"not a file: {raw}")
    return resolved


def _resolved_service_rows(services: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(services, dict):
        return rows
    for name in sorted(services):
        rows.append(_service_facts(str(name), services[name]))
    return rows


def validate_compose_merge(
    base_dir: Path,
    compose_file: str,
    overlay_files: list[str] | None = None,
    *,
    project_name: str = "",
    timeout: int = COMPOSE_CONFIG_TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """Run ``docker compose config`` and return the **resolved** merge.

    This is what turns "I wrote an overlay" into "the overlay does what I meant". Compose
    list-merge semantics are not obvious: a plain ``ports:`` in an overlay **appends** to the
    inherited list, ``ports: !override`` **replaces** it, and ``ports: !reset`` **clears** it —
    so an overlay written with ``!reset`` merges without error and publishes nothing. Only the
    resolved output distinguishes those three, which is why authoring an overlay without
    running this is guesswork.

    ``base_dir`` is the compose working directory; every file must resolve underneath it.
    The argv is a list — no user text is ever interpolated into a shell string.
    """
    base = Path(base_dir).resolve()
    if not base.is_dir():
        return {"ok": False, "error": f"not a directory: {base}"}
    roots = allowed_roots()

    try:
        files = [_confine(base, compose_file, roots)]
        for extra in overlay_files or []:
            files.append(_confine(base, str(extra), roots))
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    bounded = max(5, min(int(timeout or COMPOSE_CONFIG_TIMEOUT_DEFAULT), COMPOSE_CONFIG_TIMEOUT_MAX))
    argv: list[str] = ["docker", "compose"]
    for path in files:
        argv.extend(["-f", str(path)])
    if project_name.strip():
        argv.extend(["-p", project_name.strip()])
    argv.extend(["config", "--format", "json"])

    started = time.perf_counter()
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, never a shell string
            argv,
            cwd=str(base),
            capture_output=True,
            text=True,
            timeout=bounded,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "docker CLI is not available to this process",
                "command": argv}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"docker compose config timed out after {bounded}s",
                "command": argv}
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    stderr = (proc.stderr or "").strip()
    result: dict[str, Any] = {
        "command": argv,
        "exit_code": proc.returncode,
        "elapsed_ms": elapsed_ms,
        "compose_files": [str(f) for f in files],
        "stderr": stderr[-4000:],
    }
    if proc.returncode != 0:
        result["ok"] = False
        result["error"] = stderr.splitlines()[-1] if stderr else "docker compose config failed"
        return result

    try:
        data = json.loads(proc.stdout or "{}")
    except ValueError as exc:
        result["ok"] = False
        result["error"] = f"could not parse docker compose config output: {exc}"
        return result

    services = _resolved_service_rows(data.get("services"))
    networks = data.get("networks") if isinstance(data.get("networks"), dict) else {}
    result.update(
        {
            "ok": True,
            "project_name": str(data.get("name") or "") or None,
            "services": services,
            "networks": {
                str(k): {kk: vv for kk, vv in (v or {}).items() if kk in ("name", "external", "driver")}
                for k, v in networks.items()
            },
            "volumes": sorted(str(k) for k in (data.get("volumes") or {})),
            "summary": {
                "service_count": len(services),
                "published_ports": sum(len(s.get("port_pairs") or []) for s in services),
                "services_on_lh_network": [
                    s["service_name"] for s in services if "lh-network" in (s.get("networks") or [])
                ],
            },
        }
    )
    return result


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

_HOST_RULE_RE = re.compile(r"Host\(\s*((?:`[^`]+`\s*,?\s*)+)\)", re.I)
_BACKTICK_RE = re.compile(r"`([^`]+)`")
_HOST_REGEXP_RE = re.compile(r"HostRegexp\(", re.I)


def _load_traefik_routers() -> dict[str, Any]:
    """Every router/service Traefik's file provider is serving, across all dynamic files."""
    directory = Path(HOSTING_TRAEFIK_DIR)
    routers: list[dict[str, Any]] = []
    services: dict[str, list[str]] = {}
    files: list[str] = []
    errors: list[str] = []
    if not directory.is_dir():
        return {"ok": False, "error": f"no Traefik dynamic directory at {directory}",
                "routers": [], "services": {}, "files": []}
    for path in sorted(directory.glob("*.y*ml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, UnicodeDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        files.append(path.name)
        http = (data or {}).get("http") if isinstance(data, dict) else None
        if not isinstance(http, dict):
            continue
        for key, spec in (http.get("routers") or {}).items():
            if not isinstance(spec, dict):
                continue
            routers.append(
                {
                    "key": str(key),
                    "file": path.name,
                    "rule": str(spec.get("rule") or ""),
                    "service": str(spec.get("service") or ""),
                    "entry_points": spec.get("entryPoints") or [],
                    "tls": bool(spec.get("tls")),
                }
            )
        for key, spec in (http.get("services") or {}).items():
            if not isinstance(spec, dict):
                continue
            lb = spec.get("loadBalancer") or {}
            urls = [
                str(s.get("url"))
                for s in (lb.get("servers") or [])
                if isinstance(s, dict) and s.get("url")
            ]
            services[str(key)] = urls
    return {"ok": True, "routers": routers, "services": services, "files": files,
            "errors": errors}


def _routers_for_host(hostname: str, table: dict[str, Any]) -> list[dict[str, Any]]:
    host = (hostname or "").strip().lower()
    hits: list[dict[str, Any]] = []
    for router in table.get("routers") or []:
        rule = router.get("rule") or ""
        matched = False
        for group in _HOST_RULE_RE.findall(rule):
            if any(h.strip().lower() == host for h in _BACKTICK_RE.findall(group)):
                matched = True
                break
        if not matched and _HOST_REGEXP_RE.search(rule):
            # A regexp router *may* match; say so rather than claim it does.
            hits.append({**router, "match": "hostregexp_possible"})
            continue
        if matched:
            hits.append({**router, "match": "host_exact"})
    return hits


def _san_matches(hostname: str, sans: list[str]) -> bool:
    """RFC 6125 hostname matching, including the rule that makes ``*.lh`` useless.

    A wildcard covers exactly one label, and a wildcard directly below a top-level domain is
    refused by every TLS client — ``*.lh`` would assert ownership of an entire TLD and matches
    nothing at all, not even ``dashboard.lh``. That is precisely why certs/generate-certs.sh
    issues an explicit SAN per hostname instead, and why adding a hostname means re-issuing.
    """
    host = (hostname or "").strip().lower().rstrip(".")
    for raw in sans:
        san = raw.strip().lower()
        if san == host:
            return True
        if not san.startswith("*."):
            continue
        base = san[2:]
        if "." not in base:
            # `*.lh` — legal to write, matched by nothing.
            continue
        suffix = san[1:]  # ".example.lh"
        if host.endswith(suffix) and "." not in host[: -len(suffix)] and host != base:
            return True
    return False


def _tls_probe(hostname: str, *, timeout: float) -> dict[str, Any]:
    """Handshake against the edge with SNI set, and check the presented certificate.

    The chain cannot be verified from inside the dashboard container — the mkcert root CA
    lives in the operator's trust store, not in the repository — so that is reported as
    ``chain_verified: null`` with a reason rather than silently counted as a pass. What *is*
    verified here is the part that actually breaks when a hosted app gains a hostname: whether
    the certificate the edge serves covers that name.
    """
    out: dict[str, Any] = {
        "handshake_ok": False,
        "hostname_in_cert": None,
        "chain_verified": None,
        "chain_note": "mkcert root CA is not available to this process; chain not verified",
        "sans": [],
        "error": None,
    }
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    tmp_path = ""
    try:
        with socket.create_connection((TRAEFIK_HOST, TRAEFIK_HTTPS_PORT), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=hostname) as tls:
                der = tls.getpeercert(binary_form=True)
                out["tls_version"] = tls.version()
        out["handshake_ok"] = True
        if not der:
            out["error"] = "edge presented no certificate"
            return out
        # ssl only decodes a certificate it verified, so decode the PEM from disk instead.
        with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as handle:
            handle.write(ssl.DER_cert_to_PEM_cert(der))
            tmp_path = handle.name
        import _ssl  # noqa: PLC0415 - stdlib decoder for a PEM we already hold

        decoded = _ssl._test_decode_cert(tmp_path)  # noqa: SLF001 - the only stdlib PEM decoder
        sans = [v for k, v in decoded.get("subjectAltName") or () if k == "DNS"]
        out["sans"] = sans
        out["san_count"] = len(sans)
        out["not_after"] = decoded.get("notAfter")
        out["hostname_in_cert"] = _san_matches(hostname, sans)
    except (OSError, ssl.SSLError, ValueError) as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    return out


def _http_probe(url: str, *, timeout: float) -> dict[str, Any]:
    """Probe through Traefik with the declared Host header.

    Redirects are not followed and TLS is terminated at ``http://traefik`` deliberately: this
    is the same technique ``monitor.check_url`` uses, because ``https://<app>.lh`` frequently
    does not resolve inside the dashboard container while the edge itself always does.
    """
    import requests  # noqa: PLC0415 - heavy import, only needed when probing

    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    probe_url = f"http://{TRAEFIK_HOST}:{TRAEFIK_HTTP_PORT}{path}"
    started = time.perf_counter()
    try:
        response = requests.get(
            probe_url,
            timeout=timeout,
            verify=False,  # noqa: S501 - local mkcert edge; TLS is checked separately
            allow_redirects=False,
            headers={"Host": host},
        )
        return {
            "probe_url": probe_url,
            "status_code": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "server": response.headers.get("Server"),
            "location": response.headers.get("Location"),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - any transport failure is a probe result
        return {
            "probe_url": probe_url,
            "status_code": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }


#: Why each classification means something different to fix.
CLASSIFICATIONS = {
    "ok": "the URL answered",
    "route_missing": "no Traefik router matches this hostname — the app's routes were never merged",
    "backend_unreachable": "a router exists but the backend refused — app down, or wrong container/port",
    "tls_invalid": "the edge certificate does not cover this hostname — re-run certs/generate-certs.sh",
    "unhealthy": "the backend answered with 5xx",
}


def classify_url_result(
    routers: list[dict[str, Any]], tls: dict[str, Any] | None, http: dict[str, Any]
) -> tuple[str, str]:
    """Turn a probe into an actionable class. Order matters and is deliberate.

    A bare 502 tells an agent nothing it can act on: the router may be missing, the container
    may be down, the port may be wrong, or the hostname may simply not be in the certificate.
    Route and certificate are therefore checked *before* the status code — a hostname absent
    from the certificate reports ``tls_invalid`` even while the backend is also down, because
    regenerating the certificate is the step that has to happen first either way.
    """
    exact = [r for r in routers if r.get("match") == "host_exact"]
    if not routers:
        return "route_missing", (
            "No Traefik router rule contains this hostname. Register the app or merge its "
            "route fragment; the edge is answering with its catch-all."
        )
    if tls is not None:
        if not tls.get("handshake_ok"):
            return "tls_invalid", f"TLS handshake against the edge failed: {tls.get('error')}"
        if tls.get("hostname_in_cert") is False:
            return "tls_invalid", (
                "The certificate the edge serves does not list this hostname among its SANs. "
                "Run certs/generate-certs.sh and restart Traefik (leco_certs_refresh)."
            )
    status = http.get("status_code")
    if status is None:
        return "backend_unreachable", (
            f"The edge could not be reached at all: {http.get('error')}"
        )
    if status in (502, 503, 504):
        backends = sorted({b for r in exact or routers for b in r.get("backends") or []})
        hint = f" Router points at {', '.join(backends)}." if backends else ""
        return "backend_unreachable", (
            f"A router matches and Traefik returned {status}: the backend refused the "
            f"connection. The app is not running, or the container name/port is wrong.{hint}"
        )
    if status >= 500:
        return "unhealthy", f"The backend answered {status} — it is reachable but erroring."
    return "ok", f"Answered {status}."


def manifest_path_for_slug(slug: str) -> str | None:
    """The manifest of a registered app, straight from ``config/leco-registry.yaml``.

    Deliberately not ``leco_control.leco_meta_for_slug``: that also resolves the app's
    *source root* for compose, and refuses a slot whose ``source`` symlink resolves to a host
    path outside the container's declared mounts. Verification needs only the manifest, and
    an app that cannot be started is exactly the one whose URLs someone wants classified.
    """
    from hosted_offboard import load_leco_registry_entries, resolve_manifest_path  # noqa: PLC0415

    wanted = (slug or "").strip()
    for entry in load_leco_registry_entries():
        if str(entry.get("id") or "").strip() != wanted:
            continue
        resolved = resolve_manifest_path(str(entry.get("manifest") or "").strip())
        if resolved and Path(resolved).is_file():
            return resolved
    return None


def urls_for_slug(slug: str) -> dict[str, Any]:
    """Every URL a registered app declares, from its manifest and localhost profile."""
    from hosted_apps import manifest_ui_fields  # noqa: PLC0415 - dashboard-only import

    manifest_path = manifest_path_for_slug(slug)
    if not manifest_path:
        return {
            "ok": False,
            "error": f"no registered app {slug!r} with a readable manifest in "
            "config/leco-registry.yaml",
        }
    meta = {"manifest_path": manifest_path}
    fields = manifest_ui_fields(meta["manifest_path"])
    urls: list[str] = []
    seen: set[str] = set()

    def add(candidate: Any) -> None:
        text = str(candidate or "").strip()
        if text and text not in seen:
            seen.add(text)
            urls.append(text)

    for row in fields.get("localhost_urls") or []:
        if isinstance(row, dict):
            add(row.get("public_url") or row.get("publicUrl"))
    add(fields.get("main_url"))
    for row in fields.get("endpoint_urls") or []:
        if isinstance(row, dict):
            add(row.get("public_url") or row.get("url"))
        else:
            add(row)
    return {
        "ok": True,
        "slug": slug.strip(),
        "manifest_path": meta.get("manifest_path"),
        "urls": urls,
        "routes": fields.get("routes") or [],
    }


def verify_urls(
    urls: list[str],
    *,
    routes: list[dict[str, Any]] | None = None,
    timeout: float = VERIFY_TIMEOUT_DEFAULT,
) -> dict[str, Any]:
    """Probe each URL and classify it. See :data:`CLASSIFICATIONS`."""
    bounded = max(1.0, min(float(timeout or VERIFY_TIMEOUT_DEFAULT), VERIFY_TIMEOUT_MAX))
    table = _load_traefik_routers()
    services = table.get("services") or {}
    declared_backends: dict[str, dict[str, Any]] = {}
    for route in routes or []:
        if isinstance(route, dict) and route.get("hostname"):
            declared_backends[str(route["hostname"]).lower()] = route

    targets = [u for u in (urls or []) if str(u or "").strip()][:MAX_VERIFY_URLS]

    def one(url: str) -> dict[str, Any]:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        matched = _routers_for_host(hostname, table)
        for router in matched:
            router["backends"] = services.get(router.get("service") or "", [])
        tls = None
        if hostname.endswith(".lh") or parsed.scheme == "https":
            tls = _tls_probe(hostname, timeout=bounded)
        http = _http_probe(url, timeout=bounded)
        classification, detail = classify_url_result(matched, tls, http)
        row: dict[str, Any] = {
            "url": url,
            "hostname": hostname,
            "classification": classification,
            "detail": detail,
            "status_code": http.get("status_code"),
            "latency_ms": http.get("latency_ms"),
            "probe_url": http.get("probe_url"),
            "probe_error": http.get("error"),
            "tls_valid": None if tls is None else bool(
                tls.get("handshake_ok") and tls.get("hostname_in_cert")
            ),
            "routers": [
                {
                    "key": r.get("key"),
                    "rule": r.get("rule"),
                    "service": r.get("service"),
                    "backends": r.get("backends"),
                    "file": r.get("file"),
                    "match": r.get("match"),
                }
                for r in matched
            ],
        }
        if tls is not None:
            row["tls"] = {
                "handshake_ok": tls.get("handshake_ok"),
                "hostname_in_cert": tls.get("hostname_in_cert"),
                "chain_verified": tls.get("chain_verified"),
                "chain_note": tls.get("chain_note"),
                "san_count": tls.get("san_count"),
                "not_after": tls.get("not_after"),
                "error": tls.get("error"),
            }
        declared = declared_backends.get(hostname)
        if declared:
            row["declared_backend"] = declared.get("backend") or declared
        return row

    if not targets:
        results: list[dict[str, Any]] = []
    elif len(targets) == 1:
        results = [one(targets[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            results = list(pool.map(one, targets))

    counts: dict[str, int] = {}
    for row in results:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1

    return {
        "ok": True,
        "checked": len(results),
        "results": results,
        "counts": counts,
        "all_ok": bool(results) and all(r["classification"] == "ok" for r in results),
        "classifications": CLASSIFICATIONS,
        "traefik": {
            "files": table.get("files") or [],
            "router_count": len(table.get("routers") or []),
            "errors": table.get("errors") or [],
            "edge": f"{TRAEFIK_HOST}:{TRAEFIK_HTTP_PORT}/{TRAEFIK_HTTPS_PORT}",
        },
    }


def verify_app(
    slug: str = "", urls: list[str] | None = None, *, timeout: float = VERIFY_TIMEOUT_DEFAULT
) -> dict[str, Any]:
    """Verify a registered app by slug, an explicit URL list, or both."""
    pool: list[str] = list(urls or [])
    routes: list[dict[str, Any]] = []
    slug_info: dict[str, Any] = {}
    if slug.strip():
        slug_info = urls_for_slug(slug)
        if not slug_info.get("ok"):
            return slug_info
        pool = list(slug_info.get("urls") or []) + pool
        routes = slug_info.get("routes") or []
    if not pool:
        return {"ok": False, "error": "no URLs to verify — pass a registered slug or urls[]"}
    out = verify_urls(pool, routes=routes, timeout=timeout)
    if slug_info:
        out["slug"] = slug_info.get("slug")
        out["manifest_path"] = slug_info.get("manifest_path")
        out["declared_routes"] = routes
    return out

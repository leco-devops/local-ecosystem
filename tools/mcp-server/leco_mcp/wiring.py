"""Claude Code client wiring — the parts a user should never have to reason about.

Two failure modes cost real time and are invisible from inside a chat session:

1. **The server is registered twice.** The plugin (`leco@leco-devops-open-project`, user
   scope, every project) and a project `.mcp.json` both point at the same launcher, so the
   client loads all 65 tools twice. Nothing errors; the session just carries double the tool
   surface, and any advice about "which one is connected" is ambiguous.
2. **The installed plugin is stale.** The plugin *cache* is a snapshot pinned at the commit
   it was installed from. The marketplace clone moves on, the repository working tree moves
   on, and the cache does not — so the skills, commands and launcher an agent actually reads
   can be weeks behind the ones on disk in front of you.

Neither is a platform fault, so neither shows up in the dashboard or in the dashboard-facing
half of ``leco-mcp doctor``. This module detects both from the filesystem alone and, when
asked, repairs them.

Everything here is pure ``pathlib`` + ``json`` so it behaves identically on macOS, Linux and
Windows; the only external process is the ``claude`` CLI, and its absence is a finding rather
than a crash.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

#: Substring that identifies our server in an arbitrary client command line.
LAUNCH_MARKER = "leco-mcp"

#: Marketplace-qualified plugin id, as ``claude plugin`` expects it.
PLUGIN_ID = "leco@leco-devops-open-project"

#: Plugin payload inside this repository — the source the cache is a snapshot of.
PLUGIN_SOURCE_SUBPATH = ("tools", "claude-plugin")

#: Runtime droppings inside a plugin cache that are not part of the payload.
CACHE_IGNORE = {".in_use", ".git", "__pycache__", ".DS_Store"}


# --------------------------------------------------------------------------- paths


def claude_dir() -> Path:
    """The Claude Code configuration directory (``~/.claude`` unless overridden)."""

    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def claude_config_file() -> Path:
    """``.claude.json`` — global and per-project client state, including MCP servers."""

    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser() / ".claude.json"
    return Path.home() / ".claude.json"


def _read_json(path: Path) -> dict[str, Any]:
    """Parse ``path`` as a JSON object, or return ``{}`` — a broken file is a finding."""

    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """Write ``data`` to ``path`` with a trailing newline, creating parents as needed.

    Writes via a sibling temp file and ``os.replace`` so an interrupted write cannot leave a
    half-written settings file behind. ``os.replace`` is atomic on POSIX and overwrites on
    Windows, unlike ``os.rename``.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".leco-tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- model


@dataclass
class Registration:
    """One place a client has been told about an MCP server."""

    name: str
    origin: str
    source: str
    command: str
    enabled: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "origin": self.origin,
            "source": self.source,
            "command": self.command,
            "enabled": self.enabled,
            "detail": self.detail,
        }


@dataclass
class Finding:
    """Something wrong with the wiring, and — where possible — how to undo it."""

    id: str
    severity: str
    summary: str
    detail: str
    remedy: str
    automatic: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "summary": self.summary,
            "detail": self.detail,
            "remedy": self.remedy,
            "automatic": self.automatic,
        }


@dataclass
class Wiring:
    """The full picture: who registered us, from where, and what is out of date."""

    project_root: Path
    registrations: list[Registration] = field(default_factory=list)
    plugin: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_root": str(self.project_root),
            "registrations": [r.as_dict() for r in self.registrations],
            "active_registrations": sum(1 for r in self.registrations if r.enabled),
            "plugin": self.plugin,
            "findings": [f.as_dict() for f in self.findings],
            "healthy": not any(f.severity in ("error", "warning") for f in self.findings),
        }


# --------------------------------------------------------------------------- helpers


def _is_ours(name: str, entry: Any) -> bool:
    """Does this ``mcpServers`` entry refer to the LEco server?

    Matched on the command rather than the name, because the name is the user's to choose.
    The name is only a fallback for HTTP registrations, which have a ``url`` and no command.
    """

    if isinstance(entry, dict):
        blob = " ".join(
            str(entry.get(key, "")) for key in ("command", "url", "serverUrl", "httpUrl")
        )
        if LAUNCH_MARKER in blob:
            return True
        args = entry.get("args")
        if isinstance(args, list) and any(LAUNCH_MARKER in str(a) for a in args):
            return True
    return "leco" in name.lower()


def _describe_command(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    if entry.get("command"):
        args = entry.get("args") or []
        return " ".join([str(entry["command"]), *(str(a) for a in args)])
    for key in ("url", "serverUrl", "httpUrl"):
        if entry.get(key):
            return str(entry[key])
    return ""


def _settings_files(project_root: Path) -> list[Path]:
    """Project settings, most-specific last — ``local`` overrides shared."""

    return [
        project_root / ".claude" / "settings.json",
        project_root / ".claude" / "settings.local.json",
    ]


def _mcpjson_toggle(project_root: Path, name: str) -> tuple[bool | None, Path | None]:
    """Is a project ``.mcp.json`` server explicitly enabled or disabled, and where?

    Returns ``(None, None)`` when neither list mentions it — Claude Code then treats the
    server as pending approval rather than active.
    """

    verdict: bool | None = None
    where: Path | None = None
    for path in _settings_files(project_root):
        data = _read_json(path)
        if name in (data.get("enabledMcpjsonServers") or []):
            verdict, where = True, path
        if name in (data.get("disabledMcpjsonServers") or []):
            verdict, where = False, path
    return verdict, where


def _tree_digest(root: Path) -> dict[str, str]:
    """SHA-256 per file, keyed by POSIX-style relative path so it compares across OSes."""

    digests: dict[str, str] = {}
    if not root.is_dir():
        return digests
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in CACHE_IGNORE for part in rel.parts):
            continue
        sha = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    sha.update(chunk)
        except OSError:
            continue
        digests[rel.as_posix()] = sha.hexdigest()
    return digests


def _git_head(repo: Path) -> str | None:
    """HEAD of ``repo`` without shelling out — plumbing a git dir is enough here."""

    git_dir = repo / ".git"
    if git_dir.is_file():  # a worktree: ".git" is a pointer file
        try:
            pointer = git_dir.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if pointer.startswith("gitdir:"):
            git_dir = Path(pointer.split(":", 1)[1].strip())
    if not git_dir.is_dir():
        return None
    head = git_dir / "HEAD"
    try:
        raw = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.startswith("ref:"):
        return raw or None
    ref = raw.split(":", 1)[1].strip()
    direct = git_dir / ref
    if direct.is_file():
        try:
            return direct.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    packed = git_dir / "packed-refs"
    try:
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                continue
            sha, _, name = line.partition(" ")
            if name.strip() == ref:
                return sha.strip()
    except OSError:
        return None
    return None


# --------------------------------------------------------------------------- discovery


def _collect_registrations(project_root: Path) -> list[Registration]:
    found: list[Registration] = []

    # 1 · the project's own .mcp.json, gated by the enabled/disabled settings lists
    project_mcp = project_root / ".mcp.json"
    for name, entry in (_read_json(project_mcp).get("mcpServers") or {}).items():
        if not _is_ours(name, entry):
            continue
        toggle, where = _mcpjson_toggle(project_root, name)
        if toggle is True:
            detail = f"approved in {where.name}" if where else "approved"
        elif toggle is False:
            detail = f"disabled in {where.name}" if where else "disabled"
        else:
            detail = "pending approval — not loaded until accepted"
        found.append(
            Registration(
                name=name,
                origin="project .mcp.json",
                source=str(project_mcp),
                command=_describe_command(entry),
                enabled=toggle is True,
                detail=detail,
            )
        )

    # 2 · ~/.claude.json — the global table, plus this project's own section
    config = _read_json(claude_config_file())
    for name, entry in (config.get("mcpServers") or {}).items():
        if _is_ours(name, entry):
            found.append(
                Registration(
                    name=name,
                    origin="user config (all projects)",
                    source=str(claude_config_file()),
                    command=_describe_command(entry),
                    enabled=True,
                )
            )
    projects = config.get("projects") or {}
    for proj_path, proj in projects.items():
        try:
            same = Path(proj_path).resolve() == project_root.resolve()
        except OSError:
            same = str(proj_path) == str(project_root)
        if not same or not isinstance(proj, dict):
            continue
        for name, entry in (proj.get("mcpServers") or {}).items():
            if _is_ours(name, entry):
                found.append(
                    Registration(
                        name=name,
                        origin="user config (this project)",
                        source=f"{claude_config_file()} → projects[{proj_path}]",
                        command=_describe_command(entry),
                        enabled=True,
                    )
                )

    # 3 · the installed plugin, which carries its own .mcp.json
    for install_path in _plugin_install_paths():
        plugin_mcp = install_path / ".mcp.json"
        for name, entry in (_read_json(plugin_mcp).get("mcpServers") or {}).items():
            if _is_ours(name, entry):
                found.append(
                    Registration(
                        name=name,
                        origin="plugin (all projects)",
                        source=str(plugin_mcp),
                        command=_describe_command(entry),
                        enabled=True,
                        detail=f"from {PLUGIN_ID}",
                    )
                )

    return found


def _plugin_records() -> list[dict[str, Any]]:
    data = _read_json(claude_dir() / "plugins" / "installed_plugins.json")
    records = (data.get("plugins") or {}).get(PLUGIN_ID) or []
    return [r for r in records if isinstance(r, dict)]


def _plugin_install_paths() -> list[Path]:
    paths = []
    for record in _plugin_records():
        raw = record.get("installPath")
        if raw:
            paths.append(Path(raw))
    return paths


def _manifest_version(payload: Path) -> str | None:
    """Version declared by a plugin payload's own ``.claude-plugin/plugin.json``."""

    manifest = _read_json(payload / ".claude-plugin" / "plugin.json")
    version = manifest.get("version")
    return str(version) if version else None


def _marketplace_clone() -> Path | None:
    data = _read_json(claude_dir() / "plugins" / "known_marketplaces.json")
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if PLUGIN_ID.endswith(name) and entry.get("installLocation"):
            return Path(str(entry["installLocation"]))
    return None


def _inspect_plugin(project_root: Path) -> dict[str, Any]:
    records = _plugin_records()
    if not records:
        return {"installed": False}

    record = records[0]
    install_path = Path(str(record.get("installPath", "")))
    info: dict[str, Any] = {
        "installed": True,
        "id": PLUGIN_ID,
        "scope": record.get("scope"),
        "version": record.get("version"),
        "install_path": str(install_path),
        "installed_from_commit": record.get("gitCommitSha"),
        "installed_at": record.get("installedAt"),
        "last_updated": record.get("lastUpdated"),
    }

    clone = _marketplace_clone()
    if clone is not None:
        info["marketplace_clone"] = str(clone)
        head = _git_head(clone)
        info["marketplace_commit"] = head
        pinned = record.get("gitCommitSha")
        info["update_available"] = bool(head and pinned and head != pinned)

        # `claude plugin update` compares version strings, not commits. If the payload has
        # moved while the manifest version stood still, the update is a silent no-op for
        # every installed user — the cache can never catch up. Detect that specifically,
        # because the remedy is a version bump upstream, not a command downstream.
        clone_payload = clone.joinpath(*PLUGIN_SOURCE_SUBPATH)
        if clone_payload.is_dir() and install_path.is_dir():
            upstream, cached = _tree_digest(clone_payload), _tree_digest(install_path)
            behind = sorted(
                [k for k in upstream.keys() & cached.keys() if upstream[k] != cached[k]]
                + list(upstream.keys() - cached.keys())
            )
            info["files_behind_marketplace"] = behind
            info["marketplace_version"] = _manifest_version(clone_payload)
            info["version_gate_blocks_update"] = bool(
                behind and info.get("marketplace_version") == record.get("version")
            )

    # Does the working tree in front of the user match what the cache actually serves?
    source = project_root.joinpath(*PLUGIN_SOURCE_SUBPATH)
    if source.is_dir() and install_path.is_dir():
        src, dst = _tree_digest(source), _tree_digest(install_path)
        differing = sorted(k for k in src.keys() & dst.keys() if src[k] != dst[k])
        info["working_tree"] = {
            "source": str(source),
            "differing_files": differing,
            "missing_from_cache": sorted(src.keys() - dst.keys()),
            "extra_in_cache": sorted(dst.keys() - src.keys()),
            "in_sync": not differing and src.keys() == dst.keys(),
        }
    return info


def inspect(project_root: Path | str) -> Wiring:
    """Read the client wiring off disk and judge it. Touches nothing."""

    root = Path(project_root).expanduser()
    wiring = Wiring(project_root=root)
    wiring.registrations = _collect_registrations(root)
    wiring.plugin = _inspect_plugin(root)

    active = [r for r in wiring.registrations if r.enabled]
    if len(active) > 1:
        origins = ", ".join(f"{r.name} ({r.origin})" for r in active)
        wiring.findings.append(
            Finding(
                id="duplicate-registration",
                severity="warning",
                summary=f"{len(active)} active registrations of the same server — "
                "the client loads every tool once per registration",
                detail=origins,
                remedy="Keep the plugin (it also carries the skills and /leco:* commands) "
                "and disable the project .mcp.json copy for this machine only, via "
                "disabledMcpjsonServers in .claude/settings.local.json (gitignored, so "
                "other clones are unaffected).",
                automatic=any(r.origin == "project .mcp.json" for r in active)
                and any(r.origin.startswith("plugin") for r in active),
            )
        )
    elif not active:
        wiring.findings.append(
            Finding(
                id="no-registration",
                severity="error",
                summary="No active registration — no client will load the LEco tools",
                detail="Neither a plugin install nor an approved project .mcp.json was found.",
                remedy="claude plugin install " + PLUGIN_ID,
            )
        )

    plugin = wiring.plugin
    behind = plugin.get("files_behind_marketplace") or []
    if behind:
        wiring.findings.append(
            Finding(
                id="stale-plugin-cache",
                severity="warning",
                summary=f"The installed plugin is {len(behind)} file(s) behind the "
                "marketplace it came from",
                detail=f"cache is pinned at {str(plugin.get('installed_from_commit'))[:12]}, "
                f"marketplace is at {str(plugin.get('marketplace_commit'))[:12]} — "
                "agents read skills, commands and the launcher from the pinned copy",
                remedy=f"claude plugin update {PLUGIN_ID}",
                automatic=True,
            )
        )

    if plugin.get("version_gate_blocks_update"):
        wiring.findings.append(
            Finding(
                id="plugin-version-not-bumped",
                severity="error",
                summary="Plugin content changed without a version bump — "
                "`claude plugin update` is a no-op for everyone who installed it",
                detail=f"payload differs in {len(behind)} file(s), but both the installed "
                f"copy and the marketplace declare version {plugin.get('version')!r}. "
                "The updater compares version strings, so it reports success and copies "
                "nothing. Every downstream user is frozen at whatever they first installed.",
                remedy="Bump `version` in tools/claude-plugin/.claude-plugin/plugin.json and "
                "the matching marketplace entry, then publish. To recover this machine now, "
                f"`leco-mcp repair --apply` reinstalls the plugin (uninstall + install), "
                "which bypasses the version gate.",
                automatic=False,
            )
        )

    tree = plugin.get("working_tree") or {}
    if tree and not tree.get("in_sync"):
        changed = len(tree.get("differing_files", [])) + len(tree.get("missing_from_cache", []))
        wiring.findings.append(
            Finding(
                id="plugin-working-tree-drift",
                severity="info",
                summary=f"{changed} plugin file(s) differ between this working tree and "
                "the installed cache",
                detail="Expected while developing the plugin: the cache tracks a published "
                "commit, not your uncommitted edits. It matters because an agent reads the "
                "cache, so edits here do not affect a running session until they are "
                "published and the plugin is updated.",
                remedy="Commit and push, then: claude plugin update " + PLUGIN_ID,
            )
        )

    return wiring


# --------------------------------------------------------------------------- repair


def _disable_project_mcpjson(project_root: Path, name: str) -> str:
    """Turn off a project ``.mcp.json`` server for this machine only.

    Writes to ``.claude/settings.local.json``, which is gitignored — the repository's
    ``.mcp.json`` stays intact for clones that have no plugin installed.
    """

    path = project_root / ".claude" / "settings.local.json"
    data = _read_json(path)
    enabled = [n for n in (data.get("enabledMcpjsonServers") or []) if n != name]
    disabled = list(data.get("disabledMcpjsonServers") or [])
    if name not in disabled:
        disabled.append(name)
    if enabled:
        data["enabledMcpjsonServers"] = enabled
    else:
        data.pop("enabledMcpjsonServers", None)
    data["disabledMcpjsonServers"] = disabled
    _write_json(path, data)
    return f"disabled '{name}' from {path}"


def _run_claude(args: Iterable[str]) -> tuple[bool, str]:
    exe = shutil.which("claude")
    if not exe:
        return False, "the 'claude' CLI is not on PATH"
    try:
        proc = subprocess.run(
            [exe, *args],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    tail = output[-1] if output else ""
    return proc.returncode == 0, tail


def _still_behind(project_root: Path) -> bool:
    """Re-read the plugin from disk: is the cache still short of the marketplace?"""

    return bool((_inspect_plugin(project_root).get("files_behind_marketplace") or []))


def repair(project_root: Path | str, apply: bool = False) -> dict[str, Any]:
    """Fix what can be fixed without a judgement call.

    With ``apply=False`` this is a dry run: it reports the actions it would take. Nothing
    here is destructive — the duplicate fix writes a gitignored per-machine settings file,
    and the staleness fix delegates to ``claude plugin update``.
    """

    root = Path(project_root).expanduser()
    wiring = inspect(root)
    actions: list[dict[str, Any]] = []

    for finding in wiring.findings:
        if not finding.automatic:
            continue

        if finding.id == "duplicate-registration":
            target = next(
                (
                    r
                    for r in wiring.registrations
                    if r.enabled and r.origin == "project .mcp.json"
                ),
                None,
            )
            if target is None:
                continue
            if not apply:
                actions.append(
                    {
                        "finding": finding.id,
                        "action": "disable duplicate project .mcp.json registration",
                        "target": target.name,
                        "applied": False,
                    }
                )
                continue
            result = _disable_project_mcpjson(root, target.name)
            actions.append(
                {
                    "finding": finding.id,
                    "action": "disable duplicate project .mcp.json registration",
                    "target": target.name,
                    "applied": True,
                    "result": result,
                }
            )

        elif finding.id == "stale-plugin-cache":
            if not apply:
                actions.append(
                    {
                        "finding": finding.id,
                        "action": f"claude plugin update {PLUGIN_ID}"
                        + (
                            ", then reinstall if the version gate blocks it"
                            if wiring.plugin.get("version_gate_blocks_update")
                            else ""
                        ),
                        "applied": False,
                    }
                )
                continue

            ok, message = _run_claude(["plugin", "update", PLUGIN_ID, "--yes"])
            steps = [f"update: {message}" if ok else f"update failed: {message}"]

            # `update` exits 0 even when it copied nothing, so verify rather than trust:
            # re-read the payload and fall back to a reinstall, which ignores the version.
            if not _still_behind(root):
                actions.append(
                    {
                        "finding": finding.id,
                        "action": f"claude plugin update {PLUGIN_ID}",
                        "applied": True,
                        "result": "; ".join(steps),
                    }
                )
                continue

            steps.append("update left the cache unchanged (version gate) — reinstalling")
            removed, remove_msg = _run_claude(["plugin", "uninstall", PLUGIN_ID])
            steps.append(f"uninstall: {remove_msg}")
            installed, install_msg = _run_claude(["plugin", "install", PLUGIN_ID])
            steps.append(f"install: {install_msg}")
            settled = removed and installed and not _still_behind(root)
            actions.append(
                {
                    "finding": finding.id,
                    "action": f"reinstall {PLUGIN_ID} (version gate blocked the update)",
                    "applied": settled,
                    "result": "; ".join(steps),
                }
            )

    return {
        "wiring": wiring.as_dict(),
        "applied": apply,
        "actions": actions,
        "restart_required": apply and any(a.get("applied") for a in actions),
    }

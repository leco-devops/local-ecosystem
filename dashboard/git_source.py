"""Onboard an application straight from a Git repository.

The register wizard has always started from a *local folder*: a repo-relative
path, ``wsp:SiblingRepo``, or a host path pasted from Finder.  On a real server
there is no Finder and no pre-cloned workspace — the source of truth is a Git
URL.  This module turns a URL into exactly the same thing the wizard already
knows how to consume: a directory under an allowed root, named by the app id,
addressable through :func:`leco_detect.registration_path_field_for_ui` (so
``wsp:MyApp`` or ``hosting/app-sources/MyApp``) and therefore usable verbatim by
``/api/leco/detect``.

Design constraints that shaped this file
----------------------------------------

**Credentials never leave the server, and never land in the clone.**  A token
embedded in the remote URL is written into ``.git/config`` by ``git clone`` and
then lives forever inside the working tree the operator is about to register.
So the URL is kept clean and the secret is handed to git through a temporary
``GIT_ASKPASS`` helper (HTTPS) or a temporary key file referenced by
``GIT_SSH_COMMAND`` (SSH), both inside a 0700 directory that is removed in a
``finally``.  Secrets are additionally redacted from every captured line before
it reaches a response, a log, or the NDJSON stream.  Persisted credentials live
in ``config/git-credentials.yaml`` (mode 0600, gitignored) beside the existing
``config/ai-providers.yaml`` / ``config/ui-credentials.yaml`` and are only ever
returned masked.

**git never blocks the request.**  Without ``GIT_TERMINAL_PROMPT=0`` a private
repository makes git sit on a username prompt forever, which would hang a Flask
worker; every invocation is non-interactive, has a wall-clock timeout, and gets
killed and reported rather than left running.

**A hostile repository cannot quietly fill the disk.**  Clones are shallow by
default (``depth=1``; pass ``full_history`` for the whole thing) and the target
directory is polled while git runs — crossing the byte budget kills git and
removes the partial clone.
"""

from __future__ import annotations

import copy
import os
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
CONFIG_DIR = PROJECT_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "git-credentials.yaml"
CONFIG_REL_PATH = "config/git-credentials.yaml"

#: Fallback clone root when the workspace parent is not mounted read-write.
#: Gitignored (``hosting/app-sources/``) so clones never become repo content.
PROJECT_CLONE_SUBDIR = "hosting/app-sources"

_ENV_CLONE_ROOT = "LECO_GIT_CLONE_ROOT"
_ENV_TIMEOUT = "LECO_GIT_TIMEOUT"
_ENV_MAX_MB = "LECO_GIT_MAX_CLONE_MB"
_ENV_DEPTH = "LECO_GIT_DEFAULT_DEPTH"

DEFAULT_TIMEOUT = 300
DEFAULT_MAX_MB = 2048
DEFAULT_DEPTH = 1

#: How often the size guard re-measures the growing clone (seconds).
_SIZE_POLL_SECONDS = 2.0

_MASK_DOTS = 12


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _env_int(name: str, fallback: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return fallback
    try:
        val = int(raw)
    except ValueError:
        return fallback
    return val if val >= minimum else fallback


def clone_timeout() -> int:
    """Wall-clock budget for a single git invocation (seconds)."""
    return _env_int(_ENV_TIMEOUT, DEFAULT_TIMEOUT, minimum=10)


def max_clone_bytes() -> int:
    """Hard ceiling on a clone's on-disk size before git is killed."""
    return _env_int(_ENV_MAX_MB, DEFAULT_MAX_MB, minimum=1) * 1024 * 1024


def default_depth() -> int:
    """Default ``--depth`` for shallow clones (``full_history`` opts out)."""
    return _env_int(_ENV_DEPTH, DEFAULT_DEPTH, minimum=1)


class GitSourceError(RuntimeError):
    """A user-facing failure: the message is safe to show in the browser."""


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------

# scp-style shorthand: [user@]host:path — the only non-URL form git accepts that
# we allow, because ``git@github.com:owner/repo.git`` is what every provider
# copy-button hands the operator.
_SCP_RE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[A-Za-z0-9._-]+):(?P<path>[^\s]+)$")
_SSH_URL_RE = re.compile(
    r"^ssh://(?:(?P<user>[A-Za-z0-9._-]+)@)?(?P<host>[A-Za-z0-9._-]+)(?::(?P<port>\d+))?/(?P<path>[^\s]+)$"
)
_HTTPS_URL_RE = re.compile(
    r"^https://(?P<host>[A-Za-z0-9._-]+)(?::(?P<port>\d+))?/(?P<path>[^\s]+)$"
)

#: Anything that lets git spawn a process or read the local filesystem.
_FORBIDDEN_PREFIXES = ("file://", "ext::", "git://", "http://", "ftp://", "ftps://", "rsync://")

_MAX_URL_LEN = 512


def validate_repo_url(raw: str) -> dict[str, str]:
    """Parse and vet a repository URL.

    Only ``https://`` and SSH (``ssh://`` or ``user@host:path``) are accepted.
    ``file://`` and ``ext::`` are rejected outright — the first would let a
    caller pull arbitrary host directories into the workspace, the second makes
    git execute a command of the caller's choosing.  Credentials embedded in the
    URL are rejected too: git would persist them into ``.git/config``.

    Returns ``{"url", "kind", "host", "path", "name"}``.
    Raises :class:`GitSourceError` with a message meant for the operator.
    """
    url = (raw or "").strip()
    if not url:
        raise GitSourceError("Repository URL is required.")
    if len(url) > _MAX_URL_LEN:
        raise GitSourceError(f"Repository URL is too long (limit {_MAX_URL_LEN} characters).")
    # Order matters: the dangerous shapes are named explicitly before the generic
    # whitespace rejection, so the operator is told *why* rather than "malformed".
    if url.startswith("-"):
        raise GitSourceError("Repository URL may not start with '-' (it would be read as a git option).")

    low = url.lower()
    for bad in _FORBIDDEN_PREFIXES:
        if low.startswith(bad):
            if bad == "http://":
                raise GitSourceError("Plain http:// is not allowed — use https:// so credentials are not sent in clear text.")
            if bad == "file://":
                raise GitSourceError("file:// URLs are not allowed — use Local folder for a path already on this machine.")
            raise GitSourceError(f"{bad} URLs are not allowed. Use https:// or ssh:// (or git@host:owner/repo.git).")
    if "::" in url:
        raise GitSourceError("Remote helper syntax (transport::address) is not allowed — use a plain https:// or ssh:// URL.")
    if low.startswith("https://") and "@" in url[len("https://") :].split("/", 1)[0]:
        # git copies the remote URL verbatim into .git/config, so a token here
        # would end up inside the tree the operator is about to register.
        raise GitSourceError(
            "Remove the credentials from the URL (https://token@host/…). "
            "Paste the token in the credential field instead — a token in the URL is written into the clone's .git/config."
        )
    if any(ch in url for ch in "\r\n\t\x00") or " " in url:
        raise GitSourceError("Repository URL contains whitespace or control characters.")

    m = _HTTPS_URL_RE.match(url)
    if m:
        return _finish_parse(url, "https", m.group("host"), m.group("path"))

    m = _SSH_URL_RE.match(url)
    if m:
        if ":" in (m.group("user") or ""):
            raise GitSourceError("Remove the credentials from the URL — use the credential field instead.")
        return _finish_parse(url, "ssh", m.group("host"), m.group("path"))

    m = _SCP_RE.match(url)
    if m:
        return _finish_parse(url, "ssh", m.group("host"), m.group("path"))

    if low.startswith("https://") or low.startswith("ssh://"):
        raise GitSourceError("Repository URL is malformed — expected https://host/owner/repo.git or ssh://host/owner/repo.git.")
    raise GitSourceError(
        "Unsupported repository URL. Use https://host/owner/repo.git, ssh://host/owner/repo.git, or git@host:owner/repo.git."
    )


def _finish_parse(url: str, kind: str, host: str, path: str) -> dict[str, str]:
    clean_path = path.strip("/")
    if not clean_path:
        raise GitSourceError("Repository URL is missing the repository path (…/owner/repo.git).")
    if ".." in clean_path.split("/"):
        raise GitSourceError("Repository URL may not contain '..' path segments.")
    name = clean_path.rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    if not name:
        raise GitSourceError("Could not derive a repository name from the URL.")
    return {"url": url, "kind": kind, "host": host, "path": clean_path, "name": name}


_DIR_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_dir_name(raw: str) -> str:
    """Directory name for a clone — a single path segment, never traversal.

    The name reaches us from an app id or a repository URL, both operator-typed,
    so ``..``, slashes and dotfile names are collapsed rather than trusted.
    """
    s = _DIR_NAME_RE.sub("-", (raw or "").strip())
    s = s.strip("-. ")
    while ".." in s:
        s = s.replace("..", ".")
    s = s.strip("-. ")
    if not s or s in (".", ".."):
        raise GitSourceError("Could not derive a safe directory name — set an app id.")
    return s[:100]


# ---------------------------------------------------------------------------
# Clone root
# ---------------------------------------------------------------------------


def _dir_writable(path: Path) -> bool:
    """True when we can actually create files here (read-only bind mounts included)."""
    try:
        if not path.is_dir():
            return False
        probe = tempfile.NamedTemporaryFile(dir=str(path), prefix=".leco-git-probe-", delete=True)
    except (OSError, PermissionError):
        return False
    probe.close()
    return True


def _allowed_bases() -> list[Path]:
    from leco_detect import allowed_registration_bases

    return allowed_registration_bases()


def resolve_clone_root() -> dict[str, Any]:
    """Where clones land, and why.

    Preference order:

    1. ``LECO_GIT_CLONE_ROOT`` — an explicit operator override.
    2. The workspace parent (``DASHBOARD_WORKSPACE_PARENT``, the root that
       ``/api/leco/browse?root=wsp`` lists) when it is mounted **read-write**.
       This is the intended home: clones sit beside the operator's other repos
       and resolve to ``wsp:Name``.
    3. ``hosting/app-sources/`` inside the ecosystem repo — the fallback when
       the workspace parent is mounted read-only (the default local dev
       mount is ``:ro``), so onboarding from Git still works out of the box.

    The chosen root is reported in every clone response; the UI shows it rather
    than pretending the workspace parent was used.
    """
    override = (os.getenv(_ENV_CLONE_ROOT) or "").strip()
    if override:
        p = Path(override)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise GitSourceError(f"{_ENV_CLONE_ROOT}={override} is not usable: {exc}") from exc
        if not _dir_writable(p):
            raise GitSourceError(f"{_ENV_CLONE_ROOT}={override} is not writable.")
        return {
            "path": p.resolve(),
            "kind": "override",
            "label": override,
            "note": f"Clones go to {_ENV_CLONE_ROOT}={override}.",
        }

    bases = _allowed_bases()
    if len(bases) >= 2:
        wsp = bases[1].resolve()
        if _dir_writable(wsp):
            return {
                "path": wsp,
                "kind": "workspace-parent",
                "label": os.getenv("DASHBOARD_WORKSPACE_PARENT_HOST") or str(wsp),
                "note": "Clones go to the workspace parent, beside your other repositories (wsp: paths).",
            }
        fallback_reason = (
            "The workspace parent is mounted read-only, so clones go to "
            f"{PROJECT_CLONE_SUBDIR}/ inside the ecosystem repo instead "
            f"(gitignored). Mount it read-write, or set {_ENV_CLONE_ROOT}, to clone beside your other repos."
        )
    else:
        fallback_reason = (
            "No workspace parent is mounted, so clones go to "
            f"{PROJECT_CLONE_SUBDIR}/ inside the ecosystem repo (gitignored)."
        )

    p = (PROJECT_ROOT / PROJECT_CLONE_SUBDIR).resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GitSourceError(f"Cannot create clone root {PROJECT_CLONE_SUBDIR}: {exc}") from exc
    if not _dir_writable(p):
        raise GitSourceError(
            f"Clone root {PROJECT_CLONE_SUBDIR} is not writable. Set {_ENV_CLONE_ROOT} to a writable directory."
        )
    return {"path": p, "kind": "project", "label": PROJECT_CLONE_SUBDIR, "note": fallback_reason}


def clone_root_info() -> dict[str, Any]:
    """Browser-safe description of the clone root (no absolute host paths implied)."""
    try:
        root = resolve_clone_root()
    except GitSourceError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "kind": root["kind"],
        "label": root["label"],
        "note": root["note"],
        "path": str(root["path"]),
    }


def _target_dir(root: Path, name: str) -> Path:
    """Confine ``name`` to a direct child of ``root``."""
    cand = (root / safe_dir_name(name)).resolve()
    try:
        cand.relative_to(root.resolve())
    except ValueError as exc:
        raise GitSourceError("Clone target escapes the managed clone root.") from exc
    if cand == root.resolve():
        raise GitSourceError("Clone target must be a directory inside the clone root.")
    return cand


def path_field_for(path: Path) -> str:
    """Canonical wizard path (``wsp:Name`` / repo-relative) for a cloned tree."""
    from leco_detect import registration_path_field_for_ui

    return registration_path_field_for_ui(path)


# ---------------------------------------------------------------------------
# Credential storage (config/git-credentials.yaml, 0600, gitignored)
# ---------------------------------------------------------------------------

CREDENTIAL_KINDS = ("https-token", "ssh-key")

_SECRET_FIELDS = ("token", "private_key", "passphrase")


def _empty_store() -> dict[str, Any]:
    return {"credentials": {}}


def load_credential_store() -> dict[str, Any]:
    """Read the credential file. Server-side only — never return this to a browser."""
    if not CONFIG_FILE.is_file():
        return _empty_store()
    try:
        raw = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    creds = raw.get("credentials")
    return {"credentials": creds if isinstance(creds, dict) else {}}


def _save_credential_store(store: dict[str, Any]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    header = (
        "# Git credentials for LEco DevOps onboarding-from-Git.\n"
        "# This file is gitignored and readable only by the dashboard user (0600).\n"
        "# Tokens and keys here are never returned by an API, written into a clone,\n"
        "# a manifest, or a log line — the browser only ever receives a mask.\n\n"
    )
    body = yaml.dump(store, default_flow_style=False, sort_keys=False, allow_unicode=True)
    CONFIG_FILE.write_text(header + body, encoding="utf-8")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def mask_secret(value: str) -> str:
    """Mask a secret for display: first 4 + last 4 characters."""
    if not value:
        return ""
    if len(value) < 12:
        return "••••"
    return value[:4] + "•" * _MASK_DOTS + value[-4:]


def credential_id(raw: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (raw or "").strip()).strip("-.")
    if not s:
        raise GitSourceError("Credential name is required (letters, digits, . _ -).")
    return s[:64].lower()


def save_credential(
    cid: str,
    *,
    kind: str,
    token: str = "",
    username: str = "",
    private_key: str = "",
    passphrase: str = "",
    host: str = "",
) -> dict[str, Any]:
    """Persist a credential to ``config/git-credentials.yaml`` (0600, gitignored)."""
    key = credential_id(cid)
    k = (kind or "").strip().lower()
    if k not in CREDENTIAL_KINDS:
        raise GitSourceError(f"kind must be one of {', '.join(CREDENTIAL_KINDS)}.")
    entry: dict[str, Any] = {"kind": k, "host": (host or "").strip()}
    if k == "https-token":
        if not token.strip():
            raise GitSourceError("Token is required for an https-token credential.")
        entry["username"] = (username or "").strip() or "x-access-token"
        entry["token"] = token.strip()
    else:
        if not private_key.strip():
            raise GitSourceError("Private key is required for an ssh-key credential.")
        entry["private_key"] = private_key
        if passphrase:
            # ssh runs in BatchMode; a passphrase cannot be answered non-interactively.
            raise GitSourceError(
                "Passphrase-protected keys cannot be used non-interactively. Provide a key without a passphrase."
            )
    store = load_credential_store()
    store["credentials"][key] = entry
    _save_credential_store(store)
    return {"id": key, "kind": k}


def delete_credential(cid: str) -> bool:
    key = credential_id(cid)
    store = load_credential_store()
    if key not in store["credentials"]:
        return False
    del store["credentials"][key]
    _save_credential_store(store)
    return True


def get_credential(cid: str) -> dict[str, Any]:
    """Server-side lookup of a stored credential (contains secrets)."""
    key = credential_id(cid)
    entry = load_credential_store()["credentials"].get(key)
    if not isinstance(entry, dict):
        raise GitSourceError(f"No stored credential named '{key}'.")
    return copy.deepcopy(entry)


def credentials_for_ui() -> dict[str, Any]:
    """List stored credentials with every secret masked."""
    out: list[dict[str, Any]] = []
    for key, entry in sorted(load_credential_store()["credentials"].items()):
        if not isinstance(entry, dict):
            continue
        row: dict[str, Any] = {
            "id": key,
            "kind": entry.get("kind", "https-token"),
            "host": entry.get("host", ""),
            "username": entry.get("username", ""),
        }
        for field in _SECRET_FIELDS:
            val = entry.get(field)
            if isinstance(val, str) and val:
                row[f"{field}_set"] = True
                row[f"{field}_masked"] = mask_secret(val)
        out.append(row)
    return {
        "credentials": out,
        "storage": {
            "path": CONFIG_REL_PATH,
            "gitignored": True,
            "mode": "0600",
            "note": (
                f"Git credentials are stored only in {CONFIG_REL_PATH} on the dashboard host "
                "(mode 0600, gitignored). They are never written into a clone's .git/config, "
                "a manifest, a log line, or any API response — the browser only receives a mask."
            ),
        },
    }


# ---------------------------------------------------------------------------
# Auth plumbing — secrets reach git through a temp dir, never argv or the URL
# ---------------------------------------------------------------------------


class _AuthContext:
    """Temporary, 0700 auth material for one git invocation.

    HTTPS: a ``GIT_ASKPASS`` shell script that prints the username or the token
    depending on git's prompt.  The secret sits in a 0600 sibling file; only its
    *path* is exported, so the token never appears in ``argv`` (world-readable
    via ``ps``) nor in the remote URL (which git copies into ``.git/config``).

    SSH: the private key in a 0600 file plus ``GIT_SSH_COMMAND`` with
    ``IdentitiesOnly``/``BatchMode`` so ssh cannot fall back to an agent key or
    block on a passphrase prompt.
    """

    def __init__(self, cred: dict[str, Any] | None):
        self.cred = cred or {}
        self.dir: Path | None = None
        self.secrets: list[str] = []
        self.env: dict[str, str] = {}

    def __enter__(self) -> "_AuthContext":
        self.dir = Path(tempfile.mkdtemp(prefix="leco-git-"))
        os.chmod(self.dir, 0o700)
        home = self.dir / "home"
        home.mkdir(mode=0o700)
        # An isolated HOME keeps a real ~/.gitconfig credential.helper (which would
        # persist the token) and any ~/.ssh identity out of the picture.
        self.env = {
            "HOME": str(home),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GCM_INTERACTIVE": "never",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        kind = (self.cred.get("kind") or "").strip().lower()
        if kind == "https-token":
            self._setup_https()
        elif kind == "ssh-key":
            self._setup_ssh()
        else:
            # No credential: still pin ssh to non-interactive so a private repo
            # fails fast instead of waiting on a host-key or password prompt.
            self.env["GIT_SSH_COMMAND"] = (
                "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
            )
        return self

    def _setup_https(self) -> None:
        assert self.dir is not None
        token = str(self.cred.get("token") or "")
        username = str(self.cred.get("username") or "x-access-token")
        if not token:
            raise GitSourceError("HTTPS credential has no token.")
        self.secrets = [token]
        secret_file = self.dir / "askpass.secret"
        secret_file.write_text(token, encoding="utf-8")
        secret_file.chmod(0o600)
        user_file = self.dir / "askpass.user"
        user_file.write_text(username, encoding="utf-8")
        user_file.chmod(0o600)
        script = self.dir / "askpass.sh"
        script.write_text(
            "#!/bin/sh\n"
            "# Answers git's credential prompt from a 0600 file so the secret is\n"
            "# never in argv, the URL, or .git/config.\n"
            'case "$1" in\n'
            '  *[Uu]sername*) cat "$LECO_GIT_USER_FILE" ;;\n'
            '  *) cat "$LECO_GIT_SECRET_FILE" ;;\n'
            "esac\n",
            encoding="utf-8",
        )
        script.chmod(stat.S_IRWXU)
        self.env["GIT_ASKPASS"] = str(script)
        self.env["LECO_GIT_SECRET_FILE"] = str(secret_file)
        self.env["LECO_GIT_USER_FILE"] = str(user_file)

    def _setup_ssh(self) -> None:
        assert self.dir is not None
        key = str(self.cred.get("private_key") or "")
        if not key:
            raise GitSourceError("SSH credential has no private key.")
        self.secrets = [key]
        key_file = self.dir / "id_key"
        body = key if key.endswith("\n") else key + "\n"
        key_file.write_text(body, encoding="utf-8")
        key_file.chmod(0o600)
        known = self.dir / "known_hosts"
        known.write_text("", encoding="utf-8")
        known.chmod(0o600)
        self.env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_file} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile={known} -o ConnectTimeout=15"
        )

    def __exit__(self, *exc: Any) -> None:
        if self.dir is not None:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None


def redact(text: str, secrets: list[str]) -> str:
    """Strip any secret (and URL-embedded credentials) from git output."""
    out = text or ""
    for s in secrets:
        if s and len(s) >= 4:
            out = out.replace(s, "***redacted***")
            for line in s.splitlines():
                if len(line.strip()) >= 8:
                    out = out.replace(line, "***redacted***")
    # Belt and braces: never echo https://user:pass@host even if it came from git.
    out = re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1***redacted***@", out)
    return out


def resolve_credential(
    *,
    credential_id_value: str = "",
    kind: str = "",
    token: str = "",
    username: str = "",
    private_key: str = "",
) -> dict[str, Any] | None:
    """Pick the credential for this request: a stored id, or an inline secret.

    Inline secrets are used for the single invocation and discarded; persisting
    requires an explicit save through :func:`save_credential`.
    """
    cid = (credential_id_value or "").strip()
    if cid:
        return get_credential(cid)
    k = (kind or "").strip().lower()
    tok = (token or "").strip()
    key = private_key or ""
    if k == "ssh-key" or (not k and key.strip().startswith("-----BEGIN")):
        if not key.strip():
            raise GitSourceError("SSH key is empty.")
        return {"kind": "ssh-key", "private_key": key}
    if tok:
        return {"kind": "https-token", "token": tok, "username": (username or "").strip() or "x-access-token"}
    return None


# ---------------------------------------------------------------------------
# git runner
# ---------------------------------------------------------------------------


def _git_env(auth_env: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(auth_env)
    # A credential helper of "" clears every inherited helper: nothing may write
    # the token to disk inside the clone or the (isolated) HOME.
    return env


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def iter_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    secrets: list[str] | None = None,
    timeout: int | None = None,
    guard_path: Path | None = None,
    max_bytes: int | None = None,
) -> Iterator[tuple[str, Any]]:
    """Run git, yielding ``("line", str)`` then ``("end", (code, reason))``.

    ``reason`` is ``""`` on a normal exit, ``"timeout"`` when the wall-clock
    budget was exceeded, ``"size"`` when ``guard_path`` grew past ``max_bytes``.
    Both cases kill git rather than letting a request hang or a disk fill.
    """
    # ``safe.directory=*`` because the dashboard runs as root over bind-mounted
    # trees owned by the host user; without it git refuses with "dubious ownership".
    argv = [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "credential.interactive=false",
        "-c",
        "safe.directory=*",
        *args,
    ]
    secrets = secrets or []
    budget = timeout if timeout is not None else clone_timeout()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            # No stdin: git must never be able to read from the server's console.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=_git_env(env or {}),
        )
    except FileNotFoundError:
        yield ("line", "git is not installed in the dashboard container (add it to dashboard/Dockerfile).")
        yield ("end", (127, "missing-git"))
        return

    deadline = time.monotonic() + float(budget)
    next_size_check = time.monotonic() + _SIZE_POLL_SECONDS

    def _kill() -> None:
        try:
            proc.kill()
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            pass

    # ``readline()`` blocks until git writes a newline, so the timeout has to be
    # enforced from a thread that is *not* waiting on the pipe — otherwise a git
    # that produces no output (the classic credential prompt) would pin a worker
    # forever no matter what deadline we computed.
    lines_q: "queue.Queue[str | None]" = queue.Queue(maxsize=10_000)

    def _pump() -> None:
        try:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, ""):
                lines_q.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            lines_q.put(None)

    pump = threading.Thread(target=_pump, name="leco-git-reader", daemon=True)
    pump.start()

    try:
        while True:
            if time.monotonic() > deadline:
                _kill()
                yield ("line", f"[leco] git timed out after {budget}s and was terminated.")
                yield ("end", (124, "timeout"))
                return
            if guard_path is not None and max_bytes and time.monotonic() > next_size_check:
                next_size_check = time.monotonic() + _SIZE_POLL_SECONDS
                if _dir_size(guard_path) > max_bytes:
                    _kill()
                    yield (
                        "line",
                        f"[leco] clone exceeded the {max_bytes // (1024 * 1024)} MB size guard and was terminated.",
                    )
                    yield ("end", (125, "size"))
                    return
            try:
                item = lines_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            text = item.rstrip("\n")
            if text:
                yield ("line", redact(text, secrets))
        code = int(proc.wait(timeout=30) or 0)
        yield ("end", (code, ""))
    finally:
        if proc.poll() is None:
            _kill()


def run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    secrets: list[str] | None = None,
    timeout: int | None = None,
    guard_path: Path | None = None,
    max_bytes: int | None = None,
) -> tuple[int, list[str], str]:
    """Buffered :func:`iter_git`. Returns ``(code, lines, reason)``."""
    lines: list[str] = []
    code = 0
    reason = ""
    for kind, payload in iter_git(
        args,
        cwd=cwd,
        env=env,
        secrets=secrets,
        timeout=timeout,
        guard_path=guard_path,
        max_bytes=max_bytes,
    ):
        if kind == "line":
            lines.append(str(payload))
        else:
            code, reason = payload
    return code, lines, reason


def _friendly_error(code: int, lines: list[str], reason: str, *, had_credential: bool) -> str:
    """Turn a git failure into something an operator can act on."""
    blob = "\n".join(lines).lower()
    if reason == "timeout":
        return (
            f"git timed out after {clone_timeout()}s and was terminated. "
            f"Raise {_ENV_TIMEOUT} for a very large repository, or use a shallow clone."
        )
    if reason == "size":
        return (
            f"Repository exceeded the {max_clone_bytes() // (1024 * 1024)} MB size guard; the partial clone was removed. "
            f"Raise {_ENV_MAX_MB} if this repository really is that large."
        )
    if reason == "missing-git":
        return "git is not available in the dashboard container. Rebuild the dashboard image (dashboard/Dockerfile installs git)."
    # git words this differently per version/transport: "terminal prompts disabled"
    # (askpass unset + no tty), "unable to get password from user" (askpass declined),
    # "could not read Username" (http helper). All mean the same thing to the operator.
    if (
        "terminal prompts disabled" in blob
        or "authentication failed" in blob
        or "could not read username" in blob
        or "could not read password" in blob
        or "unable to get password" in blob
        or "unable to get username" in blob
        or "authentication required" in blob
    ):
        if had_credential:
            return "Authentication failed — the credential was rejected by the server. Check the token/key and its scopes."
        return (
            "Authentication required: this repository is private or does not exist. "
            "Add a credential (HTTPS token or SSH key) and try again."
        )
    if "permission denied (publickey" in blob:
        return "SSH authentication failed (permission denied, publickey). Check the key and that it is registered with the host."
    if ("remote branch" in blob and "not found" in blob) or "unknown revision" in blob or "pathspec" in blob:
        return "The requested ref (branch, tag or commit) does not exist in this repository."
    if "could not resolve host" in blob or "name or service not known" in blob:
        return "Could not resolve the host. Check the URL and the dashboard's network access."
    if "repository not found" in blob or ("not found" in blob and "fatal" in blob):
        return "Repository not found. Check the URL, or add a credential if it is private."
    if "does not appear to be a git repository" in blob or "does not exist" in blob:
        return "That path or URL is not a git repository. Check the URL, or add a credential if it is private."
    tail = [ln for ln in lines if ln.strip()][-3:]
    detail = " / ".join(tail) if tail else f"git exited with code {code}"
    return f"git failed: {detail}"


# ---------------------------------------------------------------------------
# Repository introspection
# ---------------------------------------------------------------------------

_LOG_SEP = "\x1f"


def _repo_head_info(path: Path) -> dict[str, Any]:
    """Ref/commit facts for a checked-out clone (no network)."""
    info: dict[str, Any] = {
        "ref": "",
        "ref_kind": "detached",
        "commit": "",
        "short_commit": "",
        "commit_subject": "",
        "commit_date": "",
        "commit_author": "",
    }
    code, lines, _ = run_git(["rev-parse", "HEAD"], cwd=path, timeout=30)
    if code == 0 and lines:
        info["commit"] = lines[0].strip()
        info["short_commit"] = info["commit"][:7]
    code, lines, _ = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=path, timeout=30)
    if code == 0 and lines and lines[0].strip():
        info["ref"] = lines[0].strip()
        info["ref_kind"] = "branch"
    else:
        code, lines, _ = run_git(["describe", "--tags", "--exact-match"], cwd=path, timeout=30)
        if code == 0 and lines and lines[0].strip():
            info["ref"] = lines[0].strip()
            info["ref_kind"] = "tag"
        else:
            info["ref"] = info["short_commit"]
            info["ref_kind"] = "commit"
    code, lines, _ = run_git(
        ["log", "-1", f"--pretty=%s{_LOG_SEP}%cI{_LOG_SEP}%an"], cwd=path, timeout=30
    )
    if code == 0 and lines:
        parts = lines[0].split(_LOG_SEP)
        info["commit_subject"] = parts[0] if parts else ""
        info["commit_date"] = parts[1] if len(parts) > 1 else ""
        info["commit_author"] = parts[2] if len(parts) > 2 else ""
    return info


def _remote_url(path: Path) -> str:
    code, lines, _ = run_git(["remote", "get-url", "origin"], cwd=path, timeout=30)
    if code == 0 and lines:
        return re.sub(r"(https?://)[^/\s@]+@", r"\1", lines[0].strip())
    return ""


def _is_dirty(path: Path) -> tuple[bool, list[str]]:
    code, lines, _ = run_git(["status", "--porcelain"], cwd=path, timeout=60)
    if code != 0:
        return False, []
    changed = [ln for ln in lines if ln.strip()]
    return bool(changed), changed[:20]


def _is_shallow(path: Path) -> bool:
    code, lines, _ = run_git(["rev-parse", "--is-shallow-repository"], cwd=path, timeout=30)
    return code == 0 and bool(lines) and lines[0].strip() == "true"


def _ahead_behind(path: Path) -> dict[str, Any]:
    code, lines, _ = run_git(
        ["rev-list", "--left-right", "--count", "@{u}...HEAD"], cwd=path, timeout=60
    )
    if code != 0 or not lines:
        return {"upstream": "", "behind": None, "ahead": None}
    parts = lines[0].split()
    up_code, up_lines, _ = run_git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd=path, timeout=30
    )
    upstream = up_lines[0].strip() if up_code == 0 and up_lines else ""
    try:
        return {"upstream": upstream, "behind": int(parts[0]), "ahead": int(parts[1])}
    except (ValueError, IndexError):
        return {"upstream": upstream, "behind": None, "ahead": None}


def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _same_remote(existing: str, requested: str) -> bool:
    """Compare remotes ignoring .git suffix, trailing slash and scp/ssh spelling."""

    def norm(u: str) -> str:
        s = (u or "").strip().rstrip("/")
        if s.endswith(".git"):
            s = s[:-4]
        m = _SCP_RE.match(s)
        if m:
            s = f"ssh://{m.group('host')}/{m.group('path')}"
        s = re.sub(r"^ssh://[A-Za-z0-9._-]+@", "ssh://", s)
        return s.lower()

    return norm(existing) == norm(requested)


# ---------------------------------------------------------------------------
# inspect (read-only, no clone)
# ---------------------------------------------------------------------------


def inspect_remote(url: str, credential: dict[str, Any] | None = None) -> dict[str, Any]:
    """``git ls-remote`` — does the URL resolve, and what refs does it have?

    No clone, nothing written to disk. Used by the wizard before committing to
    a download so the operator can pick a branch from a list.
    """
    parsed = validate_repo_url(url)
    with _AuthContext(credential) as auth:
        code, lines, reason = run_git(
            ["ls-remote", "--symref", "--heads", "--tags", "--", parsed["url"]],
            env=auth.env,
            secrets=auth.secrets,
            timeout=min(clone_timeout(), 120),
        )
        if code != 0:
            return {
                "ok": False,
                "error": _friendly_error(code, lines, reason, had_credential=bool(credential)),
                "repo": parsed["name"],
                "host": parsed["host"],
            }
        branches: list[str] = []
        tags: list[str] = []
        default_branch = ""
        for ln in lines:
            if ln.startswith("ref:"):
                m = re.match(r"ref:\s+refs/heads/(\S+)\s+HEAD", ln)
                if m:
                    default_branch = m.group(1)
                continue
            parts = ln.split()
            if len(parts) < 2:
                continue
            ref = parts[1]
            if ref.startswith("refs/heads/"):
                branches.append(ref[len("refs/heads/") :])
            elif ref.startswith("refs/tags/") and not ref.endswith("^{}"):
                tags.append(ref[len("refs/tags/") :])
    return {
        "ok": True,
        "url": parsed["url"],
        "kind": parsed["kind"],
        "host": parsed["host"],
        "repo": parsed["name"],
        "default_branch": default_branch or ("main" if "main" in branches else (branches[0] if branches else "")),
        "branches": sorted(set(branches))[:500],
        "tags": sorted(set(tags))[:500],
        "credential_used": bool(credential),
        "suggested_app_id": re.sub(r"[^a-z0-9-]+", "-", parsed["name"].lower()).strip("-"),
    }


# ---------------------------------------------------------------------------
# clone / update
# ---------------------------------------------------------------------------


def _looks_like_sha(ref: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", ref or ""))


def _clean_ref(ref: str) -> str:
    r = (ref or "").strip()
    if not r:
        return ""
    if r.startswith("-") or any(c in r for c in " \t\r\n\x00~^:?*[\\"):
        raise GitSourceError("Invalid ref — use a branch name, tag, or commit SHA.")
    if ".." in r:
        raise GitSourceError("Invalid ref — '..' is not allowed.")
    return r


def iter_clone_or_update(
    *,
    url: str,
    app_id: str = "",
    dir_name: str = "",
    ref: str = "",
    full_history: bool = False,
    depth: int | None = None,
    credential: dict[str, Any] | None = None,
    reset: bool = False,
    replace_remote: bool = True,
) -> Iterator[dict[str, Any]]:
    """Clone (or update) ``url`` into the managed clone root.

    Yields the same NDJSON events as ``/api/leco/register/stream``:
    ``{"type":"log","text":…}`` and a final ``{"type":"done","result":{…}}``,
    so the dashboard's existing stream reader handles it unchanged.
    :func:`clone_or_update` is the buffered wrapper.

    ``reset`` is the operator's explicit consent to discard local changes in an
    existing clone; without it a dirty or diverged clone is refused and the
    reason is returned instead.
    """
    started = time.monotonic()
    log: list[str] = []

    def line(text: str) -> dict[str, Any]:
        log.append(text)
        return {"type": "log", "text": text + "\n"}

    def step(text: str) -> dict[str, Any]:
        log.append(text)
        return {"type": "log", "text": f"\n--- {text} ---\n"}

    def done(result: dict[str, Any]) -> dict[str, Any]:
        result.setdefault("log", log[-200:])
        result["duration_ms"] = int((time.monotonic() - started) * 1000)
        return {"type": "done", "result": result}

    try:
        parsed = validate_repo_url(url)
        want_ref = _clean_ref(ref)
        root = resolve_clone_root()
        name = dir_name.strip() or app_id.strip() or parsed["name"]
        target = _target_dir(root["path"], name)
    except GitSourceError as exc:
        yield done({"ok": False, "error": str(exc)})
        return

    eff_depth = 0 if full_history else int(depth or default_depth())
    max_bytes = max_clone_bytes()
    notes: list[str] = [root["note"]]

    yield step(f"Target: {target.name} in {root['label']}")
    yield line(f"[leco] repository {parsed['url']}")
    yield line(f"[leco] target {target}")
    yield line(
        f"[leco] {'full history' if eff_depth == 0 else f'shallow clone (depth {eff_depth})'}"
        f", timeout {clone_timeout()}s, size guard {max_bytes // (1024 * 1024)} MB"
    )

    action = "cloned"
    try:
        with _AuthContext(credential) as auth:
            env, secrets = auth.env, auth.secrets
            existing = target.exists()
            if existing and target.is_dir() and not any(target.iterdir()):
                shutil.rmtree(target, ignore_errors=True)
                existing = False

            if existing and not _is_git_repo(target):
                yield done(
                    {
                        "ok": False,
                        "error": (
                            f"'{target.name}' already exists in {root['label']} and is not a git clone. "
                            "Choose a different app id, or remove that directory."
                        ),
                    }
                )
                return

            if existing:
                current_remote = _remote_url(target)
                if not _same_remote(current_remote, parsed["url"]):
                    dirty, _ = _is_dirty(target)
                    if dirty and not reset:
                        yield done(
                            {
                                "ok": False,
                                "error": (
                                    f"'{target.name}' is a clone of a different remote ({current_remote or 'unknown'}) "
                                    "and has uncommitted changes. Re-run with reset=true to discard them and re-clone, "
                                    "or choose a different app id."
                                ),
                                "requires_reset": True,
                            }
                        )
                        return
                    if not replace_remote:
                        yield done(
                            {
                                "ok": False,
                                "error": f"'{target.name}' points at {current_remote or 'another remote'}; refusing to replace it.",
                            }
                        )
                        return
                    yield line(
                        f"[leco] remote changed ({current_remote or 'unknown'} → {parsed['url']}); removing and re-cloning."
                    )
                    notes.append(
                        f"The existing clone pointed at {current_remote or 'another remote'}; it was removed and re-cloned."
                    )
                    shutil.rmtree(target, ignore_errors=True)
                    existing = False
                    action = "recloned"

            if existing:
                action = "updated"
                for ev in _update_existing(target, parsed, want_ref, eff_depth, env, secrets, reset, notes):
                    if ev.get("type") == "error":
                        yield done({"ok": False, "error": ev["error"], **ev.get("extra", {})})
                        return
                    if ev.get("type") == "line":
                        yield line(ev["line"])
                    else:
                        yield step(str(ev.get("message") or ev.get("step") or ""))
            else:
                yield step(f"Cloning {parsed['name']}…")
                for ev in _fresh_clone(target, parsed, want_ref, eff_depth, env, secrets, max_bytes):
                    if ev.get("type") == "error":
                        yield done({"ok": False, "error": ev["error"]})
                        return
                    if ev.get("type") == "line":
                        yield line(ev["line"])
                    else:
                        yield step(str(ev.get("message") or ev.get("step") or ""))
    except GitSourceError as exc:
        yield done({"ok": False, "error": str(exc)})
        return
    except OSError as exc:
        yield done({"ok": False, "error": f"Filesystem error: {exc}"})
        return

    head = _repo_head_info(target)
    size = _dir_size(target)
    result = {
        "ok": True,
        "action": action,
        "repo_url": parsed["url"],
        "repo_name": parsed["name"],
        "host": parsed["host"],
        "path": str(target),
        "path_field": path_field_for(target),
        "dir_name": target.name,
        "clone_root_kind": root["kind"],
        "clone_root_label": root["label"],
        "requested_ref": want_ref,
        "shallow": _is_shallow(target),
        "depth": eff_depth or None,
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        "credential_used": bool(credential),
        "credential_kind": (credential or {}).get("kind", ""),
        "reset_applied": bool(reset) and action == "updated",
        "notes": notes,
        **head,
    }
    yield step(f"{action} at {head['short_commit']} ({head['ref']})")
    yield done(result)


def _fresh_clone(
    target: Path,
    parsed: dict[str, str],
    want_ref: str,
    depth: int,
    env: dict[str, str],
    secrets: list[str],
    max_bytes: int,
) -> Iterator[dict[str, Any]]:
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--progress"]
    if depth:
        args += ["--depth", str(depth)]
    sha_ref = _looks_like_sha(want_ref)
    if want_ref and not sha_ref:
        args += ["--branch", want_ref, "--single-branch"]
    args += ["--", parsed["url"], str(target)]
    code, lines, reason = run_git(
        args, env=env, secrets=secrets, guard_path=target, max_bytes=max_bytes
    )
    for ln in lines:
        yield {"type": "line", "line": ln}
    if code != 0:
        shutil.rmtree(target, ignore_errors=True)
        yield {
            "type": "error",
            "error": _friendly_error(code, lines, reason, had_credential=bool(secrets)),
        }
        return

    if sha_ref:
        yield {"type": "line", "line": f"[leco] checking out commit {want_ref}"}
        fcode, flines, _freason = run_git(
            ["fetch", "--depth", "1", "origin", want_ref] if depth else ["fetch", "origin", want_ref],
            cwd=target,
            env=env,
            secrets=secrets,
            guard_path=target,
            max_bytes=max_bytes,
        )
        for ln in flines:
            yield {"type": "line", "line": ln}
        if fcode != 0 and depth:
            # Many servers refuse "fetch <sha>"; unshallow and try locally.
            yield {"type": "line", "line": "[leco] server refused a direct commit fetch; unshallowing."}
            ucode, ulines, _ = run_git(
                ["fetch", "--unshallow", "origin"],
                cwd=target,
                env=env,
                secrets=secrets,
                guard_path=target,
                max_bytes=max_bytes,
            )
            for ln in ulines:
                yield {"type": "line", "line": ln}
            if ucode != 0:
                shutil.rmtree(target, ignore_errors=True)
                yield {"type": "error", "error": _friendly_error(ucode, ulines, "", had_credential=bool(secrets))}
                return
        ccode, clines, creason = run_git(
            ["checkout", "--detach", want_ref], cwd=target, env=env, secrets=secrets, timeout=120
        )
        for ln in clines:
            yield {"type": "line", "line": ln}
        if ccode != 0:
            shutil.rmtree(target, ignore_errors=True)
            yield {
                "type": "error",
                "error": _friendly_error(ccode, clines, creason, had_credential=bool(secrets)),
            }
            return


def _update_existing(
    target: Path,
    parsed: dict[str, str],
    want_ref: str,
    depth: int,
    env: dict[str, str],
    secrets: list[str],
    reset: bool,
    notes: list[str],
) -> Iterator[dict[str, Any]]:
    """Fetch and move an existing clone to the requested ref.

    A clean, non-diverged clone is fast-forwarded silently.  A dirty or diverged
    clone is refused unless ``reset`` was passed — and when it was, the note
    saying local work was discarded travels back in the response.
    """
    dirty, changed = _is_dirty(target)
    if dirty and not reset:
        preview = ", ".join(c.strip()[:60] for c in changed[:5])
        yield {
            "type": "error",
            "error": (
                f"'{target.name}' has uncommitted local changes ({len(changed)} entry(ies): {preview}). "
                "Re-run with reset=true to discard them and hard-reset to the requested ref, or commit/stash them first."
            ),
            "extra": {"requires_reset": True, "dirty": True, "changed": changed},
        }
        return

    yield {"type": "step", "step": "fetch", "message": f"Updating {target.name}…"}
    run_git(["remote", "set-url", "origin", parsed["url"]], cwd=target, env=env, secrets=secrets, timeout=60)

    if not want_ref:
        head = _repo_head_info(target)
        want_ref = head["ref"] if head["ref_kind"] == "branch" else ""
        if not want_ref:
            code, lines, _ = run_git(
                ["rev-parse", "--abbrev-ref", "origin/HEAD"], cwd=target, env=env, secrets=secrets, timeout=30
            )
            want_ref = lines[0].strip().split("/", 1)[-1] if code == 0 and lines else ""
    if not want_ref:
        yield {"type": "error", "error": "Could not determine which branch to update to — pass a branch or tag."}
        return

    def fetch(refspec: list[str]) -> tuple[int, list[str], str]:
        args = ["fetch", "--progress", "--prune"]
        if depth:
            args += ["--depth", str(depth)]
        return run_git(
            [*args, "origin", *refspec],
            cwd=target,
            env=env,
            secrets=secrets,
            guard_path=target,
            max_bytes=max_clone_bytes(),
        )

    # The first clone is ``--single-branch``, so ``remote.origin.fetch`` only
    # covers the branch that was cloned; a plain ``git fetch origin other`` lands
    # in FETCH_HEAD and leaves no ``origin/other`` to check out.  Explicit
    # refspecs are what make switching branches on an existing clone work.
    checkout_target = ""
    if _looks_like_sha(want_ref):
        code, lines, reason = fetch([want_ref])
        checkout_target = want_ref
    else:
        code, lines, reason = fetch([f"+refs/heads/{want_ref}:refs/remotes/origin/{want_ref}"])
        checkout_target = f"origin/{want_ref}"
        if code != 0:
            # Not a branch — try it as a tag before giving up.
            tcode, tlines, treason = fetch([f"+refs/tags/{want_ref}:refs/tags/{want_ref}"])
            if tcode == 0:
                code, lines, reason = tcode, tlines, treason
                checkout_target = f"refs/tags/{want_ref}"
    for ln in lines:
        yield {"type": "line", "line": ln}
    if code != 0:
        yield {"type": "error", "error": _friendly_error(code, lines, reason, had_credential=bool(secrets))}
        return

    if checkout_target.startswith("origin/"):
        # Point the local branch at the fetched remote tip. Only reached with a
        # clean tree, or with reset=true — either way the response says which.
        code, lines, reason = run_git(
            ["checkout", "-B", want_ref, checkout_target], cwd=target, env=env, secrets=secrets, timeout=120
        )
    else:
        code, lines, reason = run_git(
            ["checkout", "--detach", checkout_target], cwd=target, env=env, secrets=secrets, timeout=120
        )
    for ln in lines:
        yield {"type": "line", "line": ln}
    if code != 0:
        yield {"type": "error", "error": _friendly_error(code, lines, reason, had_credential=bool(secrets))}
        return

    if reset:
        rcode, rlines, rreason = run_git(
            ["reset", "--hard", checkout_target],
            cwd=target,
            env=env,
            secrets=secrets,
            timeout=120,
        )
        for ln in rlines:
            yield {"type": "line", "line": ln}
        if rcode != 0:
            yield {"type": "error", "error": _friendly_error(rcode, rlines, rreason, had_credential=bool(secrets))}
            return
        run_git(["clean", "-fd"], cwd=target, env=env, secrets=secrets, timeout=120)
        notes.append(
            f"reset=true: the working tree was hard-reset to origin/{want_ref} and untracked files were removed — "
            "uncommitted local changes in this clone were discarded."
        )
    else:
        notes.append(f"Updated to the fetched tip of {want_ref}; no local changes were present.")


def clone_or_update(**kwargs: Any) -> dict[str, Any]:
    """Buffered :func:`iter_clone_or_update` — returns the final ``result``."""
    result: dict[str, Any] = {"ok": False, "error": "clone produced no result"}
    for ev in iter_clone_or_update(**kwargs):
        if ev.get("type") == "done":
            result = ev.get("result") or result
    return result


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def clone_status(path_field: str) -> dict[str, Any]:
    """Report on an existing clone: remote, ref, SHA, dirty/clean, ahead/behind.

    Offline only — no fetch, so ahead/behind reflect the last fetch.
    """
    from leco_detect import resolve_registration_path

    try:
        path = resolve_registration_path(path_field)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if not _is_git_repo(path):
        return {
            "ok": True,
            "is_git": False,
            "path": str(path),
            "path_field": path_field_for(path),
            "note": "This directory is not a git clone (onboarded from a local folder or a zip).",
        }
    dirty, changed = _is_dirty(path)
    head = _repo_head_info(path)
    tracking = _ahead_behind(path)
    size = _dir_size(path)
    return {
        "ok": True,
        "is_git": True,
        "path": str(path),
        "path_field": path_field_for(path),
        "remote_url": _remote_url(path),
        "dirty": dirty,
        "changed": changed,
        "shallow": _is_shallow(path),
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        **head,
        **tracking,
    }

"""
CI/CD for LEco DevOps — push to a repository triggers pull → build → deploy → verify → record.

Design notes that matter more than the code:

**The webhook endpoint is internet-facing and is authenticated by its HMAC signature, not by
``DASHBOARD_CONTROL_TOKEN``.**  A Git host cannot send ``X-Control-Token``; adding that check
would silently break every webhook while looking like a security improvement.  The signature
*is* the credential: :func:`verify_signature` runs before the payload is parsed, before any
pipeline is looked at, and before anything is written to disk, and it compares with
:func:`hmac.compare_digest`.  Rejections are deliberately uninformative (``{"ok": false,
"error": "forbidden"}``, HTTP 403) so an attacker learns nothing about which pipelines exist.

**Build/test steps run inside the app's own toolchain, never on the host shell.**  A pipeline
cannot carry an arbitrary command string.  It names a *compose service* the application itself
declares (``build_hook_service``), and LEco runs ``docker compose … run --rm --no-deps
<service>`` with **no command override** — the command comes from the app's own compose file.
An arbitrary shell string in a webhook-reachable config file would be remote code execution on
the host with one leaked secret; a service name that must already exist in the app's compose
project is not.  See ``docs/DEPLOYMENT.md`` for the hosting-overlay pattern
(``docker-compose.leco-hosting.yml``) used to add a ``leco-ci`` service without touching the
upstream repository.

**One run per pipeline at a time.**  A burst of pushes cannot start a burst of deploys: a push
for a SHA already running or already queued is dropped (and counted on the active run); a push
for a *newer* SHA replaces the single queued slot.  Ten pushes in a minute therefore produce at
most two runs, never ten, and never two concurrent ``docker compose`` invocations for one app.

**Verify is a real probe.**  A deploy that "succeeded" while the app returns 502 is a failed
deploy, so the run only reaches ``success`` when the app's public URL answers.

Storage:

* ``config/cicd-pipelines.yaml`` — gitignored, mode 0600, holds webhook secrets in clear
  (HMAC needs the raw secret).  Handled exactly like ``config/ai-providers.yaml``.
* ``ecosystem-stack/config/generated/cicd-runs.jsonl`` — append-only run history, last record
  per ``run_id`` wins, capped and rotated.

Everything git-related is delegated to ``dashboard/git_source.py``; this module never shells out
to ``git`` itself.  That module may be absent, in which case the ``pull`` step fails with a
clear message instead of raising.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIPELINES_REL = "config/cicd-pipelines.yaml"
RUNS_REL = "ecosystem-stack/config/generated/cicd-runs.jsonl"

#: Runs kept in the history file after a rotation.
MAX_RUNS = 400
#: Rotate the JSONL when it grows past this (append-only, one snapshot per state change).
MAX_RUNS_BYTES = 6 * 1024 * 1024
#: Per-run captured log ceiling. Deploy output for a large compose project is chatty.
MAX_LOG_CHARS = 60_000
#: Per-step captured detail ceiling.
MAX_STEP_DETAIL_CHARS = 4_000

#: Deploy is the long pole — a cold `docker compose build` on a big app.
DEPLOY_TIMEOUT_SEC = 40 * 60
BUILD_TIMEOUT_SEC = 30 * 60
PULL_TIMEOUT_SEC = 10 * 60

#: The app has just been recreated; give it a moment before calling the deploy bad.
VERIFY_ATTEMPTS = 6
VERIFY_INTERVAL_SEC = 5.0

PROVIDERS = ("github", "gitlab", "generic", "auto")
DEFAULT_PROVIDER = "auto"

GITHUB_SIG_HEADER = "X-Hub-Signature-256"
GITHUB_EVENT_HEADER = "X-GitHub-Event"
GITHUB_DELIVERY_HEADER = "X-GitHub-Delivery"
GITLAB_TOKEN_HEADER = "X-Gitlab-Token"
GITLAB_EVENT_HEADER = "X-Gitlab-Event"
GENERIC_SIG_HEADER = "X-LEco-Signature"
GENERIC_EVENT_HEADER = "X-LEco-Event"

#: Webhook bodies larger than this are refused before any HMAC work.
MAX_WEBHOOK_BODY_BYTES = 2 * 1024 * 1024

_ZERO_SHA_RE = re.compile(r"^0{7,64}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$", re.IGNORECASE)
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)
_PIPELINE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_STEP_NAMES = ("pull", "build", "deploy", "verify")

_LOG = None  # set lazily to a logging.Logger


def _logger():
    global _LOG
    if _LOG is None:
        import logging

        _LOG = logging.getLogger("leco.cicd")
    return _LOG


# ---------------------------------------------------------------------------
# Paths (resolved per call so tests can repoint DASHBOARD_PROJECT_ROOT)
# ---------------------------------------------------------------------------


def project_root() -> Path:
    return Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))


def pipelines_path() -> Path:
    override = (os.getenv("LECO_CICD_PIPELINES_FILE") or "").strip()
    if override:
        return Path(override)
    return project_root() / PIPELINES_REL


def runs_path() -> Path:
    override = (os.getenv("LECO_CICD_RUNS_FILE") or "").strip()
    if override:
        return Path(override)
    return project_root() / RUNS_REL


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ms_since(t0: float) -> int:
    return max(0, int((time.perf_counter() - t0) * 1000))


def _clip(text: Any, limit: int) -> str:
    s = "" if text is None else str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n… [truncated, {len(s) - limit} more chars]"


def _truthy(raw: Any) -> bool:
    return str(raw if raw is not None else "").strip().lower() in ("1", "true", "yes", "on")


def _short(sha: str) -> str:
    s = str(sha or "").strip()
    return s[:8] if s else ""


def branch_from_ref(ref: str) -> str:
    """``refs/heads/main`` → ``main``. Tags and other refs return ''."""
    r = str(ref or "").strip()
    if r.startswith("refs/heads/"):
        return r[len("refs/heads/") :]
    if r.startswith("refs/"):
        return ""
    return r


def _is_zero_sha(sha: Any) -> bool:
    s = str(sha or "").strip()
    return not s or bool(_ZERO_SHA_RE.match(s))


# ---------------------------------------------------------------------------
# Pipeline store — config/cicd-pipelines.yaml (gitignored, 0600)
# ---------------------------------------------------------------------------

_STORE_LOCK = threading.RLock()


def _yaml():
    try:
        import yaml  # type: ignore

        return yaml
    except Exception:
        return None


def _read_store() -> dict[str, Any]:
    path = pipelines_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"pipelines": []}
    except OSError as exc:
        _logger().warning("cicd: cannot read %s: %s", path, exc)
        return {"pipelines": []}
    if not raw.strip():
        return {"pipelines": []}
    yaml = _yaml()
    data: Any = None
    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
        except Exception as exc:
            _logger().error("cicd: %s is not valid YAML: %s", path, exc)
            return {"pipelines": [], "_error": f"{path.name} is not valid YAML"}
    else:
        # JSON is a YAML subset; without PyYAML we still round-trip our own writes.
        try:
            data = json.loads(raw)
        except Exception:
            return {"pipelines": [], "_error": f"{path.name} unreadable without PyYAML"}
    if not isinstance(data, dict):
        return {"pipelines": []}
    items = data.get("pipelines")
    if not isinstance(items, list):
        items = []
    return {"pipelines": [p for p in items if isinstance(p, dict)]}


def _write_store(store: dict[str, Any]) -> None:
    path = pipelines_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pipelines": store.get("pipelines") or []}
    yaml = _yaml()
    if yaml is not None:
        text = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False, allow_unicode=True)
    else:
        text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_pipelines() -> list[dict[str, Any]]:
    with _STORE_LOCK:
        return list(_read_store().get("pipelines") or [])


def get_pipeline(pipeline_id: str) -> dict[str, Any] | None:
    pid = str(pipeline_id or "").strip()
    if not pid:
        return None
    for p in load_pipelines():
        if str(p.get("id") or "") == pid:
            return p
    return None


def _generate_secret() -> str:
    return secrets.token_hex(32)


def _new_pipeline_id(app_slug: str, existing: Iterable[str]) -> str:
    base = re.sub(r"[^a-z0-9-]+", "-", str(app_slug or "app").lower()).strip("-") or "app"
    taken = set(existing)
    for _ in range(50):
        candidate = f"{base}-{uuid.uuid4().hex[:6]}"
        if candidate not in taken:
            return candidate
    return f"{base}-{uuid.uuid4().hex}"


def public_pipeline(pipeline: dict[str, Any], *, webhook_base: str = "") -> dict[str, Any]:
    """Pipeline as returned by the API. The webhook secret is never included."""
    pid = str(pipeline.get("id") or "")
    out = {
        "id": pid,
        "app_slug": pipeline.get("app_slug") or "",
        "repo_url": pipeline.get("repo_url") or "",
        "branch": pipeline.get("branch") or "",
        "provider": pipeline.get("provider") or DEFAULT_PROVIDER,
        "auto_deploy": bool(pipeline.get("auto_deploy", True)),
        "verify_url": pipeline.get("verify_url") or "",
        "build_hook_service": pipeline.get("build_hook_service") or "",
        "credential_id": pipeline.get("credential_id") or "",
        "enabled": bool(pipeline.get("enabled", True)),
        "created_at": pipeline.get("created_at") or "",
        "updated_at": pipeline.get("updated_at") or "",
        "secret_set": bool(pipeline.get("secret")),
        "secret_created_at": pipeline.get("secret_created_at") or "",
        "last_deployed_sha": pipeline.get("last_deployed_sha") or "",
        "last_deployed_at": pipeline.get("last_deployed_at") or "",
        "previous_deployed_sha": pipeline.get("previous_deployed_sha") or "",
        "workdir": pipeline.get("workdir") or "",
        "webhook_path": f"/api/cicd/webhook/{pid}",
    }
    if webhook_base:
        out["webhook_url"] = webhook_base.rstrip("/") + out["webhook_path"]
    out["busy"] = is_busy(pid)
    return out


class PipelineError(ValueError):
    """Invalid pipeline configuration supplied by an operator."""


def _validate_fields(data: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}

    def present(key: str) -> bool:
        return key in data

    if present("app_slug") or not partial:
        slug = str(data.get("app_slug") or "").strip()
        if not slug or not _SLUG_RE.match(slug):
            raise PipelineError("app_slug is required (registered app id, e.g. botfeed)")
        out["app_slug"] = slug

    if present("repo_url") or not partial:
        repo = str(data.get("repo_url") or "").strip()
        if not repo:
            raise PipelineError("repo_url is required")
        if not re.match(r"^(https?://|ssh://|git@)", repo):
            raise PipelineError("repo_url must be an http(s), ssh:// or git@ URL")
        if len(repo) > 512:
            raise PipelineError("repo_url is too long")
        out["repo_url"] = repo

    if present("branch") or not partial:
        branch = str(data.get("branch") or "main").strip()
        if not _BRANCH_RE.match(branch):
            raise PipelineError("branch contains characters that are not valid in a git ref")
        out["branch"] = branch

    if present("provider"):
        provider = str(data.get("provider") or DEFAULT_PROVIDER).strip().lower()
        if provider not in PROVIDERS:
            raise PipelineError(f"provider must be one of {', '.join(PROVIDERS)}")
        out["provider"] = provider
    elif not partial:
        out["provider"] = DEFAULT_PROVIDER

    if present("verify_url"):
        url = str(data.get("verify_url") or "").strip()
        if url and not re.match(r"^https?://", url):
            raise PipelineError("verify_url must be an http(s) URL")
        if len(url) > 512:
            raise PipelineError("verify_url is too long")
        out["verify_url"] = url
    elif not partial:
        out["verify_url"] = ""

    if present("build_hook_service"):
        svc = str(data.get("build_hook_service") or "").strip()
        if svc and not _SERVICE_RE.match(svc):
            raise PipelineError(
                "build_hook_service must be a compose service name declared by the app "
                "(letters, digits, dot, dash, underscore). Arbitrary shell commands are not accepted."
            )
        out["build_hook_service"] = svc
    elif not partial:
        out["build_hook_service"] = ""

    if present("credential_id"):
        # Names a credential saved in git_source's own gitignored store. Only the id lives
        # here — the token or key never enters the pipeline config, which is why a pipeline
        # can be shown in full without exposing anything.
        cid = str(data.get("credential_id") or "").strip()
        if len(cid) > 128:
            raise PipelineError("credential_id is too long")
        out["credential_id"] = cid
    elif not partial:
        out["credential_id"] = ""

    if present("auto_deploy"):
        out["auto_deploy"] = bool(data.get("auto_deploy")) if isinstance(data.get("auto_deploy"), bool) else _truthy(data.get("auto_deploy"))
    elif not partial:
        out["auto_deploy"] = True

    if present("enabled"):
        out["enabled"] = bool(data.get("enabled")) if isinstance(data.get("enabled"), bool) else _truthy(data.get("enabled"))
    elif not partial:
        out["enabled"] = True

    return out


def create_pipeline(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Create a pipeline. Returns ``(pipeline, secret)`` — the secret is shown once, then never again."""
    fields = _validate_fields(data or {}, partial=False)
    secret = _generate_secret()
    with _STORE_LOCK:
        store = _read_store()
        items = list(store.get("pipelines") or [])
        pid = _new_pipeline_id(fields["app_slug"], [str(p.get("id") or "") for p in items])
        record = {
            "id": pid,
            **fields,
            "secret": secret,
            "secret_created_at": _now(),
            "created_at": _now(),
            "updated_at": _now(),
            "workdir": "",
            "last_deployed_sha": "",
            "last_deployed_at": "",
            "previous_deployed_sha": "",
        }
        items.append(record)
        store["pipelines"] = items
        _write_store(store)
    return record, secret


def update_pipeline(pipeline_id: str, data: dict[str, Any]) -> dict[str, Any]:
    fields = _validate_fields(data or {}, partial=True)
    with _STORE_LOCK:
        store = _read_store()
        items = list(store.get("pipelines") or [])
        for idx, p in enumerate(items):
            if str(p.get("id") or "") == str(pipeline_id):
                merged = {**p, **fields, "updated_at": _now()}
                items[idx] = merged
                store["pipelines"] = items
                _write_store(store)
                return merged
    raise PipelineError(f"unknown pipeline {pipeline_id!r}")


def delete_pipeline(pipeline_id: str) -> bool:
    with _STORE_LOCK:
        store = _read_store()
        items = list(store.get("pipelines") or [])
        keep = [p for p in items if str(p.get("id") or "") != str(pipeline_id)]
        if len(keep) == len(items):
            return False
        store["pipelines"] = keep
        _write_store(store)
        return True


def rotate_secret(pipeline_id: str) -> str:
    """Issue a new webhook secret. The old one stops working immediately."""
    secret = _generate_secret()
    with _STORE_LOCK:
        store = _read_store()
        items = list(store.get("pipelines") or [])
        for idx, p in enumerate(items):
            if str(p.get("id") or "") == str(pipeline_id):
                items[idx] = {**p, "secret": secret, "secret_created_at": _now(), "updated_at": _now()}
                store["pipelines"] = items
                _write_store(store)
                return secret
    raise PipelineError(f"unknown pipeline {pipeline_id!r}")


def _record_deploy_sha(pipeline_id: str, sha: str) -> None:
    """Remember what is deployed now, and what was deployed before it — that is the rollback target."""
    sha = str(sha or "").strip()
    if not sha:
        return
    with _STORE_LOCK:
        store = _read_store()
        items = list(store.get("pipelines") or [])
        for idx, p in enumerate(items):
            if str(p.get("id") or "") != str(pipeline_id):
                continue
            current = str(p.get("last_deployed_sha") or "").strip()
            updated = {**p, "last_deployed_sha": sha, "last_deployed_at": _now(), "updated_at": _now()}
            if current and current != sha:
                updated["previous_deployed_sha"] = current
            items[idx] = updated
            store["pipelines"] = items
            _write_store(store)
            return


def _record_workdir(pipeline_id: str, workdir: str) -> None:
    workdir = str(workdir or "").strip()
    if not workdir:
        return
    with _STORE_LOCK:
        store = _read_store()
        items = list(store.get("pipelines") or [])
        for idx, p in enumerate(items):
            if str(p.get("id") or "") == str(pipeline_id) and str(p.get("workdir") or "") != workdir:
                items[idx] = {**p, "workdir": workdir}
                store["pipelines"] = items
                _write_store(store)
                return


# ---------------------------------------------------------------------------
# Signature verification — runs before anything else on the webhook path
# ---------------------------------------------------------------------------


def verify_github_signature(secret: str, body: bytes, header: str) -> bool:
    """GitHub: ``X-Hub-Signature-256: sha256=<hex hmac of the raw body>``."""
    sig = str(header or "").strip().lower()
    if not secret or not sig or not sig.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def verify_gitlab_token(secret: str, header: str) -> bool:
    """GitLab: ``X-Gitlab-Token`` is the shared secret verbatim (GitLab does not sign the body)."""
    token = str(header or "")
    if not secret or not token:
        return False
    return hmac.compare_digest(secret, token)


def verify_generic_signature(secret: str, body: bytes, header: str) -> bool:
    """Generic signed variant: ``X-LEco-Signature: sha256=<hex hmac of the raw body>``."""
    sig = str(header or "").strip()
    if not secret or not sig:
        return False
    if sig.lower().startswith("sha256="):
        sig = sig[7:]
    expected = hmac.new(secret.encode("utf-8"), body or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig.lower())


def verify_signature(pipeline: dict[str, Any], headers: Any, body: bytes) -> tuple[bool, str]:
    """Authenticate a webhook request.

    This is the **only** credential check on the webhook path. Returns ``(ok, scheme)``; the
    scheme is for the run record, never for the HTTP response — a rejected caller learns
    nothing beyond "forbidden".
    """
    secret = str((pipeline or {}).get("secret") or "")
    if not secret:
        return False, ""

    def hdr(name: str) -> str:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        return "" if value is None else str(value)

    configured = str((pipeline or {}).get("provider") or DEFAULT_PROVIDER).strip().lower()

    checks: list[tuple[str, Callable[[], bool]]] = []
    if configured in ("github", "auto"):
        checks.append(("github", lambda: verify_github_signature(secret, body, hdr(GITHUB_SIG_HEADER))))
    if configured in ("gitlab", "auto"):
        checks.append(("gitlab", lambda: verify_gitlab_token(secret, hdr(GITLAB_TOKEN_HEADER))))
    if configured in ("generic", "auto"):
        checks.append(("generic", lambda: verify_generic_signature(secret, body, hdr(GENERIC_SIG_HEADER))))

    for scheme, check in checks:
        try:
            if check():
                return True, scheme
        except Exception:
            continue
    return False, ""


# ---------------------------------------------------------------------------
# Payload parsing
# ---------------------------------------------------------------------------


def parse_event(headers: Any, body: bytes, scheme: str) -> dict[str, Any]:
    """Normalize a GitHub / GitLab / generic push payload.

    Returns ``{action: run|ignore, reason, ref, branch, sha, subject, ...}``. Only ever called
    **after** the signature verified.
    """

    def hdr(name: str) -> str:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        return "" if value is None else str(value)

    event_name = (hdr(GITHUB_EVENT_HEADER) or hdr(GITLAB_EVENT_HEADER) or hdr(GENERIC_EVENT_HEADER) or "").strip()
    delivery = (hdr(GITHUB_DELIVERY_HEADER) or hdr("X-Gitlab-Event-UUID") or hdr("X-LEco-Delivery") or "").strip()

    out: dict[str, Any] = {
        "provider": scheme or "generic",
        "event": event_name,
        "delivery_id": delivery[:128],
        "ref": "",
        "branch": "",
        "sha": "",
        "subject": "",
        "pusher": "",
        "commit_count": 0,
        "action": "ignore",
        "reason": "",
    }

    try:
        payload = json.loads((body or b"").decode("utf-8") or "{}")
    except Exception:
        out["reason"] = "payload is not JSON"
        return out
    if not isinstance(payload, dict):
        out["reason"] = "payload is not a JSON object"
        return out

    lowered = event_name.lower()
    if lowered in ("ping",):
        out["reason"] = "ping"
        return out

    object_kind = str(payload.get("object_kind") or "").strip().lower()
    is_push = (
        lowered == "push"
        or lowered.startswith("push hook")
        or object_kind == "push"
        or (not lowered and ("ref" in payload or "after" in payload))
    )
    if not is_push:
        out["reason"] = f"not a push event ({event_name or object_kind or 'unknown'})"
        return out

    ref = str(payload.get("ref") or "").strip()
    out["ref"] = ref
    out["branch"] = branch_from_ref(ref)

    commits = payload.get("commits")
    commits = commits if isinstance(commits, list) else []
    total = payload.get("total_commits_count")
    try:
        total_int = int(total)
    except (TypeError, ValueError):
        total_int = len(commits)
    out["commit_count"] = max(len(commits), total_int if total_int >= 0 else 0)

    after = str(payload.get("after") or payload.get("checkout_sha") or "").strip()
    head = payload.get("head_commit") if isinstance(payload.get("head_commit"), dict) else None
    if not after and head:
        after = str(head.get("id") or "").strip()
    if not after and commits and isinstance(commits[-1], dict):
        after = str(commits[-1].get("id") or "").strip()
    out["sha"] = after

    subject = ""
    if head:
        subject = str(head.get("message") or "")
    if not subject and commits and isinstance(commits[-1], dict):
        subject = str(commits[-1].get("message") or "")
    out["subject"] = subject.strip().splitlines()[0][:200] if subject.strip() else ""

    pusher = payload.get("pusher")
    if isinstance(pusher, dict):
        out["pusher"] = str(pusher.get("name") or pusher.get("email") or "")[:120]
    elif isinstance(pusher, str):
        out["pusher"] = pusher[:120]
    if not out["pusher"]:
        out["pusher"] = str(payload.get("user_username") or payload.get("user_name") or "")[:120]

    # Branch delete: GitHub sets deleted=true and after=000…; GitLab sends after=000… with no commits.
    if _truthy(payload.get("deleted")) or _is_zero_sha(after):
        out["reason"] = "branch deleted (no commits to deploy)"
        return out
    if out["commit_count"] <= 0:
        out["reason"] = "push carried no commits"
        return out
    if not out["branch"]:
        out["reason"] = f"ref {ref or '(empty)'} is not a branch"
        return out

    out["action"] = "run"
    return out


# ---------------------------------------------------------------------------
# Run history — append-only JSONL, last snapshot per run_id wins
# ---------------------------------------------------------------------------

_RUNS_LOCK = threading.RLock()


def _append_run_snapshot(run: dict[str, Any]) -> None:
    path = runs_path()
    with _RUNS_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(run, ensure_ascii=False) + "\n")
        except OSError as exc:
            _logger().warning("cicd: cannot write run history %s: %s", path, exc)
            return
        try:
            if path.stat().st_size > MAX_RUNS_BYTES:
                _rotate_runs_locked()
        except OSError:
            pass


def _rotate_runs_locked() -> None:
    """Collapse to the newest MAX_RUNS runs and rewrite the file atomically."""
    path = runs_path()
    runs = _read_runs_collapsed()
    keep = runs[:MAX_RUNS]
    keep.reverse()  # write oldest-first so the file stays chronological
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for run in keep:
                fh.write(json.dumps(run, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except OSError as exc:
        _logger().warning("cicd: run history rotation failed: %s", exc)


def _read_runs_collapsed() -> list[dict[str, Any]]:
    """All persisted runs, newest first, one entry per run_id (the last snapshot written)."""
    path = runs_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError:
        return []
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue  # truncated tail / partial write is expected on an append-only file
        if not isinstance(rec, dict):
            continue
        rid = str(rec.get("run_id") or "")
        if not rid:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = rec
    out = [by_id[rid] for rid in order]
    out.reverse()  # newest first — this project's convention
    with _RUN_LOCK:
        live_ids = set(_ACTIVE_RUNS)
    for run in out:
        # Runs are executed by threads inside this process. A persisted run still marked
        # running/queued that this process does not own was cut off by a restart or a crash —
        # say so rather than leaving a row that spins forever.
        if str(run.get("status") or "") in ("running", "queued") and str(run.get("run_id") or "") not in live_ids:
            run["status"] = "interrupted"
            if not run.get("outcome"):
                run["outcome"] = "interrupted — the dashboard restarted while this run was in flight"
    return out


def list_runs(
    *,
    pipeline_id: str | None = None,
    status: str | None = None,
    trigger: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Run history, newest first, with filters and pagination."""
    try:
        limit_i = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        limit_i = 25
    try:
        offset_i = max(0, int(offset))
    except (TypeError, ValueError):
        offset_i = 0

    persisted = _read_runs_collapsed()
    with _RUN_LOCK:
        live = {rid: dict(run) for rid, run in _ACTIVE_RUNS.items()}
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for run in live.values():
        merged.append(run)
        seen.add(str(run.get("run_id") or ""))
    for run in persisted:
        rid = str(run.get("run_id") or "")
        if rid in seen:
            continue
        merged.append(run)
        seen.add(rid)
    merged.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)

    def keep(run: dict[str, Any]) -> bool:
        if pipeline_id and str(run.get("pipeline_id") or "") != pipeline_id:
            return False
        if status and str(run.get("status") or "") != status:
            return False
        if trigger and str(run.get("trigger") or "") != trigger:
            return False
        return True

    filtered = [r for r in merged if keep(r)]
    page = filtered[offset_i : offset_i + limit_i]
    return {
        "ok": True,
        "runs": [_run_summary(r) for r in page],
        "total": len(filtered),
        "limit": limit_i,
        "offset": offset_i,
        "generated_at": _now(),
    }


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    """List view: everything except the (potentially large) captured log."""
    out = {k: v for k, v in run.items() if k != "log"}
    out["log_chars"] = len(str(run.get("log") or ""))
    return out


def get_run(run_id: str) -> dict[str, Any] | None:
    rid = str(run_id or "").strip()
    if not rid:
        return None
    with _RUN_LOCK:
        live = _ACTIVE_RUNS.get(rid)
        if live:
            return dict(live)
    for run in _read_runs_collapsed():
        if str(run.get("run_id") or "") == rid:
            return run
    return None


# ---------------------------------------------------------------------------
# Scheduler — one run per pipeline at a time, newest pending SHA wins
# ---------------------------------------------------------------------------

_RUN_LOCK = threading.RLock()
#: pipeline_id → run_id currently executing
_RUNNING: dict[str, str] = {}
#: pipeline_id → the single queued request (newest wins)
_PENDING: dict[str, dict[str, Any]] = {}
#: run_id → live run record (merged into list_runs so the UI sees in-flight runs)
_ACTIVE_RUNS: dict[str, dict[str, Any]] = {}
#: recently seen webhook delivery ids, for exact retry de-duplication
_SEEN_DELIVERIES: dict[str, float] = {}
_SEEN_TTL_SEC = 900.0


def is_busy(pipeline_id: str) -> bool:
    with _RUN_LOCK:
        return str(pipeline_id) in _RUNNING or str(pipeline_id) in _PENDING


def _remember_delivery(key: str) -> bool:
    """True if this delivery id is new. Git hosts retry deliveries; a retry is not a new push."""
    if not key:
        return True
    now = time.time()
    with _RUN_LOCK:
        for k, ts in list(_SEEN_DELIVERIES.items()):
            if now - ts > _SEEN_TTL_SEC:
                _SEEN_DELIVERIES.pop(k, None)
        if key in _SEEN_DELIVERIES:
            return False
        _SEEN_DELIVERIES[key] = now
        return True


def _new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:16]


def _new_run(pipeline: dict[str, Any], *, trigger: str, event: dict[str, Any], target_sha: str = "", rollback_of: str = "") -> dict[str, Any]:
    return {
        "run_id": _new_run_id(),
        "pipeline_id": str(pipeline.get("id") or ""),
        "app_slug": str(pipeline.get("app_slug") or ""),
        "branch": str(pipeline.get("branch") or ""),
        "trigger": trigger,
        "status": "queued",
        "outcome": "",
        "event": event or {},
        "requested_ref": target_sha or str(pipeline.get("branch") or ""),
        "commit_sha": target_sha or str((event or {}).get("sha") or ""),
        "commit_subject": str((event or {}).get("subject") or ""),
        "previous_sha": str(pipeline.get("last_deployed_sha") or ""),
        "rollback_of": rollback_of,
        "coalesced": 0,
        "started_at": _now(),
        "finished_at": "",
        "duration_ms": 0,
        "steps": [],
        "log": "",
        "verify": {},
    }


def enqueue(
    pipeline: dict[str, Any],
    *,
    trigger: str,
    event: dict[str, Any] | None = None,
    target_sha: str = "",
    rollback_of: str = "",
    runner: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Schedule a run.

    One run per pipeline executes at a time. A request for a SHA that is already running or
    already queued is dropped (and counted on the run that will cover it); a request for a
    different SHA replaces the single queued slot so the newest commit wins. That is what keeps
    ten pushes in a minute from becoming ten concurrent deploys.
    """
    pid = str(pipeline.get("id") or "")
    event = dict(event or {})
    want_sha = str(target_sha or event.get("sha") or "").strip()

    with _RUN_LOCK:
        active_run_id = _RUNNING.get(pid)
        if active_run_id:
            active = _ACTIVE_RUNS.get(active_run_id) or {}
            active_sha = str(active.get("commit_sha") or "").strip()
            if want_sha and active_sha and want_sha == active_sha and not rollback_of:
                active["coalesced"] = int(active.get("coalesced") or 0) + 1
                return {
                    "ok": True,
                    "queued": False,
                    "coalesced": True,
                    "run_id": active_run_id,
                    "message": f"commit {_short(want_sha)} is already deploying; request folded into run {active_run_id}",
                }
            pending = _PENDING.get(pid)
            if pending and str(pending.get("target_sha") or "") == want_sha and not rollback_of:
                pending["coalesced"] = int(pending.get("coalesced") or 0) + 1
                return {
                    "ok": True,
                    "queued": True,
                    "coalesced": True,
                    "run_id": "",
                    "message": f"commit {_short(want_sha)} is already queued behind run {active_run_id}",
                }
            _PENDING[pid] = {
                "pipeline_id": pid,
                "trigger": trigger,
                "event": event,
                "target_sha": want_sha,
                "rollback_of": rollback_of,
                "coalesced": (pending or {}).get("coalesced", 0),
                "runner": runner,
            }
            return {
                "ok": True,
                "queued": True,
                "coalesced": False,
                "run_id": "",
                "message": f"run {active_run_id} is in flight; this push is queued (newest commit wins)",
            }

        run = _new_run(pipeline, trigger=trigger, event=event, target_sha=want_sha, rollback_of=rollback_of)
        _RUNNING[pid] = run["run_id"]
        _ACTIVE_RUNS[run["run_id"]] = run

    _append_run_snapshot(run)
    thread = threading.Thread(
        target=_worker,
        args=(pipeline, run, runner or execute_run),
        name=f"cicd-{pid}",
        daemon=True,
    )
    thread.start()
    return {"ok": True, "queued": False, "coalesced": False, "run_id": run["run_id"], "message": "run started"}


def _worker(pipeline: dict[str, Any], run: dict[str, Any], runner: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
    pid = str(pipeline.get("id") or "")
    current_run = run
    current_pipeline = pipeline
    current_runner = runner
    while True:
        try:
            current_runner(current_pipeline, current_run)
        except Exception as exc:  # a crashed pipeline must still finish its record
            _logger().exception("cicd: pipeline %s crashed", pid)
            _finish_run(current_run, "failed", f"pipeline crashed: {exc}")
        finally:
            with _RUN_LOCK:
                _ACTIVE_RUNS.pop(current_run["run_id"], None)
                _RUNNING.pop(pid, None)
                pending = _PENDING.pop(pid, None)
            if not pending:
                return
            fresh = get_pipeline(pid) or current_pipeline
            next_run = _new_run(
                fresh,
                trigger=str(pending.get("trigger") or "webhook"),
                event=pending.get("event") or {},
                target_sha=str(pending.get("target_sha") or ""),
                rollback_of=str(pending.get("rollback_of") or ""),
            )
            next_run["coalesced"] = int(pending.get("coalesced") or 0)
            with _RUN_LOCK:
                _RUNNING[pid] = next_run["run_id"]
                _ACTIVE_RUNS[next_run["run_id"]] = next_run
            _append_run_snapshot(next_run)
            current_run = next_run
            current_pipeline = fresh
            current_runner = pending.get("runner") or execute_run


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------


def _log_line(run: dict[str, Any], text: str) -> None:
    if not text:
        return
    body = str(run.get("log") or "") + (text if text.endswith("\n") else text + "\n")
    run["log"] = _clip(body, MAX_LOG_CHARS)


def _start_step(run: dict[str, Any], name: str, note: str = "") -> dict[str, Any]:
    step = {
        "name": name,
        "status": "running",
        "started_at": _now(),
        "duration_ms": 0,
        "detail": note,
    }
    run.setdefault("steps", []).append(step)
    run["status"] = "running"
    _log_line(run, f"── {name} ──")
    if note:
        _log_line(run, note)
    _append_run_snapshot(run)
    return step


def _end_step(run: dict[str, Any], step: dict[str, Any], status: str, detail: str = "", t0: float | None = None) -> None:
    step["status"] = status
    if t0 is not None:
        step["duration_ms"] = _ms_since(t0)
    if detail:
        step["detail"] = _clip(detail, MAX_STEP_DETAIL_CHARS)
    head = detail.splitlines()[0][:200] if detail else ""
    _log_line(run, f"{str(step['name']).upper()}: {status}" + (f" — {head}" if head else ""))
    _append_run_snapshot(run)


def _finish_run(run: dict[str, Any], status: str, outcome: str) -> dict[str, Any]:
    run["status"] = status
    run["outcome"] = _clip(outcome, 400)
    run["finished_at"] = _now()
    try:
        started = datetime.fromisoformat(str(run.get("started_at") or ""))
        run["duration_ms"] = max(0, int((datetime.now(timezone.utc) - started).total_seconds() * 1000))
    except Exception:
        run["duration_ms"] = run.get("duration_ms") or 0
    _log_line(run, f"RESULT: {status} — {outcome}")
    _append_run_snapshot(run)
    return run


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


class GitSourceUnavailable(RuntimeError):
    pass


def _git_source_module():
    """Import the sibling git source module, which may not be installed yet."""
    try:
        import git_source  # type: ignore

        return git_source
    except Exception as exc:
        raise GitSourceUnavailable(
            "Git source module unavailable — dashboard/git_source.py could not be imported "
            f"({exc}). CI/CD delegates all git work to it and never shells out to git itself."
        ) from exc


_CLONE_CANDIDATES = (
    "clone_or_update",
    "clone_or_update_repo",
    "clone_repo",
    "ensure_clone",
    "git_clone",
    "clone",
    "update_repo",
    "handle_clone",
)


def git_clone_or_update(
    repo_url: str,
    ref: str,
    *,
    dir_name: str = "",
    app_id: str = "",
    credential_id: str = "",
) -> dict[str, Any]:
    """Call ``git_source``'s clone/update entry point.

    Contract (``POST /api/leco/git/clone``): returns ``{path, ref, sha, subject, fresh_clone}``.
    The function name and calling convention are probed rather than hard-coded, because that
    module is authored separately; a missing entry point is reported as a clear failure and
    never faked.

    ``reset=True`` is passed when accepted: the CI clone is machine-owned and the remote is the
    source of truth, so a working tree left detached by a rollback must not wedge the next
    deploy. Nobody edits that tree by hand.
    """
    module = _git_source_module()
    func = None
    chosen = ""
    for name in _CLONE_CANDIDATES:
        candidate = getattr(module, name, None)
        if callable(candidate):
            func = candidate
            chosen = name
            break
    if func is None:
        raise GitSourceUnavailable(
            "Git source module unavailable — dashboard/git_source.py exposes no clone entry point "
            f"(looked for: {', '.join(_CLONE_CANDIDATES)})."
        )

    extras: dict[str, Any] = {"reset": True}
    if dir_name:
        extras["dir_name"] = dir_name
    if app_id:
        extras["app_id"] = app_id
    if credential_id:
        # A pipeline against a private repository has no interactive operator to answer a
        # prompt, so the saved credential must be named explicitly. git_source resolves the
        # id against its own gitignored store; the secret never passes through this module.
        extras["credential_id_value"] = credential_id

    extras_no_cred = {k: v for k, v in extras.items() if k != "credential_id_value"}
    attempts = (
        lambda: func(url=repo_url, ref=ref, **extras),
        lambda: func(repo_url=repo_url, ref=ref, **extras),
        lambda: func(url=repo_url, ref=ref, **extras_no_cred),
        lambda: func(repo_url=repo_url, ref=ref, **extras_no_cred),
        lambda: func(url=repo_url, ref=ref),
        lambda: func(repo_url=repo_url, ref=ref),
        lambda: func(repo_url, ref),
        lambda: func({"repo_url": repo_url, "ref": ref}),
        lambda: func(repo_url),
    )
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            result = attempt()
        except TypeError as exc:
            last_error = exc
            continue
        return _normalize_clone_result(result, ref, chosen)
    raise GitSourceUnavailable(
        f"Git source module unavailable — could not call git_source.{chosen}() with the documented "
        f"clone contract (repo_url, ref): {last_error}"
    )


def _normalize_clone_result(result: Any, ref: str, func_name: str) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise GitSourceUnavailable(
            f"git_source.{func_name}() returned {type(result).__name__}, expected the documented "
            "{path, ref, sha, subject, fresh_clone} mapping"
        )
    if result.get("ok") is False:
        raise RuntimeError(str(result.get("error") or "git clone/update failed"))
    inner = result.get("result") if isinstance(result.get("result"), dict) else result
    action = str(inner.get("action") or "")
    return {
        # ``path_field`` is the wsp:-prefixed form the rest of the dashboard passes around.
        "path": str(inner.get("path_field") or inner.get("path") or ""),
        "ref": str(inner.get("ref") or ref),
        "sha": str(inner.get("sha") or inner.get("commit") or ""),
        "subject": str(inner.get("subject") or inner.get("commit_subject") or inner.get("message") or ""),
        "fresh_clone": bool(inner.get("fresh_clone")) or action in ("cloned", "clone"),
        "action": action,
        "notes": inner.get("notes") if isinstance(inner.get("notes"), list) else [],
    }


def step_pull(run: dict[str, Any], pipeline: dict[str, Any], ref: str) -> dict[str, Any]:
    step = _start_step(run, "pull", f"{pipeline.get('repo_url')} @ {ref}")
    t0 = time.perf_counter()
    slug = str(pipeline.get("app_slug") or "").strip()
    try:
        result = git_clone_or_update(
            str(pipeline.get("repo_url") or ""),
            ref,
            # Stable per-pipeline working tree so repeated deploys update instead of re-cloning.
            dir_name=f"cicd-{pipeline.get('id')}" if pipeline.get("id") else "",
            app_id=slug,
            credential_id=str(pipeline.get("credential_id") or "").strip(),
        )
    except GitSourceUnavailable as exc:
        _end_step(run, step, "failed", str(exc), t0)
        raise
    except Exception as exc:
        _end_step(run, step, "failed", f"git clone/update failed: {exc}", t0)
        raise
    sha = result.get("sha") or ""
    subject = result.get("subject") or ""
    if sha:
        run["commit_sha"] = sha
    if subject and not run.get("commit_subject"):
        run["commit_subject"] = subject[:200]
    if result.get("path"):
        run["workdir"] = result["path"]
        _record_workdir(str(pipeline.get("id") or ""), result["path"])
    detail = f"{_short(sha) or '(unknown sha)'} {subject}".strip()
    if result.get("fresh_clone"):
        detail = "fresh clone · " + detail
    _end_step(run, step, "ok", detail, t0)
    return result


def _compose_meta(app_slug: str) -> dict[str, Any] | None:
    try:
        from leco_control import leco_meta_for_slug

        return leco_meta_for_slug(app_slug)
    except Exception as exc:
        _logger().warning("cicd: cannot resolve compose metadata for %s: %s", app_slug, exc)
        return None


def step_build(run: dict[str, Any], pipeline: dict[str, Any]) -> None:
    """Optional build/test step — the app's own compose service, never a host shell string.

    ``docker compose … run --rm --no-deps <service>`` with **no command override**: the command
    lives in the application's compose file, so nothing from the (webhook-reachable) pipeline
    config is ever interpreted as a shell command on the host.
    """
    service = str(pipeline.get("build_hook_service") or "").strip()
    if not service:
        return
    if not _SERVICE_RE.match(service):
        raise RuntimeError(f"invalid build hook service name {service!r}")

    step = _start_step(run, "build", f"compose service {service} (command comes from the app's compose file)")
    t0 = time.perf_counter()
    meta = _compose_meta(str(pipeline.get("app_slug") or ""))
    if not meta or not meta.get("compose_tail"):
        detail = (
            f"app {pipeline.get('app_slug')!r} has no compose project, so the build hook cannot run "
            "inside the app's toolchain"
        )
        _end_step(run, step, "failed", detail, t0)
        raise RuntimeError(detail)

    import subprocess

    argv = ["docker", "compose", *list(meta["compose_tail"]), "run", "--rm", "--no-deps", service]
    cwd = str(meta.get("compose_cwd") or meta.get("root") or ".")
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=BUILD_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        _end_step(run, step, "failed", f"build hook timed out after {BUILD_TIMEOUT_SEC}s", t0)
        raise RuntimeError("build hook timed out")
    except OSError as exc:
        _end_step(run, step, "failed", f"could not run docker compose: {exc}", t0)
        raise RuntimeError(f"could not run docker compose: {exc}")

    output = ((proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")).strip()
    _log_line(run, _clip(output, 20_000) or "(build hook produced no output)")
    if proc.returncode != 0:
        _end_step(run, step, "failed", f"exit {proc.returncode}", t0)
        raise RuntimeError(f"build hook '{service}' failed with exit {proc.returncode}")
    _end_step(run, step, "ok", "exit 0", t0)


def step_deploy(run: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any]:
    """Reuse the existing deploy path — the same one /api/control/stream drives."""
    slug = str(pipeline.get("app_slug") or "").strip()
    target_id = f"leco-stack-{slug}"
    step = _start_step(run, "deploy", f"{target_id} · action deploy")
    t0 = time.perf_counter()

    from control import run_action_streaming

    result: dict[str, Any] = {}
    deadline = time.monotonic() + DEPLOY_TIMEOUT_SEC
    try:
        for event in run_action_streaming(target_id, "deploy"):
            if not isinstance(event, dict):
                continue
            if event.get("type") == "log":
                _log_line(run, str(event.get("text") or "").rstrip("\n"))
            elif event.get("type") == "done":
                result = event.get("result") if isinstance(event.get("result"), dict) else {}
            if time.monotonic() > deadline:
                _end_step(run, step, "failed", f"deploy exceeded {DEPLOY_TIMEOUT_SEC}s", t0)
                raise RuntimeError("deploy timed out")
    except RuntimeError:
        raise
    except Exception as exc:
        _end_step(run, step, "failed", f"deploy raised: {exc}", t0)
        raise RuntimeError(f"deploy failed: {exc}")

    if not result.get("ok"):
        detail = str(result.get("error") or result.get("message") or "deploy reported failure")
        _end_step(run, step, "failed", detail, t0)
        raise RuntimeError(detail)
    _end_step(run, step, "ok", str(result.get("message") or "deploy ok"), t0)
    return result


def _derive_verify_url(pipeline: dict[str, Any]) -> str:
    explicit = str(pipeline.get("verify_url") or "").strip()
    if explicit:
        return explicit
    slug = str(pipeline.get("app_slug") or "").strip()
    if not slug:
        return ""
    try:
        from leco_control import leco_meta_for_slug
        from hosted_apps import manifest_ui_fields

        meta = leco_meta_for_slug(slug)
        if not meta:
            return ""
        fields = manifest_ui_fields(str(meta.get("manifest_path") or ""))
        return str(fields.get("main_url") or fields.get("derived_main_url") or "").strip()
    except Exception as exc:
        _logger().info("cicd: could not derive verify URL for %s: %s", slug, exc)
        return ""


def _probe(url: str) -> dict[str, Any]:
    """Probe the app's public URL. Uses the hosted-apps probe so *.lh routes through Traefik."""
    try:
        from hosted_apps import _probe_main_url

        return _probe_main_url(url)
    except Exception:
        pass
    # Fallback for environments without the dashboard's request stack.
    import urllib.error
    import urllib.request

    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "leco-cicd-verify/1"})
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - operator-supplied URL
            code = int(resp.status)
        return {"checked": True, "url": url, "ok": 200 <= code < 400, "status_code": code, "ms": _ms_since(t0)}
    except urllib.error.HTTPError as exc:
        return {
            "checked": True,
            "url": url,
            "ok": 200 <= int(exc.code) < 400,
            "status_code": int(exc.code),
            "ms": _ms_since(t0),
        }
    except Exception as exc:
        return {"checked": True, "url": url, "ok": False, "status_code": None, "ms": _ms_since(t0), "error": str(exc)[:160]}


def step_verify(run: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any]:
    """A deploy that 'succeeded' while the app 502s is a failed deploy."""
    url = _derive_verify_url(pipeline)
    step = _start_step(run, "verify", url or "no verify URL configured or derivable")
    t0 = time.perf_counter()
    if not url:
        detail = (
            "skipped — set a verify URL on the pipeline (or a main URL on the app manifest) so a "
            "deploy that leaves the app returning 502 is recorded as a failure"
        )
        _end_step(run, step, "skipped", detail, t0)
        run["verify"] = {"checked": False, "reason": "no verify URL"}
        return run["verify"]

    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    for i in range(VERIFY_ATTEMPTS):
        result = _probe(url)
        attempts.append({"attempt": i + 1, "status_code": result.get("status_code"), "ok": bool(result.get("ok")), "ms": result.get("ms")})
        _log_line(run, f"verify attempt {i + 1}/{VERIFY_ATTEMPTS}: {result.get('status_code') or result.get('error') or 'no response'}")
        if result.get("ok"):
            break
        if i < VERIFY_ATTEMPTS - 1:
            time.sleep(VERIFY_INTERVAL_SEC)

    result["attempts"] = attempts
    run["verify"] = result
    if result.get("ok"):
        _end_step(run, step, "ok", f"HTTP {result.get('status_code')} in {result.get('ms')} ms", t0)
    else:
        detail = f"HTTP {result.get('status_code') or '—'} after {len(attempts)} attempts: {result.get('error') or 'not healthy'}"
        _end_step(run, step, "failed", detail, t0)
        raise RuntimeError(f"verify failed for {url}: {detail}")
    return result


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def execute_run(pipeline: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """pull → (optional build) → deploy → verify → record."""
    ref = str(run.get("requested_ref") or pipeline.get("branch") or "main")
    run["status"] = "running"
    _log_line(
        run,
        f"CI/CD run {run['run_id']} · pipeline {pipeline.get('id')} · app {pipeline.get('app_slug')} · "
        f"trigger {run.get('trigger')} · ref {ref}",
    )
    _append_run_snapshot(run)

    try:
        step_pull(run, pipeline, ref)
        step_build(run, pipeline)
        step_deploy(run, pipeline)
        step_verify(run, pipeline)
    except GitSourceUnavailable as exc:
        return _finish_run(run, "failed", str(exc))
    except Exception as exc:
        return _finish_run(run, "failed", str(exc))

    sha = str(run.get("commit_sha") or "")
    if sha and not run.get("rollback_of"):
        _record_deploy_sha(str(pipeline.get("id") or ""), sha)
    elif sha and run.get("rollback_of"):
        # After a rollback the old SHA is current again, and the SHA we rolled back *from*
        # becomes the next rollback target — so the operator can roll forward again if the
        # rollback was the mistake. Leaving the pointer where it was would offer a rollback to
        # the commit that is already deployed, which does nothing.
        rolled_from = str(run.get("rollback_of") or "")
        with _STORE_LOCK:
            store = _read_store()
            items = list(store.get("pipelines") or [])
            for idx, p in enumerate(items):
                if str(p.get("id") or "") == str(pipeline.get("id") or ""):
                    items[idx] = {
                        **p,
                        "last_deployed_sha": sha,
                        "last_deployed_at": _now(),
                        "previous_deployed_sha": rolled_from if rolled_from and rolled_from != sha else "",
                        "updated_at": _now(),
                    }
                    store["pipelines"] = items
                    _write_store(store)
                    break

    verify = run.get("verify") or {}
    if verify.get("checked") and verify.get("ok"):
        outcome = f"deployed {_short(sha)} and verified HTTP {verify.get('status_code')}"
    elif verify.get("checked") is False:
        outcome = f"deployed {_short(sha)} (not verified — no verify URL)"
    else:
        outcome = f"deployed {_short(sha)}"
    return _finish_run(run, "success", outcome)


# ---------------------------------------------------------------------------
# Webhook entry point
# ---------------------------------------------------------------------------


def handle_webhook(pipeline_id: str, headers: Any, body: bytes) -> tuple[dict[str, Any], int]:
    """Authenticate, decide, acknowledge. The pipeline itself runs on a background thread.

    Authentication here is the HMAC signature and **nothing else**. Do not add a
    ``DASHBOARD_CONTROL_TOKEN`` check: GitHub and GitLab cannot send that header, so doing so
    would reject every real webhook while appearing to harden the endpoint.
    """
    pid = str(pipeline_id or "").strip()

    # Uniform rejection: an unknown pipeline, a wrong secret and a missing signature all look
    # identical from outside, so probing cannot enumerate pipelines.
    forbidden = ({"ok": False, "error": "forbidden"}, 403)

    if not pid or not _PIPELINE_ID_RE.match(pid):
        return forbidden
    if body is not None and len(body) > MAX_WEBHOOK_BODY_BYTES:
        return forbidden

    pipeline = get_pipeline(pid)
    if not pipeline:
        return forbidden

    ok, scheme = verify_signature(pipeline, headers, body or b"")
    if not ok:
        _logger().warning("cicd: rejected unsigned/invalid webhook for pipeline %s", pid)
        return forbidden

    # ---- from here on the caller is authenticated ----

    if not bool(pipeline.get("enabled", True)):
        return {"ok": True, "accepted": False, "reason": "pipeline disabled"}, 202

    event = parse_event(headers, body or b"", scheme)
    if event.get("action") != "run":
        return {"ok": True, "accepted": False, "reason": event.get("reason") or "ignored"}, 202

    wanted = str(pipeline.get("branch") or "").strip()
    if wanted and event.get("branch") != wanted:
        return (
            {
                "ok": True,
                "accepted": False,
                "reason": f"branch {event.get('branch')!r} does not match pipeline branch {wanted!r}",
            },
            202,
        )

    if not bool(pipeline.get("auto_deploy", True)):
        return {"ok": True, "accepted": False, "reason": "auto-deploy is off for this pipeline"}, 202

    delivery = str(event.get("delivery_id") or "")
    if delivery and not _remember_delivery(f"{pid}:{delivery}"):
        return {"ok": True, "accepted": False, "reason": "duplicate delivery (retry)"}, 202

    scheduled = enqueue(pipeline, trigger="webhook", event=event)
    return (
        {
            "ok": True,
            "accepted": True,
            "run_id": scheduled.get("run_id") or "",
            "queued": bool(scheduled.get("queued")),
            "coalesced": bool(scheduled.get("coalesced")),
            "reason": scheduled.get("message") or "",
        },
        202,
    )


# ---------------------------------------------------------------------------
# Manual triggers
# ---------------------------------------------------------------------------


def trigger_manual(pipeline_id: str) -> tuple[dict[str, Any], int]:
    pipeline = get_pipeline(pipeline_id)
    if not pipeline:
        return {"ok": False, "error": f"unknown pipeline {pipeline_id!r}"}, 404
    event = {"provider": "manual", "event": "manual", "branch": pipeline.get("branch") or ""}
    scheduled = enqueue(pipeline, trigger="manual", event=event)
    return {**scheduled, "pipeline_id": str(pipeline.get("id") or "")}, 202


def trigger_rollback(pipeline_id: str, sha: str = "") -> tuple[dict[str, Any], int]:
    """Redeploy the previously deployed commit.

    Honest scope: this re-checks-out and redeploys **code**. It does not migrate a database
    backwards, restore volumes, or undo anything an already-deployed release wrote.
    """
    pipeline = get_pipeline(pipeline_id)
    if not pipeline:
        return {"ok": False, "error": f"unknown pipeline {pipeline_id!r}"}, 404
    target = str(sha or pipeline.get("previous_deployed_sha") or "").strip()
    if not target:
        return (
            {
                "ok": False,
                "error": "no previous deployed commit recorded for this pipeline — nothing to roll back to",
            },
            400,
        )
    if not _SHA_RE.match(target):
        return {"ok": False, "error": "rollback target must be a commit SHA"}, 400
    if target == str(pipeline.get("last_deployed_sha") or "").strip():
        return {"ok": False, "error": f"commit {_short(target)} is already the deployed one"}, 400
    event = {
        "provider": "manual",
        "event": "rollback",
        "branch": pipeline.get("branch") or "",
        "sha": target,
        "subject": f"rollback to {_short(target)}",
    }
    scheduled = enqueue(
        pipeline,
        trigger="rollback",
        event=event,
        target_sha=target,
        rollback_of=str(pipeline.get("last_deployed_sha") or ""),
    )
    return {**scheduled, "pipeline_id": str(pipeline.get("id") or ""), "target_sha": target}, 202


# ---------------------------------------------------------------------------
# Panel payload
# ---------------------------------------------------------------------------


def build_overview(*, webhook_base: str = "", runs_limit: int = 25) -> dict[str, Any]:
    """Everything the CI/CD tab needs on first paint."""
    pipelines = load_pipelines()
    runs = list_runs(limit=runs_limit)
    last_by_pipeline: dict[str, dict[str, Any]] = {}
    for run in runs.get("runs") or []:
        key = str(run.get("pipeline_id") or "")
        if key and key not in last_by_pipeline:
            last_by_pipeline[key] = run

    rows = []
    for p in pipelines:
        row = public_pipeline(p, webhook_base=webhook_base)
        last = last_by_pipeline.get(row["id"])
        if last is None:
            found = list_runs(pipeline_id=row["id"], limit=1)
            candidates = found.get("runs") or []
            last = candidates[0] if candidates else None
        row["last_run"] = (
            {
                "run_id": last.get("run_id"),
                "status": last.get("status"),
                "trigger": last.get("trigger"),
                "commit_sha": last.get("commit_sha"),
                "commit_subject": last.get("commit_subject"),
                "started_at": last.get("started_at"),
                "duration_ms": last.get("duration_ms"),
                "outcome": last.get("outcome"),
            }
            if last
            else None
        )
        prev = str(p.get("previous_deployed_sha") or "")
        row["can_rollback"] = bool(prev) and prev != str(p.get("last_deployed_sha") or "")
        rows.append(row)

    git_source_ok = True
    git_source_error = ""
    try:
        _git_source_module()
    except GitSourceUnavailable as exc:
        git_source_ok = False
        git_source_error = str(exc)

    return {
        "ok": True,
        "pipelines": rows,
        "runs": runs.get("runs") or [],
        "runs_total": runs.get("total") or 0,
        "git_source_available": git_source_ok,
        "git_source_error": git_source_error,
        "pipelines_file": PIPELINES_REL,
        "runs_file": RUNS_REL,
        "generated_at": _now(),
    }

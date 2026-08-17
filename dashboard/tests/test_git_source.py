"""Onboarding from a Git repository: URL vetting, confinement, credentials, clone/update.

The load-bearing invariants:

* a token or key must never reach ``.git/config``, a response, or a log line;
* a hostile URL must not make git touch the local filesystem or run a command;
* a clone must not escape the managed clone root;
* git must never block the request — no credential prompt, always a timeout.

Clone/update behaviour is exercised against a **real local git repository**
served over ``file://`` at the subprocess level (the validator's ``file://`` ban
is a URL-layer policy, so these tests drive :func:`_fresh_clone` /
:func:`_update_existing` through ``iter_clone_or_update`` with the validator
patched for the transport only).
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "dashboard"))

HAS_GIT = shutil.which("git") is not None
needs_git = pytest.mark.skipif(not HAS_GIT, reason="git is not installed")

FAKE_TOKEN = "ghp_TESTTOKEN0123456789abcdefABCDEF0123"


@pytest.fixture()
def gs(tmp_path, monkeypatch):
    """git_source rooted at a throwaway project dir with a writable clone root."""
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    wsp = tmp_path / "workspace"
    wsp.mkdir()
    monkeypatch.setenv("DASHBOARD_PROJECT_ROOT", str(project))
    monkeypatch.setenv("DASHBOARD_WORKSPACE_PARENT", str(wsp))
    monkeypatch.delenv("LECO_GIT_CLONE_ROOT", raising=False)
    monkeypatch.delenv("DASHBOARD_PROJECT_ROOT_HOST", raising=False)
    monkeypatch.delenv("DASHBOARD_WORKSPACE_PARENT_HOST", raising=False)
    import leco_detect

    importlib.reload(leco_detect)
    import git_source as mod

    importlib.reload(mod)
    mod._TEST_PROJECT = project  # type: ignore[attr-defined]
    mod._TEST_WSP = wsp  # type: ignore[attr-defined]
    yield mod


def _git(*args: str, cwd: Path) -> str:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_NAME": "LEco Test",
            "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "LEco Test",
            "GIT_COMMITTER_EMAIL": "test@example.invalid",
        }
    )
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True, env=env
    ).stdout


@pytest.fixture()
def origin_repo(tmp_path):
    """A real repository with two branches and two commits."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git("add", "README.md", cwd=repo)
    _git("commit", "-q", "-m", "first commit", cwd=repo)
    _git("branch", "feature", cwd=repo)
    (repo / "second.txt").write_text("second\n", encoding="utf-8")
    _git("add", "second.txt", cwd=repo)
    _git("commit", "-q", "-m", "second commit", cwd=repo)
    return repo


@pytest.fixture()
def allow_file_urls(gs, monkeypatch, origin_repo):
    """Let the clone machinery use a local path as the transport for tests only."""
    real = gs.validate_repo_url

    def patched(raw: str):
        s = (raw or "").strip()
        if s.startswith("file://"):
            return {"url": s, "kind": "https", "host": "local", "path": s, "name": Path(s).name}
        return real(raw)

    monkeypatch.setattr(gs, "validate_repo_url", patched)
    return f"file://{origin_repo}"


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,name,kind",
    [
        ("https://github.com/octocat/Hello-World.git", "Hello-World", "https"),
        ("https://github.com/octocat/Hello-World", "Hello-World", "https"),
        ("https://gitlab.example.com:8443/group/sub/app.git", "app", "https"),
        ("ssh://git@github.com/octocat/Hello-World.git", "Hello-World", "ssh"),
        ("ssh://github.com/octocat/Hello-World.git", "Hello-World", "ssh"),
        ("git@github.com:octocat/Hello-World.git", "Hello-World", "ssh"),
    ],
)
def test_valid_urls_are_parsed(gs, url, name, kind):
    parsed = gs.validate_repo_url(url)
    assert parsed["name"] == name
    assert parsed["kind"] == kind


@pytest.mark.parametrize(
    "url,fragment",
    [
        ("file:///etc/passwd", "file://"),
        ("ext::sh -c 'touch /tmp/pwned'", "not allowed"),
        ("git://github.com/octocat/Hello-World.git", "not allowed"),
        ("http://github.com/octocat/Hello-World.git", "https://"),
        ("ftp://example.com/repo.git", "not allowed"),
        ("--upload-pack=touch /tmp/x", "'-'"),
        ("https://github.com/a b/c.git", "whitespace"),
        ("https://github.com/octocat/../../etc/passwd", "'..'"),
        ("", "required"),
        ("not a url", "whitespace"),
        ("https://", "malformed"),
        ("transport::address", "Remote helper"),
    ],
)
def test_hostile_urls_are_rejected(gs, url, fragment):
    with pytest.raises(gs.GitSourceError) as exc:
        gs.validate_repo_url(url)
    assert fragment in str(exc.value)


def test_url_with_embedded_credentials_is_rejected_with_guidance(gs):
    with pytest.raises(gs.GitSourceError) as exc:
        gs.validate_repo_url(f"https://{FAKE_TOKEN}@github.com/acme/private.git")
    msg = str(exc.value)
    assert "credential field" in msg
    # The rejection must not repeat the secret back at the caller.
    assert FAKE_TOKEN not in msg


def test_overlong_url_is_rejected(gs):
    with pytest.raises(gs.GitSourceError):
        gs.validate_repo_url("https://github.com/a/" + "x" * 600 + ".git")


# ---------------------------------------------------------------------------
# Path confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Hello-World", "Hello-World"),
        ("my app", "my-app"),
        ("../../etc", "etc"),
        ("a/b/c", "a-b-c"),
        (".hidden", "hidden"),
    ],
)
def test_safe_dir_name(gs, raw, expected):
    assert gs.safe_dir_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "..", "/", "///", "  "])
def test_safe_dir_name_rejects_empty_or_traversal(gs, raw):
    with pytest.raises(gs.GitSourceError):
        gs.safe_dir_name(raw)


def test_target_dir_stays_inside_clone_root(gs, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = gs._target_dir(root, "../../escape")
    assert target.parent == root.resolve()


def test_clone_root_prefers_writable_workspace_parent(gs):
    info = gs.resolve_clone_root()
    assert info["kind"] == "workspace-parent"
    assert info["path"] == gs._TEST_WSP.resolve()


def test_clone_root_falls_back_to_project_when_workspace_readonly(gs, monkeypatch):
    monkeypatch.setattr(gs, "_dir_writable", lambda p: "workspace" not in str(p))
    info = gs.resolve_clone_root()
    assert info["kind"] == "project"
    assert info["path"].as_posix().endswith(gs.PROJECT_CLONE_SUBDIR)
    assert "read-only" in info["note"]


def test_clone_root_override_wins(gs, tmp_path, monkeypatch):
    override = tmp_path / "elsewhere"
    monkeypatch.setenv("LECO_GIT_CLONE_ROOT", str(override))
    info = gs.resolve_clone_root()
    assert info["kind"] == "override"
    assert info["path"] == override.resolve()


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_credentials_are_stored_gitignored_and_masked(gs):
    gs.save_credential("GitHub Bot", kind="https-token", token=FAKE_TOKEN, host="github.com")
    assert gs.CONFIG_FILE.is_file()
    assert oct(gs.CONFIG_FILE.stat().st_mode)[-3:] == "600"
    assert gs.CONFIG_REL_PATH == "config/git-credentials.yaml"

    ui = gs.credentials_for_ui()
    blob = repr(ui)
    assert FAKE_TOKEN not in blob
    row = ui["credentials"][0]
    assert row["id"] == "github-bot"
    assert row["token_set"] is True
    assert row["token_masked"].startswith("ghp_") and row["token_masked"].endswith(FAKE_TOKEN[-4:])
    assert ui["storage"]["gitignored"] is True

    # Server-side lookup still has the real secret.
    assert gs.get_credential("github-bot")["token"] == FAKE_TOKEN
    assert gs.delete_credential("github-bot") is True
    assert gs.credentials_for_ui()["credentials"] == []


def test_gitignore_covers_the_credential_file():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/git-credentials.yaml" in text
    assert "hosting/app-sources/" in text


def test_passphrase_protected_keys_are_refused(gs):
    with pytest.raises(gs.GitSourceError) as exc:
        gs.save_credential("k", kind="ssh-key", private_key="-----BEGIN X-----", passphrase="pw")
    assert "non-interactively" in str(exc.value)


def test_resolve_credential_prefers_stored_id(gs):
    gs.save_credential("stored", kind="https-token", token=FAKE_TOKEN)
    cred = gs.resolve_credential(credential_id_value="stored")
    assert cred["token"] == FAKE_TOKEN
    inline = gs.resolve_credential(token="inline-token-value")
    assert inline == {"kind": "https-token", "token": "inline-token-value", "username": "x-access-token"}
    key = gs.resolve_credential(private_key="-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    assert key["kind"] == "ssh-key"
    assert gs.resolve_credential() is None


def test_auth_context_keeps_the_token_out_of_argv_and_env_value(gs):
    with gs._AuthContext({"kind": "https-token", "token": FAKE_TOKEN}) as auth:
        # Only paths are exported; the secret itself lives in a 0600 file.
        assert FAKE_TOKEN not in repr(auth.env)
        secret_file = Path(auth.env["LECO_GIT_SECRET_FILE"])
        assert secret_file.read_text(encoding="utf-8") == FAKE_TOKEN
        assert oct(secret_file.stat().st_mode)[-3:] == "600"
        assert auth.env["GIT_TERMINAL_PROMPT"] == "0"
        assert auth.env["HOME"] != os.environ.get("HOME")
        tmpdir = auth.dir
    # The whole auth dir is gone once the clone finishes.
    assert tmpdir is not None and not tmpdir.exists()


def test_auth_context_ssh_key_is_0600_and_batch_mode(gs):
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key-body\n-----END OPENSSH PRIVATE KEY-----"
    with gs._AuthContext({"kind": "ssh-key", "private_key": key}) as auth:
        cmd = auth.env["GIT_SSH_COMMAND"]
        assert "BatchMode=yes" in cmd and "IdentitiesOnly=yes" in cmd
        key_file = Path(cmd.split("-i ", 1)[1].split(" ", 1)[0])
        assert oct(key_file.stat().st_mode)[-3:] == "600"
        assert key in key_file.read_text(encoding="utf-8")


def test_no_credential_still_pins_ssh_non_interactive(gs):
    with gs._AuthContext(None) as auth:
        assert auth.env["GIT_TERMINAL_PROMPT"] == "0"
        assert "BatchMode=yes" in auth.env["GIT_SSH_COMMAND"]


def test_redact_strips_secrets_and_url_credentials(gs):
    text = f"remote: using {FAKE_TOKEN}\nfatal: https://user:pw@github.com/x.git not found"
    out = gs.redact(text, [FAKE_TOKEN])
    assert FAKE_TOKEN not in out
    assert "user:pw@" not in out
    assert "***redacted***" in out


# ---------------------------------------------------------------------------
# git runner
# ---------------------------------------------------------------------------


@needs_git
def test_run_git_times_out_instead_of_hanging(gs, tmp_path):
    code, lines, reason = gs.run_git(["version"], cwd=tmp_path, timeout=10)
    assert reason == "" and code == 0
    # A command that would block far longer than the budget is killed, not awaited.
    code, lines, reason = gs.run_git(
        ["-c", "alias.leco-sleep=!sleep 30", "leco-sleep"], cwd=tmp_path, timeout=1
    )
    assert reason == "timeout"
    assert code == 124
    assert any("timed out" in ln for ln in lines)


def test_missing_git_binary_is_reported_not_raised(gs, monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(gs.subprocess, "Popen", boom)
    code, lines, reason = gs.run_git(["status"])
    assert code == 127 and reason == "missing-git"
    assert "git is not installed" in lines[0]


def test_friendly_error_messages(gs):
    assert "Authentication required" in gs._friendly_error(
        128, ["fatal: could not read Username for 'https://github.com': terminal prompts disabled"], "", had_credential=False
    )
    assert "rejected" in gs._friendly_error(
        128, ["fatal: Authentication failed for 'https://github.com/x'"], "", had_credential=True
    )
    # git 2.47 over https says this instead when no askpass is configured — the
    # operator must still be told "add a credential", not shown git's phrasing.
    assert "Authentication required" in gs._friendly_error(
        128, ["fatal: unable to get password from user"], "", had_credential=False
    )
    assert "timed out" in gs._friendly_error(124, [], "timeout", had_credential=False)
    assert "size guard" in gs._friendly_error(125, [], "size", had_credential=False)
    assert "does not exist" in gs._friendly_error(
        128, ["fatal: Remote branch nope not found in upstream origin"], "", had_credential=False
    )


# ---------------------------------------------------------------------------
# Clone / update against a real repository
# ---------------------------------------------------------------------------


def _run(gs, **kwargs):
    return gs.clone_or_update(**kwargs)


@needs_git
def test_clone_returns_path_field_ref_and_commit(gs, allow_file_urls):
    out = _run(gs, url=allow_file_urls, app_id="hello-world")
    assert out["ok"] is True, out
    assert out["action"] == "cloned"
    assert out["path_field"] == "wsp:hello-world"
    assert out["ref"] == "main" and out["ref_kind"] == "branch"
    assert len(out["short_commit"]) == 7
    assert out["commit_subject"] == "second commit"
    assert out["commit_date"]
    assert out["shallow"] is True
    assert (Path(out["path"]) / "README.md").is_file()


@needs_git
def test_clone_a_branch_and_a_commit(gs, allow_file_urls, origin_repo):
    out = _run(gs, url=allow_file_urls, app_id="on-branch", ref="feature")
    assert out["ok"] is True, out
    assert out["ref"] == "feature"
    assert out["commit_subject"] == "first commit"

    sha = _git("rev-parse", "HEAD", cwd=origin_repo).strip()
    out = _run(gs, url=allow_file_urls, app_id="on-commit", ref=sha, full_history=True)
    assert out["ok"] is True, out
    assert out["commit"] == sha
    assert out["ref_kind"] == "commit"


@needs_git
def test_full_history_opt_out_of_shallow(gs, allow_file_urls):
    out = _run(gs, url=allow_file_urls, app_id="deep", full_history=True)
    assert out["ok"] is True, out
    assert out["shallow"] is False
    assert out["depth"] is None


@needs_git
def test_bad_ref_gives_a_clear_message_and_leaves_nothing_behind(gs, allow_file_urls):
    out = _run(gs, url=allow_file_urls, app_id="badref", ref="no-such-branch")
    assert out["ok"] is False
    assert "does not exist" in out["error"] or "not found" in out["error"]
    assert not (gs._TEST_WSP / "badref").exists()


@needs_git
def test_update_existing_clone_reports_new_sha(gs, allow_file_urls, origin_repo):
    first = _run(gs, url=allow_file_urls, app_id="upd")
    assert first["ok"] is True, first
    (origin_repo / "third.txt").write_text("third\n", encoding="utf-8")
    _git("add", "third.txt", cwd=origin_repo)
    _git("commit", "-q", "-m", "third commit", cwd=origin_repo)

    second = _run(gs, url=allow_file_urls, app_id="upd")
    assert second["ok"] is True, second
    assert second["action"] == "updated"
    assert second["commit_subject"] == "third commit"
    assert second["short_commit"] != first["short_commit"]
    assert second["path_field"] == first["path_field"]


@needs_git
def test_update_refuses_to_clobber_local_changes_without_reset(gs, allow_file_urls):
    first = _run(gs, url=allow_file_urls, app_id="dirty")
    assert first["ok"] is True, first
    (Path(first["path"]) / "README.md").write_text("local edit\n", encoding="utf-8")

    blocked = _run(gs, url=allow_file_urls, app_id="dirty")
    assert blocked["ok"] is False
    assert blocked["requires_reset"] is True
    assert "reset=true" in blocked["error"]
    assert (Path(first["path"]) / "README.md").read_text(encoding="utf-8") == "local edit\n"

    forced = _run(gs, url=allow_file_urls, app_id="dirty", reset=True)
    assert forced["ok"] is True, forced
    assert forced["reset_applied"] is True
    assert any("discarded" in n for n in forced["notes"])
    assert (Path(first["path"]) / "README.md").read_text(encoding="utf-8") == "hello\n"


@needs_git
def test_changed_remote_triggers_a_reclone(gs, allow_file_urls, tmp_path, monkeypatch):
    first = _run(gs, url=allow_file_urls, app_id="moved")
    assert first["ok"] is True, first

    other = tmp_path / "other-origin"
    other.mkdir()
    _git("init", "-q", "-b", "main", cwd=other)
    (other / "OTHER.md").write_text("other\n", encoding="utf-8")
    _git("add", "OTHER.md", cwd=other)
    _git("commit", "-q", "-m", "other repo", cwd=other)

    out = _run(gs, url=f"file://{other}", app_id="moved")
    assert out["ok"] is True, out
    assert out["action"] == "recloned"
    assert out["commit_subject"] == "other repo"
    assert (Path(out["path"]) / "OTHER.md").is_file()
    assert not (Path(out["path"]) / "README.md").exists()


@needs_git
def test_same_remote_spellings_do_not_force_a_reclone(gs):
    assert gs._same_remote("https://github.com/a/b.git", "https://github.com/a/b")
    assert gs._same_remote("git@github.com:a/b.git", "ssh://git@github.com/a/b.git")
    assert not gs._same_remote("https://github.com/a/b.git", "https://github.com/a/c.git")


@needs_git
def test_existing_non_git_directory_is_refused(gs, allow_file_urls):
    (gs._TEST_WSP / "occupied").mkdir()
    (gs._TEST_WSP / "occupied" / "keep.txt").write_text("x", encoding="utf-8")
    out = _run(gs, url=allow_file_urls, app_id="occupied")
    assert out["ok"] is False
    assert "not a git clone" in out["error"]
    assert (gs._TEST_WSP / "occupied" / "keep.txt").is_file()


@needs_git
def test_size_guard_kills_a_clone_that_grows_too_large(gs, allow_file_urls, monkeypatch):
    monkeypatch.setenv("LECO_GIT_MAX_CLONE_MB", "1")
    monkeypatch.setattr(gs, "_SIZE_POLL_SECONDS", 0.0)
    monkeypatch.setattr(gs, "_dir_size", lambda _p: 999 * 1024 * 1024)
    out = _run(gs, url=allow_file_urls, app_id="huge")
    assert out["ok"] is False
    assert "size guard" in out["error"]
    assert not (gs._TEST_WSP / "huge").exists()


@needs_git
def test_clone_never_writes_a_credential_into_the_clone_or_the_response(gs, allow_file_urls):
    """The trap this feature exists to avoid: a token baked into .git/config."""
    out = _run(
        gs,
        url=allow_file_urls,
        app_id="withtoken",
        credential={"kind": "https-token", "token": FAKE_TOKEN, "username": "x-access-token"},
    )
    assert out["ok"] is True, out
    config_text = (Path(out["path"]) / ".git" / "config").read_text(encoding="utf-8")
    assert FAKE_TOKEN not in config_text
    assert FAKE_TOKEN not in repr(out)
    assert out["credential_used"] is True
    assert "token" not in out


@needs_git
def test_stream_events_match_the_register_stream_shape(gs, allow_file_urls):
    events = list(gs.iter_clone_or_update(url=allow_file_urls, app_id="streamed"))
    # Same event vocabulary as /api/leco/register/stream so the dashboard's
    # readNdjsonLinesFromReader() consumes it unchanged.
    assert {e["type"] for e in events} == {"log", "done"}
    assert all("text" in e for e in events if e["type"] == "log")
    assert events[-1]["type"] == "done"
    assert events[-1]["result"]["ok"] is True


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@needs_git
def test_status_reports_ref_sha_and_dirtiness(gs, allow_file_urls):
    cloned = _run(gs, url=allow_file_urls, app_id="statused")
    assert cloned["ok"] is True, cloned

    st = gs.clone_status(cloned["path_field"])
    assert st["ok"] is True and st["is_git"] is True
    assert st["dirty"] is False
    assert st["ref"] == "main"
    assert st["short_commit"] == cloned["short_commit"]
    assert st["remote_url"]

    (Path(cloned["path"]) / "new-file.txt").write_text("x", encoding="utf-8")
    st2 = gs.clone_status(cloned["path_field"])
    assert st2["dirty"] is True
    assert st2["changed"]


@needs_git
def test_status_on_a_non_git_folder_says_so(gs):
    (gs._TEST_WSP / "plain").mkdir()
    st = gs.clone_status("wsp:plain")
    assert st["ok"] is True and st["is_git"] is False
    assert "not a git clone" in st["note"]


def test_status_rejects_paths_outside_the_allowed_roots(gs):
    st = gs.clone_status("../../etc")
    assert st["ok"] is False


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@needs_git
def test_inspect_lists_branches_without_cloning(gs, allow_file_urls, tmp_path):
    out = gs.inspect_remote(allow_file_urls)
    assert out["ok"] is True, out
    assert "main" in out["branches"] and "feature" in out["branches"]
    assert out["default_branch"] == "main"
    # Nothing was written into the clone root.
    assert list(gs._TEST_WSP.iterdir()) == []


@needs_git
def test_inspect_of_a_nonexistent_repo_fails_fast_with_a_message(gs, allow_file_urls, tmp_path):
    out = gs.inspect_remote(f"file://{tmp_path / 'does-not-exist'}")
    assert out["ok"] is False
    assert out["error"]


def test_inspect_rejects_a_hostile_url_before_running_git(gs):
    with pytest.raises(gs.GitSourceError):
        gs.inspect_remote("ext::sh -c whoami")

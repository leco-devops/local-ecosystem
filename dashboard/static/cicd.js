/*
 * CI/CD tab — self-contained.
 *
 * Deliberately independent of dashboard.js: it only reads the shared Control token from
 * localStorage (the key dashboard.js already writes) and owns everything else itself, so the
 * two files never have to be edited together.
 *
 * Endpoints: /api/cicd/overview, /api/cicd/pipelines, /api/cicd/runs, /api/cicd/runs/<id>,
 * /api/cicd/pipelines/<id>/{run,rollback,rotate-secret}.  The webhook receiver is not called
 * from here — Git hosts call it, authenticated by its HMAC signature.
 */
(function () {
  "use strict";

  var CONTROL_TOKEN_KEY = "dashboard_control_token";
  var POLL_MS = 5000;

  var state = {
    pipelines: [],
    runs: [],
    runsTotal: 0,
    offset: 0,
    limit: 25,
    selectedRunId: "",
    pollTimer: null,
    booted: false,
    lastSignature: "",
  };

  // ---------------------------------------------------------------- helpers

  function $(id) {
    return document.getElementById(id);
  }

  function controlToken() {
    try {
      return localStorage.getItem(CONTROL_TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function esc(value) {
    return String(value === null || value === undefined ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function shortSha(sha) {
    var s = String(sha || "");
    return s ? s.slice(0, 8) : "";
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d.getTime())) return String(iso);
    return d.toLocaleString();
  }

  function fmtDuration(ms) {
    var n = Number(ms || 0);
    if (!n) return "—";
    if (n < 1000) return n + " ms";
    if (n < 60000) return (n / 1000).toFixed(1) + " s";
    var mins = Math.floor(n / 60000);
    var secs = Math.round((n % 60000) / 1000);
    return mins + "m " + secs + "s";
  }

  function statusPill(status) {
    var s = String(status || "none");
    var known = ["success", "failed", "running", "queued", "skipped", "interrupted"];
    var cls = known.indexOf(s) >= 0 ? s : "none";
    return '<span class="cicd-status cicd-status--' + cls + '">' + esc(s) + "</span>";
  }

  async function api(path, options) {
    var opts = options || {};
    var headers = { Accept: "application/json" };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    var tok = controlToken();
    if (tok) headers["X-Control-Token"] = tok;
    var res = await fetch(path, {
      method: opts.method || "GET",
      headers: headers,
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      cache: "no-store",
    });
    var payload = null;
    try {
      payload = await res.json();
    } catch (_) {
      payload = null;
    }
    if (!res.ok) {
      var msg = (payload && (payload.error || payload.reason)) || "HTTP " + res.status;
      var err = new Error(msg);
      err.status = res.status;
      err.payload = payload;
      throw err;
    }
    return payload || {};
  }

  async function copyText(text, button) {
    var original = button ? button.textContent : "";
    try {
      await navigator.clipboard.writeText(String(text || ""));
      if (button) button.textContent = "Copied";
    } catch (_) {
      // Clipboard API needs a secure context; fall back to a selectable prompt.
      window.prompt("Copy manually:", String(text || ""));
      if (button) button.textContent = "Copy manually";
    }
    if (button) {
      setTimeout(function () {
        button.textContent = original;
      }, 1800);
    }
  }

  function banner(html, bad) {
    var slot = $("cicdBanner");
    if (!slot) return;
    slot.innerHTML = html ? '<div class="cicd-banner' + (bad ? " cicd-banner--bad" : "") + '">' + html + "</div>" : "";
  }

  // ---------------------------------------------------------------- render

  function renderPipelines() {
    var host = $("cicdPipelines");
    if (!host) return;
    if (!state.pipelines.length) {
      host.innerHTML =
        '<p class="muted small">No pipelines yet. <strong>New pipeline</strong> creates one and shows its webhook secret once.</p>';
      return;
    }
    host.innerHTML = state.pipelines
      .map(function (p) {
        var last = p.last_run;
        var lastStatus = last ? last.status : "none";
        var webhook = p.webhook_url || p.webhook_path;
        var rows = [
          ["Application", "<code>" + esc(p.app_slug) + "</code>"],
          ["Repository", "<code>" + esc(p.repo_url) + "</code>"],
          ["Branch", "<code>" + esc(p.branch) + "</code>"],
          ["Git host", esc(p.provider)],
          ["Auto-deploy", p.auto_deploy ? "on" : '<span class="cicd-status cicd-status--none">off</span>'],
          ["Verify URL", p.verify_url ? "<code>" + esc(p.verify_url) + "</code>" : '<span class="muted">derived from the app manifest</span>'],
          [
            "Build hook",
            p.build_hook_service
              ? "compose service <code>" + esc(p.build_hook_service) + "</code>"
              : '<span class="muted">none</span>',
          ],
          [
            "Deployed",
            p.last_deployed_sha
              ? "<code>" + esc(shortSha(p.last_deployed_sha)) + "</code> · " + esc(fmtTime(p.last_deployed_at))
              : '<span class="muted">never</span>',
          ],
          [
            "Rollback to",
            p.previous_deployed_sha
              ? "<code>" + esc(shortSha(p.previous_deployed_sha)) + "</code>"
              : '<span class="muted">no earlier deploy recorded</span>',
          ],
          ["Secret", p.secret_set ? "set " + esc(fmtTime(p.secret_created_at)) : '<span class="cicd-status cicd-status--failed">missing</span>'],
        ];
        var lastLine = last
          ? statusPill(lastStatus) +
            " " +
            esc(fmtTime(last.started_at)) +
            (last.commit_sha ? " · <code>" + esc(shortSha(last.commit_sha)) + "</code>" : "") +
            (last.outcome ? '<br /><span class="muted small">' + esc(last.outcome) + "</span>" : "")
          : '<span class="muted">no runs yet</span>';
        return (
          '<article class="cicd-card" data-pipeline="' +
          esc(p.id) +
          '">' +
          '<div class="cicd-card__head">' +
          '<h4 class="cicd-card__title">' +
          esc(p.app_slug) +
          "</h4>" +
          statusPill(p.busy ? "running" : lastStatus) +
          "</div>" +
          '<p class="muted small" style="margin:6px 0 0">' +
          lastLine +
          "</p>" +
          "<dl>" +
          rows
            .map(function (r) {
              return "<dt>" + r[0] + "</dt><dd>" + r[1] + "</dd>";
            })
            .join("") +
          "</dl>" +
          '<div class="cicd-copyrow">' +
          '<code class="cicd-secret__value">' +
          esc(webhook) +
          "</code>" +
          '<button type="button" class="cicd-btn cicd-btn--copy" data-copy-webhook="' +
          esc(webhook) +
          '">Copy webhook URL</button>' +
          "</div>" +
          '<div class="cicd-card__actions">' +
          '<button type="button" class="cicd-btn cicd-btn--primary" data-act="deploy" data-pipeline="' +
          esc(p.id) +
          '"' +
          (p.busy ? " disabled" : "") +
          ">Deploy now</button>" +
          '<button type="button" class="cicd-btn cicd-btn--danger" data-act="rollback" data-pipeline="' +
          esc(p.id) +
          '"' +
          (p.can_rollback && !p.busy ? "" : " disabled") +
          ">Rollback</button>" +
          '<button type="button" class="cicd-btn" data-act="toggle-auto" data-pipeline="' +
          esc(p.id) +
          '">' +
          (p.auto_deploy ? "Pause auto-deploy" : "Resume auto-deploy") +
          "</button>" +
          '<button type="button" class="cicd-btn" data-act="rotate" data-pipeline="' +
          esc(p.id) +
          '">Rotate secret</button>' +
          '<button type="button" class="cicd-btn cicd-btn--danger" data-act="delete" data-pipeline="' +
          esc(p.id) +
          '">Delete</button>' +
          "</div>" +
          "</article>"
        );
      })
      .join("");
  }

  function renderRuns() {
    var body = $("cicdRunsBody");
    if (!body) return;
    if (!state.runs.length) {
      body.innerHTML = '<tr><td colspan="8" class="muted small">No runs recorded yet.</td></tr>';
    } else {
      body.innerHTML = state.runs
        .map(function (run) {
          var steps = (run.steps || [])
            .map(function (s) {
              var cls =
                s.status === "ok" ? "ok" : s.status === "failed" ? "failed" : s.status === "running" ? "running" : "";
              return '<span class="cicd-step-dot' + (cls ? " cicd-step-dot--" + cls : "") + '">' + esc(s.name) + "</span>";
            })
            .join("");
          var commit = run.commit_sha
            ? "<code>" + esc(shortSha(run.commit_sha)) + "</code>" + (run.commit_subject ? " " + esc(run.commit_subject) : "")
            : '<span class="muted">—</span>';
          var coalesced = Number(run.coalesced || 0);
          var cells = [
            esc(fmtTime(run.started_at)),
            "<code>" + esc(run.pipeline_id) + '</code><br /><span class="muted small">' + esc(run.app_slug || "") + "</span>",
            esc(run.trigger || "") + (coalesced ? '<br /><span class="muted small">+' + coalesced + " folded in</span>" : ""),
            commit,
            '<span class="cicd-steps">' + (steps || '<span class="muted">—</span>') + "</span>",
            esc(fmtDuration(run.duration_ms)),
            statusPill(run.status) + (run.outcome ? '<br /><span class="muted small">' + esc(run.outcome) + "</span>" : ""),
            '<button type="button" class="cicd-btn" data-open-run="' + esc(run.run_id) + '">Detail</button>',
          ];
          return (
            '<tr data-run="' +
            esc(run.run_id) +
            '"' +
            (run.run_id === state.selectedRunId ? ' class="cicd-row--selected"' : "") +
            ">" +
            cells
              .map(function (c) {
                return "<td>" + c + "</td>";
              })
              .join("") +
            "</tr>"
          );
        })
        .join("");
    }

    var meta = $("cicdRunsMeta");
    if (meta) {
      var from = state.runsTotal ? state.offset + 1 : 0;
      var to = Math.min(state.offset + state.limit, state.runsTotal);
      meta.textContent = state.runsTotal
        ? "Showing " + from + "–" + to + " of " + state.runsTotal + " run(s), newest first."
        : "No runs match the current filters.";
    }
    renderPager();
  }

  function renderPager() {
    var pager = $("cicdRunsPager");
    if (!pager) return;
    if (state.runsTotal <= state.limit) {
      pager.innerHTML = "";
      return;
    }
    var page = Math.floor(state.offset / state.limit) + 1;
    var pages = Math.max(1, Math.ceil(state.runsTotal / state.limit));
    pager.innerHTML =
      '<span class="mcp-pager__count muted small">Page ' +
      page +
      " of " +
      pages +
      '</span><span class="mcp-pager__controls">' +
      '<button type="button" class="cicd-btn mcp-pager__btn" data-page="first"' +
      (page === 1 ? " disabled" : "") +
      ">« First</button>" +
      '<button type="button" class="cicd-btn mcp-pager__btn" data-page="prev"' +
      (page === 1 ? " disabled" : "") +
      ">‹ Prev</button>" +
      '<button type="button" class="cicd-btn mcp-pager__btn" data-page="next"' +
      (page >= pages ? " disabled" : "") +
      ">Next ›</button>" +
      '<button type="button" class="cicd-btn mcp-pager__btn" data-page="last"' +
      (page >= pages ? " disabled" : "") +
      ">Last »</button>" +
      "</span>";
  }

  function renderRunDetail(run) {
    var host = $("cicdRunDetail");
    if (!host) return;
    if (!run) {
      host.innerHTML = '<p class="muted small">Select a run above to see its steps and log.</p>';
      return;
    }
    var verify = run.verify || {};
    var verifyLine = verify.checked
      ? (verify.ok ? "healthy" : "unhealthy") +
        " · HTTP " +
        esc(verify.status_code === null || verify.status_code === undefined ? "—" : verify.status_code) +
        " · " +
        esc(verify.url || "") +
        (verify.attempts ? " · " + verify.attempts.length + " attempt(s)" : "")
      : "not probed — " + esc(verify.reason || "no verify URL configured");

    var steps = (run.steps || [])
      .map(function (s) {
        var cls = s.status === "ok" ? "ok" : s.status === "failed" ? "failed" : s.status === "running" ? "running" : "";
        return (
          '<div class="cicd-detail__step">' +
          '<span class="cicd-detail__step-name">' +
          esc(s.name) +
          "</span>" +
          '<span class="cicd-step-dot' +
          (cls ? " cicd-step-dot--" + cls : "") +
          '">' +
          esc(s.status) +
          "</span>" +
          '<span class="muted small">' +
          esc(fmtDuration(s.duration_ms)) +
          "</span>" +
          '<span class="cicd-detail__step-detail">' +
          esc(s.detail || "") +
          "</span>" +
          "</div>"
        );
      })
      .join("");

    host.innerHTML =
      '<div class="cicd-card__head">' +
      '<h4 class="cicd-card__title">' +
      esc(run.run_id) +
      "</h4>" +
      statusPill(run.status) +
      "</div>" +
      "<dl>" +
      "<dt>Pipeline</dt><dd><code>" +
      esc(run.pipeline_id) +
      "</code> · " +
      esc(run.app_slug || "") +
      "</dd>" +
      "<dt>Trigger</dt><dd>" +
      esc(run.trigger || "") +
      (run.rollback_of ? " (rolling back from <code>" + esc(shortSha(run.rollback_of)) + "</code>)" : "") +
      "</dd>" +
      "<dt>Commit</dt><dd><code>" +
      esc(run.commit_sha || "—") +
      "</code> " +
      esc(run.commit_subject || "") +
      "</dd>" +
      "<dt>Started</dt><dd>" +
      esc(fmtTime(run.started_at)) +
      "</dd>" +
      "<dt>Finished</dt><dd>" +
      esc(run.finished_at ? fmtTime(run.finished_at) : "in flight") +
      "</dd>" +
      "<dt>Duration</dt><dd>" +
      esc(fmtDuration(run.duration_ms)) +
      "</dd>" +
      "<dt>Verify</dt><dd>" +
      verifyLine +
      "</dd>" +
      "<dt>Outcome</dt><dd>" +
      esc(run.outcome || "—") +
      "</dd>" +
      "</dl>" +
      '<div class="cicd-detail__steps">' +
      (steps || '<p class="muted small">No steps recorded.</p>') +
      "</div>" +
      '<p class="muted small">Captured log (bounded):</p>' +
      '<pre class="cicd-log">' +
      esc(run.log || "(no log captured)") +
      "</pre>";
  }

  // ---------------------------------------------------------------- loading

  async function loadOverview() {
    try {
      var data = await api("/api/cicd/overview");
      state.pipelines = data.pipelines || [];
      if (!data.git_source_available) {
        banner(
          "<strong>Git source module unavailable.</strong> Pipelines can be configured, but the <code>pull</code> step " +
            "will fail until <code>dashboard/git_source.py</code> is present: " +
            esc(data.git_source_error || ""),
          true
        );
      } else {
        banner("");
      }
      renderPipelines();
      syncPipelineFilter();
    } catch (err) {
      banner("Could not load CI/CD state: " + esc(err.message), true);
    }
  }

  function runsQuery() {
    var params = new URLSearchParams();
    var pipeline = $("cicdRunPipeline") ? $("cicdRunPipeline").value : "";
    var status = $("cicdRunStatus") ? $("cicdRunStatus").value : "";
    var trigger = $("cicdRunTrigger") ? $("cicdRunTrigger").value : "";
    if (pipeline) params.set("pipeline", pipeline);
    if (status) params.set("status", status);
    if (trigger) params.set("trigger", trigger);
    params.set("limit", String(state.limit));
    params.set("offset", String(state.offset));
    return params.toString();
  }

  async function loadRuns() {
    try {
      var data = await api("/api/cicd/runs?" + runsQuery());
      state.runs = data.runs || [];
      state.runsTotal = data.total || 0;
      renderRuns();
      if (state.selectedRunId) {
        var stillListed = state.runs.some(function (r) {
          return r.run_id === state.selectedRunId;
        });
        if (stillListed) await openRun(state.selectedRunId, true);
      }
    } catch (err) {
      var body = $("cicdRunsBody");
      if (body) body.innerHTML = '<tr><td colspan="8" class="muted small">Could not load runs: ' + esc(err.message) + "</td></tr>";
    }
  }

  function syncPipelineFilter() {
    var select = $("cicdRunPipeline");
    if (!select) return;
    var current = select.value;
    var options = ['<option value="">All pipelines</option>'].concat(
      state.pipelines.map(function (p) {
        return '<option value="' + esc(p.id) + '">' + esc(p.app_slug) + " (" + esc(p.id) + ")</option>";
      })
    );
    select.innerHTML = options.join("");
    select.value = current;

    var list = $("cicdAppList");
    if (list && !list.dataset.filled) {
      // Suggest registered apps without depending on dashboard.js state.
      fetch("/api/hosted-apps", { cache: "no-store" })
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (data) {
          if (!data) return;
          var apps = data.apps || data.items || [];
          list.innerHTML = apps
            .map(function (a) {
              return '<option value="' + esc(a.id || a.slug || "") + '"></option>';
            })
            .join("");
          list.dataset.filled = "1";
        })
        .catch(function () {
          /* suggestions are optional */
        });
    }
  }

  async function openRun(runId, quiet) {
    state.selectedRunId = runId;
    try {
      var data = await api("/api/cicd/runs/" + encodeURIComponent(runId));
      renderRunDetail(data.run);
    } catch (err) {
      if (!quiet) renderRunDetail(null);
    }
    document.querySelectorAll("#cicdRunsBody tr").forEach(function (tr) {
      tr.classList.toggle("cicd-row--selected", tr.getAttribute("data-run") === runId);
    });
  }

  async function refreshAll() {
    await loadOverview();
    await loadRuns();
  }

  // ---------------------------------------------------------------- actions

  function needsTokenMessage(err) {
    if (err && err.status === 401) {
      return "Unauthorized — set the Control token in the Control tab first.";
    }
    return err ? err.message : "request failed";
  }

  function setStatus(text) {
    var el = $("cicdCreateStatus");
    if (el) el.textContent = text || "";
  }

  function showSecret(secret, webhookUrl, provider) {
    $("cicdSecretValue").textContent = secret;
    $("cicdSecretUrl").textContent = webhookUrl;
    var help =
      provider === "gitlab"
        ? "GitLab: paste the secret into the webhook's Secret token field (sent as X-Gitlab-Token) and enable Push events."
        : "GitHub: paste the secret into the webhook's Secret field, content type application/json, and select 'Just the push event'.";
    $("cicdSecretHelp").textContent = help + " Only pushes to the pipeline's branch trigger a deploy.";
    $("cicdSecretReveal").classList.remove("is-hidden");
    $("cicdSecretReveal").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function createPipeline(evt) {
    evt.preventDefault();
    setStatus("Creating…");
    var payload = {
      app_slug: $("cicdAppSlug").value.trim(),
      repo_url: $("cicdRepoUrl").value.trim(),
      branch: $("cicdBranch").value.trim(),
      provider: $("cicdProvider").value,
      verify_url: $("cicdVerifyUrl").value.trim(),
      build_hook_service: $("cicdBuildHook").value.trim(),
      auto_deploy: $("cicdAutoDeploy").checked,
      token: controlToken(),
    };
    try {
      var data = await api("/api/cicd/pipelines", { method: "POST", body: payload });
      setStatus("");
      $("cicdCreateForm").classList.add("is-hidden");
      $("cicdCreateForm").reset();
      $("cicdBranch").value = "main";
      $("cicdAutoDeploy").checked = true;
      showSecret(data.secret, (data.pipeline && data.pipeline.webhook_url) || "", payload.provider);
      await refreshAll();
    } catch (err) {
      setStatus(needsTokenMessage(err));
    }
  }

  async function pipelineAction(id, act, button) {
    var pipeline = state.pipelines.filter(function (p) {
      return p.id === id;
    })[0];
    if (!pipeline) return;

    if (act === "rollback") {
      var target = pipeline.previous_deployed_sha ? pipeline.previous_deployed_sha.slice(0, 8) : "";
      var confirmed = window.confirm(
        "Roll back " +
          pipeline.app_slug +
          " to commit " +
          target +
          "?\n\n" +
          "This re-checks-out and REDEPLOYS that commit's code, then re-probes the app.\n\n" +
          "It does NOT migrate the database backwards, restore volumes or uploaded files, or undo anything the " +
          "current release already wrote. If the release you are rolling back ran a migration, the older code may " +
          "not work against the newer schema."
      );
      if (!confirmed) return;
    }
    if (act === "delete") {
      if (!window.confirm("Delete pipeline " + id + "? Its webhook secret is destroyed and the Git host webhook stops working. Run history is kept.")) {
        return;
      }
    }

    if (button) button.disabled = true;
    try {
      if (act === "deploy") {
        await api("/api/cicd/pipelines/" + encodeURIComponent(id) + "/run", { method: "POST", body: { token: controlToken() } });
      } else if (act === "rollback") {
        await api("/api/cicd/pipelines/" + encodeURIComponent(id) + "/rollback", { method: "POST", body: { token: controlToken() } });
      } else if (act === "rotate") {
        var rotated = await api("/api/cicd/pipelines/" + encodeURIComponent(id) + "/rotate-secret", {
          method: "POST",
          body: { token: controlToken() },
        });
        showSecret(rotated.secret, pipeline.webhook_url || pipeline.webhook_path, pipeline.provider);
      } else if (act === "toggle-auto") {
        await api("/api/cicd/pipelines/" + encodeURIComponent(id), {
          method: "PATCH",
          body: { auto_deploy: !pipeline.auto_deploy, token: controlToken() },
        });
      } else if (act === "delete") {
        await api("/api/cicd/pipelines/" + encodeURIComponent(id), { method: "DELETE", body: { token: controlToken() } });
      }
      await refreshAll();
    } catch (err) {
      banner(needsTokenMessage(err), true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  // ---------------------------------------------------------------- wiring

  function wire() {
    var tab = $("cicdTab");
    if (!tab || state.booted) return;
    state.booted = true;

    var reload = $("cicdReload");
    if (reload) reload.addEventListener("click", refreshAll);

    var newToggle = $("cicdNewToggle");
    if (newToggle) {
      newToggle.addEventListener("click", function () {
        $("cicdCreateForm").classList.toggle("is-hidden");
      });
    }
    var cancel = $("cicdCreateCancel");
    if (cancel) {
      cancel.addEventListener("click", function () {
        $("cicdCreateForm").classList.add("is-hidden");
        setStatus("");
      });
    }
    var form = $("cicdCreateForm");
    if (form) form.addEventListener("submit", createPipeline);

    var secretCopy = $("cicdSecretCopy");
    if (secretCopy) {
      secretCopy.addEventListener("click", function () {
        copyText($("cicdSecretValue").textContent, secretCopy);
      });
    }
    var urlCopy = $("cicdSecretUrlCopy");
    if (urlCopy) {
      urlCopy.addEventListener("click", function () {
        copyText($("cicdSecretUrl").textContent, urlCopy);
      });
    }
    var dismiss = $("cicdSecretDismiss");
    if (dismiss) {
      dismiss.addEventListener("click", function () {
        $("cicdSecretReveal").classList.add("is-hidden");
        $("cicdSecretValue").textContent = "";
      });
    }

    var pipelinesHost = $("cicdPipelines");
    if (pipelinesHost) {
      pipelinesHost.addEventListener("click", function (evt) {
        var copyBtn = evt.target.closest("[data-copy-webhook]");
        if (copyBtn) {
          copyText(copyBtn.getAttribute("data-copy-webhook"), copyBtn);
          return;
        }
        var actBtn = evt.target.closest("[data-act]");
        if (actBtn) {
          pipelineAction(actBtn.getAttribute("data-pipeline"), actBtn.getAttribute("data-act"), actBtn);
        }
      });
    }

    var runsBody = $("cicdRunsBody");
    if (runsBody) {
      runsBody.addEventListener("click", function (evt) {
        var row = evt.target.closest("tr[data-run]");
        if (row) openRun(row.getAttribute("data-run"), false);
      });
    }

    ["cicdRunPipeline", "cicdRunStatus", "cicdRunTrigger"].forEach(function (id) {
      var el = $(id);
      if (el)
        el.addEventListener("change", function () {
          state.offset = 0;
          loadRuns();
        });
    });
    var limitSel = $("cicdRunLimit");
    if (limitSel) {
      limitSel.addEventListener("change", function () {
        state.limit = parseInt(limitSel.value, 10) || 25;
        state.offset = 0;
        loadRuns();
      });
    }
    var reset = $("cicdRunReset");
    if (reset) {
      reset.addEventListener("click", function () {
        ["cicdRunPipeline", "cicdRunStatus", "cicdRunTrigger"].forEach(function (id) {
          if ($(id)) $(id).value = "";
        });
        if ($("cicdRunLimit")) $("cicdRunLimit").value = "25";
        state.limit = 25;
        state.offset = 0;
        loadRuns();
      });
    }

    var pager = $("cicdRunsPager");
    if (pager) {
      pager.addEventListener("click", function (evt) {
        var btn = evt.target.closest("[data-page]");
        if (!btn || btn.disabled) return;
        var pages = Math.max(1, Math.ceil(state.runsTotal / state.limit));
        var page = Math.floor(state.offset / state.limit) + 1;
        var which = btn.getAttribute("data-page");
        if (which === "first") page = 1;
        else if (which === "prev") page = Math.max(1, page - 1);
        else if (which === "next") page = Math.min(pages, page + 1);
        else if (which === "last") page = pages;
        state.offset = (page - 1) * state.limit;
        loadRuns();
      });
    }

    refreshAll();

    // Poll only while the tab is visible, so a background tab costs nothing.
    state.pollTimer = setInterval(function () {
      if (!tab.classList.contains("active") || document.hidden) return;
      refreshAll();
    }, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();

# Git onboarding and CI/CD

> **Open source** · [MIT License](../LICENSE) · Maintained by [Techtonic Systems Media And Research LLC](https://techtonic.systems/)

Two capabilities that only matter once LEco runs somewhere other than your laptop: onboard an application **from a Git URL** instead of a local folder, and **redeploy it automatically when someone pushes**.

Related: [Production hardening](PRODUCTION_HARDENING.md) — read it before exposing any of this · [Cloud VM deployment](CLOUD_VM_DEPLOYMENT.md) · [LEco app blueprint](LECO_APP_BLUEPRINT.md)

---

## Part 1 — Onboard from a Git repository

On a server there is no Finder and no pre-cloned workspace; the source of truth is a repository. **Hosted apps → Register application → Source: Git repository** takes a URL, clones it, and fills the App root path so the rest of the wizard (Detect → Generate YAML → Register → Deploy) proceeds unchanged.

| Field | Notes |
|-------|-------|
| **Repository URL** | `https://…` or `ssh://` / `git@…`. Plain `http://`, `file://` and `ext::` are refused |
| **Ref** | Branch, tag, or commit. **Check repository** lists what the remote actually has, without cloning |
| **Credential** | Optional: an HTTPS token or an SSH key, for private repositories |

### Where clones land

In order of preference: `LECO_GIT_CLONE_ROOT` if set → the workspace parent **if it is writable** (giving a `wsp:` path) → `hosting/app-sources/` inside the repository (gitignored). The response and the UI state which root was used and why, rather than implying one.

On a laptop the workspace parent is mounted read-only, so clones land in `hosting/app-sources/`. On a server, set `LECO_GIT_CLONE_ROOT` or mount the workspace parent read-write to get `wsp:` paths.

### Credentials

Tokens and keys never reach `git` through the command line or the URL. They are written to `0600` files and passed via `GIT_ASKPASS` / `GIT_SSH_COMMAND`, so nothing is persisted into the clone's `.git/config` — the usual way a token leaks. Saved credentials live in the gitignored `config/git-credentials.yaml` and are only ever returned masked.

If you paste a URL that already contains a token, LEco refuses it and tells you to use the credential field instead. That is deliberate: a URL credential ends up written into the clone.

`git` runs non-interactive with a timeout, so a private repository without a credential fails in under a second with a clear message instead of hanging forever on a password prompt.

---

## Part 2 — CI/CD

A push to your repository triggers **pull → build hook → deploy → verify → record** for one registered app.

### Setting up a pipeline

1. **CI/CD → New pipeline** — pick the registered app, repository URL, branch, and optionally a verify URL and a build hook.
2. For a **private repository**, set the pipeline's **credential** to the id of a credential you saved in the Git source panel. Only the id is stored in the pipeline; the token or key stays in `config/git-credentials.yaml`. A pipeline has no operator to answer a prompt, so without this a private repository fails at the pull step.
3. Copy the **webhook URL** and the **secret** — the secret is shown once, at creation.
4. Add it in your Git host: GitHub → *Settings → Webhooks* (content type `application/json`, secret pasted); GitLab → *Settings → Webhooks* (secret token).

On a real domain, set `LECO_PUBLIC_BASE_URL` on the dashboard so the copy button hands out a reachable address:

```bash
export LECO_PUBLIC_BASE_URL="https://leco.mydomain.com"
./ecosystem-stack/ecosystem-stack.sh restart dashboard
```

Without it the URL is derived from the incoming request and will read `localhost:8090`, which no Git host can reach.

### What a run does

| Step | Behaviour |
|------|-----------|
| **pull** | Checks out the **exact pushed commit**, not the branch tip, so a race with a newer push cannot deploy something else |
| **build** | Optional. Runs a **compose service** the app defines — see the note below |
| **deploy** | The same deploy path the Control tab uses; no second implementation |
| **verify** | A real HTTP probe of the app's URL |
| **record** | Commit, per-step status, duration, captured log, outcome |

**Verify is the point.** A deploy that finishes cleanly while the app returns 502 is recorded as **failed**, and the last-deployed commit is not advanced — so rollback still points at something that actually worked.

### Build hooks run the app's own command, not yours

The build step names a **compose service**; LEco runs `docker compose run --rm --no-deps <service>` with **no command override**. The command itself lives in the application's compose file.

This is a deliberate limitation. An arbitrary command string in a webhook-reachable config would mean one leaked secret equals code execution on the host. Command injection attempts in that field are rejected.

### Safety properties

- **Signature verified before anything else**, with `hmac.compare_digest`, before the payload is parsed and before any state is touched. Unsigned, wrongly-signed and unknown-pipeline requests all get an identical `403` — no enumeration.
- The webhook is authenticated **by its signature, not** by `DASHBOARD_CONTROL_TOKEN`. A Git host cannot send that header. Do not "fix" this by putting the endpoint behind the control token — it would break every webhook.
- **Branch filtering**: a push to a feature branch does not deploy a `main` pipeline.
- **One run per pipeline at a time.** Ten rapid pushes produce one run, not ten; a newer commit arriving mid-run replaces the queued slot so only the newest deploys.
- Non-push events, branch deletions and zero-commit pushes are acknowledged and ignored.
- Secrets live in the gitignored `config/cicd-pipelines.yaml` at mode `0600`, are never returned after creation, and rotation invalidates the old secret immediately.

### Rollback, honestly

Rollback re-checks-out and redeploys the previously deployed commit. **It does not migrate a database backwards.** If a release changed schema, rolling the code back may leave the app pointing at data it cannot read. The UI says so at the point of use.

---

## Troubleshooting

| Symptom | Cause |
|---------|-------|
| Webhook returns `403` | Secret mismatch, or the payload was modified in transit. Rotate the secret and re-paste it |
| Webhook returns `202` with `accepted: false` | Working as intended — usually a branch that does not match the pipeline |
| Ten pushes, one run | Working as intended — runs coalesce per pipeline |
| Run fails at **verify** | The deploy succeeded but the app did not answer. Check `leco_app_logs` / the Logs tab; this is the check doing its job |
| Clone fails instantly on a private repo | No credential configured. Add an HTTPS token or SSH key |
| Webhook URL shows `localhost:8090` | Set `LECO_PUBLIC_BASE_URL` (above) |
| A run shows `interrupted` | The dashboard restarted mid-run. Re-trigger it |

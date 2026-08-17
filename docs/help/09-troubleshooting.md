# Common issues

Start here. For routing specifically, jump to [502 / routing](help:ts-502) or [503 / Varnish backend](help:ts-503).

---

## CI/CD: the Git host says the webhook returned **403**

A `403` from `/api/cicd/webhook/<pipeline-id>` means **the signature did not verify**. Nothing was deployed and nothing was logged as a run — the check happens before the payload is even parsed.

The response is deliberately identical for every cause (`{"ok": false, "error": "forbidden"}`), so an attacker cannot use it to discover which pipelines exist. That also means the response will not tell *you* which of these it was:

| Cause | Fix |
|-------|-----|
| The secret in the Git host does not match the pipeline's | **Rotate secret** on the pipeline card, copy the new value, paste it into the webhook. It is shown **once**. |
| GitHub webhook is not sending a signature | GitHub → *Settings → Webhooks* → the secret field must be filled, and **Content type** must be `application/json`. Signature header: `X-Hub-Signature-256`. |
| GitLab is configured as GitHub (or vice versa) | GitLab sends the secret verbatim in `X-Gitlab-Token`; it does **not** sign the body. Set **Git host** on the pipeline to match, or leave it on *Auto*. |
| A proxy rewrote or re-encoded the request body | The HMAC is over the **raw** body. Any middlebox that reformats JSON invalidates it. |
| The pipeline id in the URL is wrong or was deleted | Re-copy the URL with **Copy webhook URL**. |

Check what the Git host actually saw: GitHub and GitLab both keep a **Recent Deliveries** / **Recent events** list with the request headers and the response.

If the Git host cannot reach the URL at all (timeout, not 403), the copied URL probably says `localhost:8090`. Set `LECO_PUBLIC_BASE_URL=https://leco.example.com` on the dashboard and copy it again.

## CI/CD: the run failed at **verify** but the deploy looked fine

**This is the check working, not a bug.** The pipeline is `pull → build hook → deploy → verify → record`, and **verify is a real HTTP probe** of the app's public URL — up to **6 attempts** with a pause between them.

A deploy that finishes cleanly while the app answers 502, 404, or nothing at all is recorded as **FAILED**, and the **last-deployed commit is not advanced**. That is on purpose: it is what keeps **Rollback** pointing at a commit that actually served traffic.

What to do:

1. Open the run's **Detail** — the captured log shows each attempt and the status code it got.
2. Fix the app, not the pipeline. A 502 here is the same 502 as everywhere else → [502 / routing](help:ts-502): container on `lh-network`, Traefik router present, `loadBalancer` host matching the container name.
3. Confirm by hand: `curl -kIsS https://<app>.lh/<health-path>`.
4. Slow starters (a framework installing dependencies on first boot) can exhaust the attempts. Give the compose service a **healthcheck** so `deploy` waits, rather than widening the probe.

**`verify` reported *skipped*.** No verify URL was configured and none could be derived. Set **Verify URL** on the pipeline, or give the app a main URL in its manifest — otherwise a broken deploy is recorded as a success.

## Onboarding from Git: the clone hangs, times out, or fails on a private repo

The wizard never lets git block the request. Every invocation runs with `GIT_TERMINAL_PROMPT=0`, a wall-clock timeout, and a size guard, and is killed and reported rather than left running.

| Symptom | Cause | Fix |
|---------|-------|-----|
| *"git timed out after 300s and was terminated"* | Large repository, slow network, or git was waiting on something | Leave **Full history** unchecked so the clone stays `--depth 1`. Raise `LECO_GIT_TIMEOUT` for a genuinely huge repo. |
| *"exceeded the 2048 MB size guard; the partial clone was removed"* | The repository really is that large | Raise `LECO_GIT_MAX_CLONE_MB`, or clone it outside LEco and register it as a **Local folder** instead. |
| `Authentication failed` / `could not read Username` | Private repo, no credential | Paste an HTTPS token or an OpenSSH private key into **Token or SSH private key**. Never put the token in the URL — a URL carrying a token is refused, because `git clone` would write it into `.git/config` inside the tree you are about to register. |
| `Permission denied (publickey)` | SSH key wrong, or a passphrase-protected key | Use an unencrypted deploy key, or switch to an HTTPS token. |
| `Host key verification failed` | Unknown SSH host | Use the HTTPS URL, or pre-seed the host key on the dashboard host. |
| Clone root not writable | `hosting/app-sources/` is mounted read-only | Mount it read-write or set `LECO_GIT_CLONE_ROOT` to a writable directory. |

Use **Check repository** *before* **Clone / update & use** — it lists refs without cloning, so a credential or branch-name problem surfaces in a second instead of after a long clone.

Credentials you save go to `config/git-credentials.yaml` (mode `0600`, gitignored) and are redacted from every captured line, so they never appear in the clone, a manifest, a log, or a response.

## AI provider: connect works, then every real call returns **404**

Almost always a **model id from a different vendor**. `gpt-4o-mini` on an **Anthropic** provider authenticates fine — the key is valid, the endpoint is real — and then 404s, because Anthropic has no such model. The same happens with a Claude id on OpenAI, or any model your account is not entitled to.

Fix it the way the screen is designed to be used:

1. **Service hubs → AI providers (LLM access)**.
2. Press **Connect & list models**. LEco calls the provider and lists its **real** catalogue.
3. Click a model from that list — do not type one from memory into **Selected model id**.
4. **Save configuration**.

Check the current answer with `curl -s http://localhost:8090/api/ai/rag/status` — the `provider` block reports the name, model, and where traffic goes.

Watch for this in **Hybrid** mode especially: it holds *two* providers and *two* model ids (a local one and a cloud one), and it is easy to leave a stale cloud model behind when you switch cloud vendors.

Other provider symptoms:

| Message | Meaning |
|---------|---------|
| `No model catalogue at <endpoint> (HTTP 404)` | The **Base URL** is missing its API path. Most OpenAI-compatible services need the `/v1` suffix. |
| `Unauthorized — save the dashboard control token on the Control tab first` | `DASHBOARD_CONTROL_TOKEN` is set; enter it on [Control](help:dash-control). |
| A provider shows *curated list (no live catalogue)* | That vendor's API did not return a catalogue; LEco fell back to a known-good list. |
| The key field is empty after you saved it | Expected. It is never prefilled — the browser only ever receives a mask. Leave it blank to keep the stored key. |

## Local HTTPS says **"Not secure"** on a `*.lh` host

If the certificate was made with `mkcert "*.lh"`, it matches **nothing** — not even `dashboard.lh`.

A wildcard directly below a top-level domain is rejected by RFC 6125 and the CA/Browser Forum rules, because `*.lh` would assert ownership of an entire TLD. curl, Chrome, Safari, Firefox and Python all enforce this. The confusing part is that the *chain* verifies — your mkcert CA is trusted — so `mkcert -install` looks like it worked, and only the hostname check fails.

Regenerate with the repo script, which writes one explicit SAN per hostname:

```bash
./certs/generate-certs.sh
./ecosystem-stack/ecosystem-stack.sh restart traefik
```

It discovers every `*.lh` hostname from `traefik/dynamic.yml`, `hosting/traefik/`, the registry, and `hosting/app-available/`, and it verifies coverage before it finishes. Preview without writing anything:

```bash
./certs/generate-certs.sh --list
```

Confirm a specific host is covered:

```bash
openssl x509 -in certs/wildcard.lh.pem -noout -checkhost myapp.lh
```

**Re-run it after adding a hosted app with a new hostname**, then restart Traefik. Wildcards are still used where they are legal — `*.myapp.lh` has three labels and is valid — so an app publishing `panel.myapp.lh` and `ops.myapp.lh` is covered by one entry.

The script refuses to run when `tls.mode` in `config/leco-platform.yaml` is `acme`, `cloudflare`, or `static`, and tells you who issues certificates in that mode instead. mkcert is **local-only**: its CA exists solely in this machine's trust store, so on a public server every visitor would see an untrusted certificate. See [Platform tab](help:dash-platform) and [PRODUCTION_HARDENING.md](/?tab=docsTab&doc=production-hardening).

---

## Dashboard shows an old UI (missing tabs, no Model manager)

1. Restart the dashboard container:
   ```bash
   ./ecosystem-stack/ecosystem-stack.sh restart dashboard
   ```
2. Hard refresh (`Cmd+Shift+R`).
3. Confirm the `dashboard.js?v=…` query string changed in the page source.

If a tab seems to have vanished: the navigation is now **grouped**. **Hosted apps**, **CI/CD** and **Routes** are under **Deploy ▾**; **Control** and **Infrastructure** under **Operate ▾**; **Metrics**, **Logs** and **Reference** under **Insight ▾**; **Platform** and **MCP** under **Platform ▾**. **Docs** and **Develop** are no longer in the nav at all — reach them from the page footer or `/?tab=docsTab`. See [Dashboard tour](help:dash-overview).

## `airllm` container exits immediately

```bash
docker logs airllm
```

Common causes (fixed in current repo pins):

- `optimum.bettertransformer` missing → pin `optimum<1.18`
- `transformers.utils.is_tf_available` → pin `transformers<4.49`
- Rebuild: `AIRLLM_FORCE_BUILD=1 ./leco-cli.sh airllm build`

## Any action returns *unauthorized*

`DASHBOARD_CONTROL_TOKEN` is set in `ecosystem-stack/services/dashboard.sh`. Enter the matching token once on the [Control tab](help:dash-control); it is reused by Hosted apps, CI/CD, Routes, Platform, and the AI providers panel.

## An MCP tool call shows as **blocked**

On the **MCP** tab, `blocked` is not an error — it means a safety gate refused the call, which is the guard doing its job. Destructive tools and credential access are opt-in and off by default. `error` is the different case: a tool that ran and failed. See [MCP server](help:mcp-server).

## Help page 404 / a help topic will not load

Help content is read from `docs/help/` through the bind mount, so **editing a help page needs no restart** — press **Reload** on the topic. A 404 means the dashboard image or compose file is missing the `docs/` mount, or the topic id does not exist in the help tree.

## More

- [502 / routing / lh-network](help:ts-502)
- [503 / Varnish backend fetch failed](help:ts-503)
- [Git onboarding & CI/CD](help:git-cicd)
- [MCP server](help:mcp-server)
- [Deploy, rebuild & offload](help:deploy-rebuild)
- [Removal & uninstall](help:removal)

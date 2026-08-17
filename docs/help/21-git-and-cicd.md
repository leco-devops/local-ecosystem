# Git onboarding & CI/CD

Onboard an application **from a Git URL** and redeploy it automatically when someone pushes.

## Onboard from Git

**Hosted apps → Register application → Source: Git repository**. Paste the URL, press **Check repository** to list branches without cloning, then **Clone / update & use**. The App root path fills in and the normal wizard continues — Detect → Generate YAML → Register → Deploy.

Private repositories take an HTTPS token or an SSH key. Credentials are written to `0600` files and passed through `GIT_ASKPASS` / `GIT_SSH_COMMAND`, so they never land in the clone's `.git/config`. A URL that already contains a token is refused — paste the token in the credential field instead.

## Set up CI/CD

1. **CI/CD → New pipeline**: choose the app, repository, branch, and optionally a verify URL.
2. Copy the **webhook URL** and the **secret** — the secret is shown once.
3. Paste both into GitHub (*Settings → Webhooks*, content type `application/json`) or GitLab (*Settings → Webhooks*, secret token).

On a real domain set `LECO_PUBLIC_BASE_URL=https://leco.mydomain.com` on the dashboard, or the copied URL says `localhost:8090` and no Git host can reach it.

## What a run does

`pull → build hook → deploy → verify → record`, checking out the exact pushed commit.

**Verify is a real HTTP probe.** A deploy that finishes while the app returns 502 is recorded as **failed** and the last-deployed commit is not advanced — so rollback still points at something that worked.

## Good to know

- A push to a branch the pipeline does not track is acknowledged and ignored.
- Ten rapid pushes produce **one** run, not ten.
- Unsigned or wrongly-signed requests get `403` and change nothing.
- The build hook runs a **compose service your app defines** — not an arbitrary command. That is deliberate: a command string in a webhook-reachable config would be host code execution on one leaked secret.
- **Rollback redeploys the previous commit; it does not migrate a database backwards.**

Full reference: [GIT_AND_CICD.md](/?tab=docsTab&doc=git-and-cicd) in the **Docs** tab. Before exposing any of this publicly, read [PRODUCTION_HARDENING.md](/?tab=docsTab&doc=production-hardening).

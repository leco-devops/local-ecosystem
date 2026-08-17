# Onboarding an application repository

"Onboarding" means: take a repo that already has a `docker-compose.yml` (and/or a
`wrangler.toml`), give it a LEco manifest, register it, route it through Traefik, and deploy
it — **without modifying the upstream repo**.

---

## Path form

App paths use a rooted form, not absolute filesystem paths:

- `wsp:<Name>` — the workspace parent, i.e. the directory *containing* the
  `local-ecosystem` checkout, where your other repos live. This is the normal case.
  `wsp:CrawlerVision`, `wsp:atithify`, `wsp:some/nested/dir`.
- `project:<rel>` — inside the `local-ecosystem` checkout itself.

Always start with `leco_browse(root="wsp")` and use the `path_field` value from the response
verbatim. Do not construct `wsp:` strings by guessing, and do not pass `/Users/...` paths.

---

## The fast path

```
leco_browse(root="wsp")            → find the repo, take its path_field
leco_detect(path)                  → read the report BEFORE deciding anything
leco_onboard(path, app_id)         → detect + manifest + register + Traefik merge + deploy
leco_app_snapshot(slug, sections=["runtime","urls"])   → verify
```

`leco_onboard` reports each stage separately so a failure is attributable. Use it when
`leco_detect` came back clean.

### Reading `leco_detect` properly

`leco_detect` writes nothing. Look at, in order:

1. **`main_url_warnings`.** If there is **no compose signal and no wrangler signal**, the app
   has no infrastructure to route to. Onboarding will "succeed" and produce a hostname that
   404s or 502s forever. Say so to the user and stop — do not onboard hopefully.
2. **`archetype`.** Drives sensible defaults (wordpress, magento2, nextjs, node, php-fpm,
   laravel, static, java, dotnet, generic).
3. **Detected host ports.** Any port the upstream compose publishes will collide with LEco
   core or another stack. The generated overlay applies `ports: !reset []`, but know what is
   being reset.
4. **The previewed manifest.** `full=True` returns the YAML verbatim. If the previewed
   routing does not describe what the app actually serves, take the step-by-step path below
   instead of `leco_onboard`.

### `app_id` is load-bearing

The slug becomes the hostname `<app_id>.lh`, the compose project name, and the registry id.
Changing it later means re-merging Traefik and possibly remapping stale DNS hosts in the
manifest (see `troubleshooting.md` §2). Agree the slug with the user **before** onboarding,
not after.

---

## The step-by-step path

Use this when the app needs hand-tuned routes, non-default ports, a custom health URL, a
split frontend/API, or a local edge runtime.

```
leco_manifest_status(path, app_id)     → is there already a manifest to preserve?
leco_manifest_generate(path, app_id)   → writes leco.app.yaml + the localhost profile
leco_manifest_read(path, app_id)       → read back what was written
   … edit the YAML text …
leco_manifest_validate(manifest_yaml, localhost_yaml, path)   → schema + path check
leco_manifest_save(path, app_id, manifest_yaml, localhost_yaml)
leco_register(path, app_id, deploy=True)
```

`leco_manifest_generate` **overwrites** existing LEco manifests. Always call
`leco_manifest_status` first when the repo might already have hand edits.

`leco_manifest_samples()` lists preset manifest/profile pairs (WordPress, Node API, SPA,
Workers, …). Starting from a sample is usually faster and more correct than editing a
generated manifest from scratch.

`leco_manifest_urls(localhost_yaml, set_urls=[...])` rewrites just the public-URL rows and
returns merged YAML — then write it with `leco_manifest_save`.

---

## What the files mean

```
hosting/app-available/<slug>/
  leco.app.yaml   # BRIDGE  — thin. name, root, localHostProfile, optional configRefs
  leco.yaml       # PROFILE — infrastructure.*, urls, lifecycle, archetype, notes
  source -> …     # symlink to the untouched upstream tree
```

`lecoAppVersion: "3"` keeps the bridge thin: `infrastructure.dockerCompose`,
`infrastructure.cloudflare`, `infrastructure.routing`, `infrastructure.runtimes` all live in
**`leco.yaml`**, not on the bridge. (v2 allowed them on the bridge; do not write new v2.)

Key `infrastructure.dockerCompose` fields:

| Field | Resolved from | Use |
|-------|---------------|-----|
| `composeFile` | app resolved root | the upstream compose file |
| `additionalComposeFiles` | app resolved root | upstream-owned extras |
| `composeFileFromManifest` | the bridge's directory | when the hosting tree should own the **primary** compose file (an entry file that `include:`s the upstream one) |
| `additionalComposeFilesFromManifest` | the bridge's directory | **where LEco-only overlays go** — `docker-compose.leco-hosting.yml`, `docker-compose.leco-runtime.yml` |

The last row is the whole zero-touch story: `lh-network` membership, `ports: !reset []`, and
`*.lh` env defaults are appended as extra `-f` files under
`hosting/app-available/<slug>/`, never patched into the app's repo.

---

## Routing shapes

**Single service:** one `routing.entries[]` with a hostname and a backend.

**Split UI + API on one hostname:** set `apiPathPrefix` (usually `/api`), `frontend`, and
`apiBackend`. The fragment generator emits a high-priority `Host(...) && PathPrefix(/api)`
router at the API container and a low-priority `Host(...)` router at the UI. Traefik
forwards the **full path including `/api`**, so the API must mount its routes under `/api` —
exactly as it does when called directly on `localhost:8001`.

**Modern shape (preferred when present):** `routing.entries[].upstream[]`, a list of
`{prefix, target, runtime?, service?}` rules. `target: runtime` forwards to a sibling
`runtimes[].id`; `target: service` forwards to a Docker DNS name. This replaces the legacy
`frontend`/`apiBackend`/`backendHost` fields and derives router priority from prefix length
(longest wins), so `/health/json` > `/api` > `/` with no manual priority arithmetic.

Upstream backend host must match Docker DNS: the compose `container_name` when set,
otherwise `{dockerCompose.projectName}-{service}-1`.

---

## Verification is part of onboarding

Onboarding is not done when the tool returns success. Do this every time:

1. `leco_app_snapshot(slug, sections=["runtime","urls"])` — containers up, and what do the
   URL probes say? A fresh deploy can take a moment to answer; one retry is reasonable, a
   loop is not.
2. If a URL fails: **404 means no route** (Traefik merge did not happen or the manifest has
   no routing) — **502 means the route exists and the backend is unreachable** (usually
   `lh-network`). These have different fixes; see `troubleshooting.md` §1 and §4.
3. `leco_app_validate(slug)` — schema + on-disk paths. Run this before touching anything
   else; it also auto-heals the hosting overlay.
4. `leco_app_logs(slug)` and `leco_app_insights(slug)` for crash loops and error spikes.

Never respond to a failed probe by redeploying. Find out which of the two failure classes
you have first.

---

## Offboarding

| Want | Tool |
|------|------|
| Unregister + strip Traefik routes, keep containers and files | `leco_app_offboard(slug, confirm=True)` |
| Also stop and delete containers | `leco_app_control(slug, "remove", confirm=True)` |
| Also delete volumes (data loss) | `leco_app_control(slug, "reset", confirm=True)` |

All three need `LECO_MCP_ALLOW_DESTRUCTIVE=1` on the server as well as `confirm=True`.
None of them delete the upstream source repo. `ecosystem-unregister` does remove
`hosting/app-available/<slug>/` when the manifest path is under `hosting/` — so
LEco-generated overlays and any `.dev.vars` there **are** lost.

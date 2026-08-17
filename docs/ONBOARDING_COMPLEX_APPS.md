# Onboarding a complex application

**Last verified:** 2026-08-17, against `utility-server-edge` — 18 Cloudflare Workers behind
`wrangler dev` in one container, four public origins, ports declared in a JavaScript module.

Most applications onboard from `leco_detect` alone. This guide is for the ones that do not, and it
is written from a real failure: an AI onboarding run on this application produced a manifest routing
to **port 3000 on a container that did not exist**, and overwrote a working deployment to do it. The
post-mortem is [AI-ONBOARDING-FINDINGS.md](AI-ONBOARDING-FINDINGS.md).

The model was not careless. Its own reasoning said the port was arbitrary. **It was never shown the
file that had the answer.** Everything below follows from that.

---

## 1. What makes an application "complex" here

Not size. These four properties, any one of which breaks detection-by-convention:

| Property | Why detection alone fails | This app |
|---|---|---|
| Ports declared as **data**, not in compose | The number exists, but in a `.mjs`/`.ts` module | `infra/dev/topology.mjs` |
| Compose **below the root** | A root-only scan finds no compose and generates one | `infra/docker/docker-compose.yml` |
| **Many services, one container** | One container name, many ports, each a different app | 10 workers in `bf-edge` |
| **Several public origins** | One host + `/api` collapses separate security scopes | 4 hostnames |

That last one is a security property, not a tidiness preference. The customer panel and the operator
panel on one origin share a cookie scope.

---

## 2. Ask for evidence before generating anything

```
leco_app_evidence(path="wsp:UtilityServer/utility-server-edge")
```

Real output from this application:

```json
{
  "summary": {
    "compose_files": 1, "compose_services": 4,
    "declared_port_entries": 10, "published_port_pairs": 12,
    "wrangler_configs": 14, "unknown_count": 1
  },
  "port_attribution": {
    "attributed": [
      { "service_name": "edge", "container_name": "bf-edge",
        "published": 8787, "container_port": 8787,
        "owner": "core-router", "owner_source": "infra/dev/topology.mjs" }
    ]
  },
  "unknowns": [
    "2 published container port(s) have no declared owner: bf-origin-ssr:80, bf-origin-spa:80."
  ]
}
```

Three things to take from that shape:

1. **`owner_source` is the point.** Port 8787 belongs to `core-router` *because
   `infra/dev/topology.mjs` says so*. A port with no `owner_source` has no evidence behind it.
   Leave it out of the manifest and report it as undetermined — a wrong port produces a stack that
   builds, starts, and serves nothing, which is far more expensive than an incomplete manifest.
2. **`unknowns` is a real answer.** Two fixture ports genuinely have no declared owner. The tool
   says so instead of inventing one.
3. **`container_port` is what Traefik needs**, not `published`. Traefik reaches the app over
   `lh-network`, where the host-side publish plays no part.

---

## 3. Prefer the application's own compose

A working compose is the strongest available statement of how an app runs. Point at it; do not
generate a replacement.

```yaml
# leco.app.yaml
root: source                                        # symlink to the upstream checkout
configRefs:
  dockerComposeFile: infra/docker/docker-compose.yml
```

**The `source` symlink must be written in container coordinates** — `/workspace-parent/<Org>/<App>`,
matching the other hosted apps. A host-absolute path (`/Users/...`) resolves outside every mount the
dashboard can see, and the app becomes invisible in `/api/hosted-apps` while looking perfectly
correct on the host. Two further traps found the hard way:

- Creating the symlink while the container runs leaves the bind mount stale — `stat()` answers
  `EINVAL` until the container is restarted. `docker restart service-dashboard` clears it.
- One unresolvable app used to 500 the entire listing and hide every healthy app with it. It now
  degrades to a logged warning and that app alone disappears
  (`dashboard/leco_control.py`, `_leco_meta_from_resolved_manifest`).

---

## 4. Overlay for LEco's concerns only

The upstream checkout is never modified. The overlay does exactly two things — join `lh-network`
so Traefik can resolve the container by name, and move host publishes out of a range another
application already holds.

```yaml
services:
  edge:
    networks: [default, lh-network]
    ports: !override          # NOT !reset
      - '18787:8787'
```

> **`!reset` clears the list and leaves it empty; `!override` replaces it.** `ports: !reset` with
> entries beneath it is accepted YAML that yields *zero* published ports. Nothing warns you.
> `leco_compose_validate` merges the real files and reports what Docker actually resolves — run it
> before deploying, not after something fails to answer.

---

## 5. Verify, and read the classification

```
leco_verify(slug="utility-server-edge")
```

Verified run, stack up:

```
utility-server.lh          ok   tls=True  http=404   133.0ms
www.utility-server.lh      ok   tls=True  http=200    19.7ms
panel.utility-server.lh    ok   tls=True  http=200    21.2ms
ops.utility-server.lh      ok   tls=True  http=200    20.1ms
```

**The 404 is a pass.** `core-router` correctly returns 404 for a hostname with no tenant configured,
so the front door answering 404 proves the route and the backend are both working. A naive "expect
200" check reports this mesh as broken exactly when it is healthy — which is why `leco_verify`
classifies rather than asserting a status code.

The classifications map to different fixes, which is the whole reason they are distinct:

| Classification | Meaning | Fix |
|---|---|---|
| `ok` | Router matched, backend answered | — |
| `route_missing` | No Traefik router for the hostname | Register the app; apply routes |
| `backend_unreachable` | Router matched, 502 | Stack not running, or wrong container name/port |
| `tls_invalid` | Certificate does not cover the hostname | `leco_certs_refresh` |
| `unhealthy` | Backend answered 5xx | The application's own problem |

Each result carries the resolved router and the declared backend, so
`backend_unreachable` reads `Router points at http://bf-edge:8787` rather than a bare 502.

---

## 6. When LEco should not own the runtime

This application's workers export `WorkerEntrypoint` and call each other over service-binding RPC.
LEco's bundled `workers-runtime` adapter pins **Miniflare 2**, which predates both, so it cannot
execute this mesh at all. The app therefore runs its own `wrangler dev` — real workerd — inside its
container, and LEco supplies only the edge: hostnames, TLS, Traefik, and the shared network.

That division is worth generalising. **LEco is strongest at the edge and does not need to own the
runtime.** When an app brings a runtime that works, take the edge and leave the runtime alone;
`infrastructure.runtimes[]` is then deliberately absent and validation's warning about it is
expected. Record why in the manifest `notes`, so the next reader does not "fix" it.

---

## Related

- [MCP_SERVER.md](MCP_SERVER.md) — the tools used here
- [AI-ONBOARDING-FINDINGS.md](AI-ONBOARDING-FINDINGS.md) — the failure this guide came from
- [LECO_APP_BLUEPRINT.md](LECO_APP_BLUEPRINT.md) — manifest reference
- [DEPLOY_CUSTOM_APPS.md](DEPLOY_CUSTOM_APPS.md) — the ordinary (non-complex) path

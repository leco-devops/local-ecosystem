# Deploy, rebuild & lifecycle

After onboarding, you **deploy** containers, **rebuild** images, **restart** services, or **offboard** apps. Stack services (dashboard, Traefik, Ollama) use different commands than **hosted apps** (`leco-stack-<slug>`).

## Hosted app deploy (first time)

| Method | Command / action |
|--------|------------------|
| Dashboard | **Hosted apps** → select app → **Deploy** (control token) |
| Register wizard | Check **Deploy stack** on **Register** |
| CLI | `leco-devops deploy -f hosting/app-available/myapp/leco.app.yaml` |
| One-shot onboard | `leco-devops onboard -E "$LECO_ECOSYSTEM_ROOT"` (from app cwd) |

Underlying: `docker compose -f … up -d --build` using paths from effective manifest (`compose_runner.py`).

**Workers-only** apps (no `dockerCompose` in effective manifest): register merges Traefik; deploy step is skipped. Use runtime container controls instead.

## Rebuild after code or compose changes

1. Pull or edit upstream code (sibling repo or `source` target).
2. If infrastructure YAML changed: **Save YAML** or edit `leco.yaml`, then **Register** again if routing/compose file list changed.
3. Rebuild containers:

```bash
# From ecosystem root, manifest path explicit:
leco-devops deploy -f hosting/app-available/myapp/leco.app.yaml

# Or dashboard / Control target leco-stack-myapp → action deploy
```

4. Hard refresh browser if only static assets changed behind Traefik.

### Dashboard image (LEco DevOps UI itself)

After editing `dashboard/` Python, templates, or static files:

```bash
./ecosystem-stack/ecosystem-stack.sh restart dashboard
# or full rebuild:
bash ./ecosystem-stack/services/dashboard.sh deploy
```

Mount `/project` means template/JS edits often need only **container restart**, not image rebuild — unless dependencies changed.

### Traefik after stack route edits

Edit **`traefik/dynamic.yml`** in git, then:

```bash
./ecosystem-stack/ecosystem-stack.sh heal traefik
# or restart traefik service
```

Hosted app routes in **`hosting/traefik/dynamic.yml`** reload via file provider (no full stack restart usually).

## Stop / down / staging / remove

| Action | Keeps files? | Traefik keys | Registry | hosting/app-available |
|--------|--------------|--------------|----------|------------------------|
| **Stop** | Yes | Yes | Yes | Yes |
| **Down** | Yes | Yes | Yes | Yes |
| **Staging / offload** | Yes | Stripped | Yes | Yes |
| **Remove / unregister** | No* | Stripped | Removed | Deleted if manifest under hosting |

\*Unregister runs local CF teardown (when enabled), `docker compose down`, Traefik cleanup, registry removal, deletes `hosting/app-available/<slug>` when manifest path is under `hosting/`.

```bash
leco-devops offload -f hosting/app-available/myapp/leco.app.yaml   # staging
leco-devops ecosystem-unregister myapp -E "$LECO_ECOSYSTEM_ROOT"
```

Dashboard **Control** → target **`leco-stack-myapp`**: actions `deploy`, `stop`, `down`, `staging`, `remove`, `reset`.

**Reset** = down with **`-v`** (volumes) + unregister path.

## Ecosystem stack services

Platform services (not per-app):

```bash
./ecosystem-stack/ecosystem-stack.sh start|stop|restart [service]
./ecosystem-stack/ecosystem-stack.sh heal traefik
./leco-cli.sh                          # interactive menu
```

Services: `traefik`, `dashboard`, `ollama`, `airllm`, `webui`, `n8n`, `postgres`, `cloudflare-local`, …

## Hooks before deploy

```bash
leco-devops run-hooks -f hosting/app-available/myapp/leco.app.yaml --phase prepare
leco-devops run-hooks -f hosting/app-available/myapp/leco.app.yaml --phase build
leco-devops run-hooks -f hosting/app-available/myapp/leco.app.yaml --phase preStart
```

## Verify deployment

1. **Hosted apps** — status, health URLs, recent logs.
2. `curl -fsS -H "Host: myapp.lh" http://127.0.0.1/` (or HTTPS with `-k`).
3. **Routes** tab — merged routers in `hosting/traefik/dynamic.yml`.
4. `docker ps --filter name=myapp` or compose project name from manifest.

## Related

- [Onboarding overview](help:onboarding-overview)
- [Traefik routes](help:traefik-routes)
- [502 troubleshooting](help:ts-502)

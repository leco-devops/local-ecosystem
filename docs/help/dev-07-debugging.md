# Debugging & validation

## Quick validation (every change)

```bash
# From repo root
python3 -m compileall -q dashboard tools/deploy-cli/leco_app
```

## Dashboard development loop

1. Edit `dashboard/` (Python, templates, static)
2. `./ecosystem-stack/ecosystem-stack.sh restart dashboard`
3. Hard refresh browser (cache-busted `?v=` on `dashboard.js` / `dashboard.css`)
4. Hit APIs with curl and `Host: localhost.lh`:

```bash
curl -fsS -H "Host: localhost.lh" http://127.0.0.1/api/overview | python3 -m json.tool | head
curl -fsS -H "Host: localhost.lh" "http://127.0.0.1/api/help/search?q=hosting"
```

## CLI development loop

```bash
cd tools/deploy-cli && pip install -e .
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
leco-devops detect hosting/app-available/myapp
leco-devops traefik-fragment -f hosting/app-available/myapp/leco.app.yaml
```

## Hosted app 502 / routing

Follow **`docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`**:

1. `docker network inspect lh-network` — app container attached?
2. `hosting/traefik/dynamic.yml` — router exists for hostname?
3. `curl -H "Host: myapp.lh" http://127.0.0.1/` from host
4. `./ecosystem-stack/ecosystem-stack.sh heal traefik`

User help: [502 / routing](help:ts-502).

## Traefik empty `http: {}`

```bash
python3 ecosystem-stack/scripts/normalize-hosting-traefik-dynamic.py
./ecosystem-stack/ecosystem-stack.sh heal traefik
```

## Register failures

1. Dashboard register stream: **Hosted apps → Register** (watch NDJSON) or `POST /api/leco/register/stream`
2. Run CLI manually with same manifest:

```bash
leco-devops ecosystem-register -E "$LECO_ECOSYSTEM_ROOT" \
  -f hosting/app-available/myapp/leco.app.yaml --merge-traefik
```

3. Validate YAML:

```bash
curl -X POST -H "Host: localhost.lh" -H "Content-Type: application/json" \
  http://127.0.0.1/api/leco/validate-yaml \
  -d '{"bridge":"...","profile":"..."}'
```

## Control token

Mutations require `DASHBOARD_CONTROL_TOKEN`. UI seeds token when `DASHBOARD_INJECT_CONTROL_TOKEN_UI=1` (local dev).

## Cloudflare-local smoke

```bash
./cloudflare-local/scripts/smoke.sh
```

Requires `*.lh` DNS and adapters running.

## Logs

| Target | Command / UI |
|--------|----------------|
| Hosted app | **Hosted apps** → logs, or `leco-devops logs -f …` |
| Stack service | `./ecosystem-stack/ecosystem-stack.sh` / **Logs** tab |
| Traefik | `docker logs traefik` |
| Dashboard | `docker logs service-dashboard` |

## Docs regression

- New doc in **`docs/`** for Docs tab: add to **`dashboard/docs_catalog.py`**, verify `GET /api/docs/content?id=…`
- New help page: add to **`dashboard/help_manual.py`** + `docs/help/*.md`, verify `/api/help/content?id=…`

## Useful grep entry points

```bash
rg "def register_app_wizard" dashboard/
rg "load_effective_manifest" tools/deploy-cli/
rg "merge_manifest_routing" tools/deploy-cli/
rg "@app\\.(get|post).*hosted" dashboard/app.py
```

## Related user topics

- [Deploy & rebuild](help:deploy-rebuild)
- [Troubleshooting](help:ts-common)

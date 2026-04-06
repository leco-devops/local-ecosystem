# Sample: Node.js multi-process + Varnish + MongoDB + Redis

Use this pattern when onboarding a **multi-process Node.js application** that has:

- Multiple entry scripts (server, worker, cron, queue consumer, etc.)
- Hardcoded `localhost` URIs in a config file (MongoDB, Redis, Varnish, etc.)
- An HTTP cache layer (Varnish) in front of the Express server
- Production service configs (VCL, nginx.conf, redis.conf) that need Docker adaptation

## Architecture

```
Client → Traefik (my-app.lh) → Varnish (:80) → Express (:3000) → MongoDB / Redis
                                                 ├── worker.js   → MongoDB / Redis
                                                 └── cron.js     → MongoDB / Redis
```

## Files

| File | Purpose |
|------|---------|
| `leco.app.yaml` | Bridge — registry slug, root, profile pointer, config refs |
| `leco.yaml` | Profile — compose config, Traefik routing, healthcheck, URL probes |
| `docker-compose.yml` | Base stack — services, volumes, networks, healthchecks |
| `docker-compose.leco-hosting.yml` | Hosting overlay — lh-network, preloader mount, env vars, commands |
| `leco-docker-preload.js` | Runtime config patcher — intercepts `require('./config')` |
| `conf/varnish/default.vcl` | Docker-adapted Varnish VCL (or copy your production VCL here) |

## Quick start

1. Copy this directory to `hosting/app-available/<your-slug>/`
2. Edit every file — replace `my-app` with your slug, adjust paths and service names
3. Copy your production VCL to `conf/varnish/default.vcl` and apply Docker adaptations (see comments in file)
4. Open the dashboard → **Register application** → select your app → **Register**
5. Hit **Recreate** from the control panel

## Key patterns

### Runtime config preloading (`leco-docker-preload.js`)

Instead of modifying upstream source code, the preloader intercepts Node's `require('./config')` at startup and patches the returned object using `LECO_*` environment variables. This means:

- **Zero upstream changes** — the app repo stays clean
- **All config is in docker-compose.leco-hosting.yml** — easy to audit
- **Logs each patch** — `[leco-preload] MONGODB_URI → mongodb://mongo:27017/`

### Custom service configs (`conf/` directory)

Production configs live in `conf/<service>/<config-file>` and are bind-mounted `:ro` into containers. When adapting a production config for Docker:

1. Replace `localhost` / `127.0.0.1` with the Docker `container_name`
2. Update ACLs for Docker bridge subnets (`172.16.0.0/12`)
3. Remove host-based routing (single backend in Docker)
4. Use `vcl 4.1;` for Varnish 7.x (required for `resp.body`, `bereq.backend` assignment)

### Multi-process shared node_modules

The primary service (`server`) runs `npm install` which populates the shared `app_node_modules` volume. Secondary services (`worker`, `cron`) poll for `/app/node_modules/.package-lock.json` before starting — this file is created at the end of `npm install`, acting as a ready signal.

### Staging (offload)

The dashboard **staging** button tears down all containers and volumes (`docker compose down -v --remove-orphans`) and strips Traefik routes, but keeps all files in `hosting/app-available/`. Hit **Recreate** to bring it back.

## Customisation checklist

- [ ] `leco.app.yaml` — `name`, `configRefs.packageJson`
- [ ] `leco.yaml` — `projectName`, `hostname`, `backendHost`, `backendPort`, `healthcheckUrls`, `publicUrl`
- [ ] `docker-compose.yml` — source path, `container_name` prefix, volume names, images
- [ ] `docker-compose.leco-hosting.yml` — `LECO_*` env vars, `LECO_OWN_DOMAINS`, command scripts
- [ ] `leco-docker-preload.js` — config key names to match your app's `config.js` exports
- [ ] `conf/varnish/default.vcl` — backend `.host` and `.port`, ACL, VCL logic

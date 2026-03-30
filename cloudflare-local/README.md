# Cloudflare Local (Docker-only)

Full platform setup (DNS, TLS, AI stack, optional Cloudflare-local) lives in the repo root: **[docs/SETUP.md](../docs/SETUP.md)**. Day-two operations: **[docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)**.

This stack emulates Cloudflare-like local services using only Docker:

- R2-like API → MinIO + `r2-adapter`
- KV-like API → Valkey + `kv-adapter`
- D1-like API → SQLite + `d1-adapter`
- Workers-like runtime → Miniflare 2 + `workers-runtime`
- Container autoscaling simulation → `autoscaler`

Documentation: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/USER_MANUAL.md](docs/USER_MANUAL.md) · [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md)

## Start

```bash
./cloudflare-local/scripts/bootstrap.sh
```

## Seed and smoke tests

```bash
./cloudflare-local/scripts/seed.sh
./cloudflare-local/scripts/smoke.sh
```

## Routed local URLs

- `http://r2.lh`
- `http://kv.lh`
- `http://d1.lh`
- `http://workers.lh`
- `http://autoscale.lh`
- `http://minio-console.lh`

## Direct compose control

```bash
docker compose -f cloudflare-local/docker-compose.yml ps
docker compose -f cloudflare-local/docker-compose.yml logs -f
docker compose -f cloudflare-local/docker-compose.yml down -v
```

# Cloudflare Local — Architecture

**Full local platform setup** (DNS, TLS, ecosystem stack): [../../docs/SETUP.md](../../docs/SETUP.md).

This stack approximates **R2**, **KV**, **D1**, **Workers**, and an **autoscaler** on Docker so applications can be developed against URLs and behaviors similar to Cloudflare’s edge platform, without calling Cloudflare APIs.

## Topology

```text
Traefik (*.lh)
    │
    ├── r2.lh        → r2-adapter (S3 API → MinIO)
    ├── kv.lh        → kv-adapter (Redis protocol → Valkey namespaces/TTL)
    ├── d1.lh        → d1-adapter (SQLite per database, migrations, backups)
    ├── workers.lh   → workers-runtime (Miniflare 2, Workers fetch handler)
    ├── autoscale.lh → autoscaler (Docker CPU metrics → scale labeled replicas)
    └── minio-console.lh → MinIO console
```

All services attach to the external Docker network **`lh-network`** (created by the main ecosystem stack).

## Components

| Piece | Role |
|--------|------|
| **MinIO** | S3-compatible object store backing the R2 adapter. |
| **Valkey** | Redis-compatible store for KV keys with optional TTL. |
| **r2-adapter** | HTTP API mapping to bucket/object operations on MinIO. |
| **kv-adapter** | HTTP API for namespaces and key/value semantics on Valkey. |
| **d1-adapter** | HTTP API for SQLite databases, SQL execution, backups under `/backups`. |
| **workers-runtime** | **Miniflare 2** runs a sample `fetch()` worker on port 8787. Replace `worker.js` with your own script. |
| **autoscaler** | Reads Docker stats for containers labeled as a target group and scales replicas within min/max. |
| **autoscale-demo** | Example `nginx` container with autoscale labels. |

## Data paths

- MinIO and Valkey use named volumes (`minio_data`, `valkey_data`).
- D1 uses `d1_data` and `d1_backups` plus a bind mount for `./migrations`.

## LEco DevOps integration

For the full CF product → LEco service mapping, see [CF_LECO_SERVICE_MAP.md](../../docs/CF_LECO_SERVICE_MAP.md).

**localhost.lh** (LEco DevOps):

- Probes adapter health and resource counts via `GET /api/cloudflare-local`.
- Records **Docker-wide** time series (CPU, memory, network rates, block I/O, disk tracked by the engine) via `GET /api/metrics/history`.
- Offers lifecycle actions via `POST /api/control` when `/project` is mounted and the Docker CLI is available in the LEco DevOps image.

## Limits

- **Not** Cloudflare production: no global edge, no real R2/KV/D1/Workers billing or quotas.
- **Workers**: Miniflare 2 supports a large subset of the Workers runtime; upgrade path is documented in Miniflare/Wrangler release notes.
- **Host metrics**: Values such as “host memory” come from the **Docker engine’s reported totals** (e.g. Docker Desktop VM on macOS), not necessarily the bare-metal OS.

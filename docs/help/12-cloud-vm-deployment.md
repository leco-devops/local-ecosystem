# Cloud VM deployment

Use this guide when LEco DevOps runs on a **Linux cloud VM** (development or preproduction) instead of a local Mac/Windows workstation with `*.lh`.

## Install profiles

Run the cloud installer or foundation script with a profile:

| Profile | What you get |
|---------|----------------|
| `minimal` | Traefik + dashboard |
| `cloudflare-full` | + full Cloudflare-local mimic |
| `ai-full` | + Ollama, AirLLM, Open WebUI, update-catalog |
| `ai-cloud` | Same as `ai-full`; seeds external LLM keys |
| `full` | All ecosystem services |

```bash
./ecosystem-stack/cloud-install.sh --profile ai-cloud --domain dev.example.com --tls acme
```

## Platform settings

Copy `config/leco-platform.yaml.example` to `config/leco-platform.yaml` (gitignored). Set:

- `deployment_mode: cloud`
- `base_domain` — e.g. `dev.example.com`
- `tls.mode` — `acme`, `static`, `mkcert`, or `cloudflare`

Use the dashboard **Platform** tab to edit config, start/stop bundles, and build dev stacks.

## Dev stacks

Create isolated Postgres/MySQL/Redis/Node/Python stacks that do not share one global database. Each stack is its own compose project (`leco-devstack-<id>`).

See [DEV_STACK_ISOLATION.md](../DEV_STACK_ISOLATION.md).

## Hosted apps

Register apps as usual. In cloud mode, public URLs use `base_domain` instead of `*.lh`. Optional `platform.devStackId` in `leco.yaml` binds the app to a dev stack.

## External LLM

For `ai-cloud`, copy and edit `config/ai-providers.yaml` (seeded from the example on install). Configure keys under **Infrastructure → AI-assisted onboarding**.

## Related

- [CLOUD_VM_DEPLOYMENT.md](../CLOUD_VM_DEPLOYMENT.md)
- [SRS_CLOUD_VM_PLATFORM.md](../SRS_CLOUD_VM_PLATFORM.md)

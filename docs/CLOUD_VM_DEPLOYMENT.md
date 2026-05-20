# Cloud VM deployment

Deploy **LEco DevOps** on a Linux cloud VM (development or preproduction) with selective services, custom domains, isolated dev stacks, and external LLM APIs.

Requirements: [SRS_CLOUD_VM_PLATFORM.md](SRS_CLOUD_VM_PLATFORM.md).

## Quick start

```bash
git clone …/local-ecosystem && cd local-ecosystem
cp config/leco-platform.yaml.example config/leco-platform.yaml
cp config/ai-providers.yaml.example config/ai-providers.yaml   # optional

# Cloud install (profile + non-interactive)
./ecosystem-stack/cloud-install.sh --profile ai-cloud --domain dev.example.com --tls acme

# Or explicit profile
LECO_INSTALL_PROFILE=cloudflare-full ./ecosystem-stack/install-foundation.sh \
  --mode cloud --profile cloudflare-full --domain dev.example.com --no-start
./ecosystem-stack/ecosystem-stack.sh start
```

Point DNS `*.dev.example.com` (or listed hosts) to the VM public IP. Open ports **80** and **443**.

## Install profiles

| Profile | Contents |
|---------|----------|
| `minimal` | Traefik + dashboard |
| `platform` | + Postgres, n8n |
| `cloudflare-full` | + full [cloudflare-local](../cloudflare-local/docker-compose.yml) mimic |
| `ai-full` | + Ollama, AirLLM, Open WebUI, update-catalog |
| `ai-cloud` | Same as `ai-full`; installer favors external LLM API keys |
| `infra-full` | + shared [infra](../infra/docker-compose.yml) |
| `full` | All ecosystem services |

Profiles are defined in `ecosystem-stack/config/install-profiles.yaml` and written to `config/leco-platform.yaml`.

## Platform config

`config/leco-platform.yaml` (gitignored) controls:

- `deployment_mode`: `local` | `cloud`
- `base_domain`, `tls.mode` (`mkcert` | `acme` | `static` | `cloudflare`)
- `enabled_services`, `enabled_bundles`
- `dev_stacks[]` registry

Enforced by `ecosystem-stack/core.sh` — `start` without a service name only starts enabled services.

## TLS modes

| Mode | Use |
|------|-----|
| `mkcert` | Local `*.lh` (default) |
| `acme` | Let’s Encrypt via Traefik (`traefik/traefik-static-acme.yaml`) |
| `static` | Operator-provided PEM paths in platform config |
| `cloudflare` | Orange-cloud or Tunnel; see platform notes in config |

Apply domain routes: dashboard **Platform** tab → **Apply Traefik routes**, or:

```bash
python3 scripts/render-platform-traefik.py --write
./ecosystem-stack/services/traefik.sh heal
```

## Dev stacks

Isolated compose projects under `platform/dev-stacks/<id>/` — see [DEV_STACK_ISOLATION.md](DEV_STACK_ISOLATION.md).

Dashboard **Platform** tab → **Dev stack builder** → per-stack **Start**, **Stop**, **Repair**, **Reinstall**, **Destroy** (live operation log). User guide: [help/03-platform-tab.md](help/03-platform-tab.md).

API:

- `GET/POST /api/dev-stacks` — list / create (preset, template, or components)
- `POST /api/dev-stacks/<id>/action` — `start` | `stop` | `destroy` | `repair` | `reinstall`
- `POST /api/dev-stacks/<id>/action/stream` — same actions with NDJSON logs
- `GET /api/dev-stacks/<id>/access` — networking, credentials, quick links for the UI card

## External LLM

Copy `config/ai-providers.yaml.example` → `config/ai-providers.yaml` and set OpenAI, Anthropic, Google, or `openai-compatible` URLs. Configure in **Infrastructure → AI-assisted onboarding**.

## Dashboard

**Platform** tab: config, ecosystem bundles, dev stacks. **Hosted apps** unchanged; URLs use `base_domain` in cloud mode.

## Related

- [SETUP.md](SETUP.md) — local workstation
- [help/12-cloud-vm-deployment.md](help/12-cloud-vm-deployment.md)
- [help/dev-09-platform-cloud.md](help/dev-09-platform-cloud.md)

# Further reading

The complete index: every page in this manual, every canonical document behind it, and where each one opens.

## Everything in this Help manual

| Topic | Page |
|-------|------|
| **Welcome / start here** | [Welcome](help:welcome) |
| Architecture & diagrams | [Architecture & diagrams](help:architecture-diagrams) |
| Requirements & prerequisites | [Requirements](help:requirements) |
| **Installation** | [Ecosystem stack (first-time)](help:install-stack) · [LEco CLI](help:install-cli) · [DNS (`*.lh`) and certificates](help:install-dns) · [Cloud VM — Cloudflare DNS & SSL](help:cloudflare-ssl-install) |
| **Daily operations** | [Dashboard tour](help:dash-overview) · [Platform tab & dev stacks](help:dash-platform) · [Control tab](help:dash-control) · [Infrastructure tab](help:dash-infra) · [FTP & SFTP file transfer](help:file-transfer) |
| **AI agents & automation** | [Paperclip (agent orchestration)](help:paperclip) · [MCP server — agents run LEco](help:mcp-server) |
| **Local AI** | [Ollama](help:ollama) · [AirLLM](help:airllm) · [Ollama vs AirLLM](help:llm-compare) |
| **Updates & LLM catalogs** | [Update catalog service](help:update-catalog-service) · [Stack & model updates (live)](help:ecosystem-updates) · [Ollama catalog](help:llm-catalog-ollama) · [AirLLM catalog](help:llm-catalog-airllm) |
| **Hosting & onboarding** | [Hosting layout & components](help:hosting-layout) · [Onboarding new apps](help:onboarding-overview) · [wsp: paths & materialize](help:onboarding-materialize) · [Git onboarding & CI/CD](help:git-cicd) · [Multi-Wrangler monorepos](help:multi-wrangler-monorepo) · [Attached services panel](help:hosted-app-attached-services) · [Seed data import](help:hosted-app-data-import) · [Cloud VM deployment](help:cloud-vm-deployment) · [Overriding upstream apps](help:hosting-overrides) · [Deploy, rebuild & offload](help:deploy-rebuild) |
| AI news aggregator | [AI news](help:ai-news) |
| **LEco CLI reference** | [`leco-devops` basics](help:cli-basics) · [Hosted apps (dashboard)](help:hosted-apps) · [Traefik routes](help:traefik-routes) |
| **Developer's guide** | [Codebase overview](help:dev-overview) · [Dashboard architecture](help:dev-dashboard) · [CLI & schema](help:dev-cli) · [Registration data flow](help:dev-registration-flow) · [Traefik & routing code](help:dev-traefik) · [Ecosystem stack](help:dev-ecosystem-stack) · [File transfer stack](help:dev-file-transfer) · [MCP server](help:dev-mcp-server) · [Extending LEco](help:dev-extending) · [Debugging & validation](help:dev-debugging) · [Attached services (API)](help:dev-hosted-app-services) · [Data import](help:dev-data-import) · [Platform cloud APIs](help:dev-platform-cloud) |
| Cloudflare local | [Cloudflare local](help:cloudflare) |
| **Troubleshooting** | [Common issues](help:ts-common) · [502 / routing](help:ts-502) · [503 / Varnish backend](help:ts-503) |
| Removal & uninstall | [Removal](help:removal) |
| Further reading | *(this page)* · [Releases & versioning](help:releases-versioning) |

## Canonical technical documents

These open in the **Docs** reader. Docs is no longer in the top navigation — use these links, the page footer, or `/?tab=docsTab`.

| Document | What it covers |
|----------|----------------|
| [ARCHITECTURE.md](/?tab=docsTab&doc=architecture-overview) | System architecture |
| [HLD.md](/?tab=docsTab&doc=architecture-hld) · [LLD.md](/?tab=docsTab&doc=architecture-lld) | High- and low-level design |
| [LECO_USER_MANUAL.md](/?tab=docsTab&doc=leco-user-manual) | **The operator manual — install to first deploy to CI/CD to agents** |
| [LECO_APP_BLUEPRINT.md](/?tab=docsTab&doc=leco-app-blueprint) | Bridge vs profile, compose, Cloudflare, Traefik (canonical) |
| [DEPLOY_CLI.md](/?tab=docsTab&doc=devops-deploy-cli) | `leco-devops` command and YAML field reference |
| [DEPLOY_CUSTOM_APPS.md](/?tab=docsTab&doc=devops-custom-apps) | Custom app routing patterns |
| **[GIT_AND_CICD.md](/?tab=docsTab&doc=git-and-cicd)** | **Git onboarding + CI/CD: pipelines, webhook signatures, runs, rollback** |
| **[MCP_SERVER.md](/?tab=docsTab&doc=mcp-server)** | **MCP server: transports, tool tables, safety gates** |
| [Agent route map](/?tab=docsTab&doc=agent-route-map) | Which tool to reach for, per task |
| **[PRODUCTION_HARDENING.md](/?tab=docsTab&doc=production-hardening)** | **Read before exposing LEco on a real domain** |
| [CLOUD_VM_DEPLOYMENT.md](/?tab=docsTab&doc=cloud-vm-deployment) | Cloud VM operator guide |
| [CLOUDFLARE_SSL_INSTALL.md](/?tab=docsTab&doc=cloudflare-ssl-install) | Cloudflare DNS and edge SSL |
| [DEV_STACK_ISOLATION.md](/?tab=docsTab&doc=dev-stack-isolation) | Dev stack architecture & APIs |
| [HOSTED_APPS_TRAEFIK_RUNBOOK.md](/?tab=docsTab&doc=hosted-apps-traefik-runbook) | 502, `lh-network`, probes |
| [FILE_TRANSFER.md](/?tab=docsTab&doc=file-transfer) | FTP / SFTP / browser stack |
| [UI_CREDENTIAL_VAULT.md](/?tab=docsTab&doc=ui-credential-vault) | Where UI logins are stored |
| [AIRLLM_INTEGRATION.md](/?tab=docsTab&doc=airllm-integration) | AirLLM runtime |
| [CF_LECO_SERVICE_MAP.md](/?tab=docsTab&doc=cf-leco-service-map) | Wrangler binding → local service matrix |
| [SETUP.md](/?tab=docsTab&doc=devops-setup) · [DEPLOYMENT.md](/?tab=docsTab&doc=devops-deployment) | First machine setup; stack deployment |
| [DEVELOPMENT_PLAYBOOK.md](/?tab=docsTab&doc=dev-playbook) | Maintainer daily commands |
| [DEVOPS_GUIDE.md](/?tab=docsTab&doc=devops-guide) | Operations overview |
| [SRS_CLOUD_VM_PLATFORM.md](/?tab=docsTab&doc=srs-cloud-vm-platform) | Cloud VM platform requirements |
| [AGENTS.md](/?tab=docsTab&doc=agents-guide) | Agent / automation guardrails |
| [CHANGELOG](/?tab=docsTab&doc=project-changelog) · [Release notes](/?tab=docsTab&doc=project-release-notes) · [Versioning](/?tab=docsTab&doc=project-versioning) | Project history and release policy |
| [LICENSE](/?tab=docsTab&doc=open-source-license) · [CONTRIBUTING](/?tab=docsTab&doc=open-source-contributing) · [SECURITY](/?tab=docsTab&doc=open-source-security) · [Stewardship](/?tab=docsTab&doc=open-source-stewardship) | Open-source docs |

## Releases & versioning

| Resource | Where |
|----------|--------|
| Current version | Footer on every dashboard page · `GET /api/version` |
| Release index | [Releases & versioning](help:releases-versioning) · Docs → *Release notes* |
| Full history | `CHANGELOG.md` · Docs → *Changelog* |
| Maintainer workflow | `docs/VERSIONING.md` · `tools/release/bump-version.sh` |

## In the repository (not in the Docs reader)

| Path | Topic |
|------|--------|
| `README.md` | Landing page |
| `START_HERE.md` | Route map — install, deploy, operate, drive over MCP |
| `docs/PROJECT.md` | Full repository guide |
| `hosting/README.md` | Writable hosting, `wsp:`, zip |
| `platform/README.md` | Dev stack layout |
| `tools/deploy-cli/README.md` | CLI package overview |
| `tools/mcp-server/README.md` | MCP server package + tool tables |
| `tools/claude-plugin/README.md` | Claude Code plugin and skills |
| `certs/generate-certs.sh` | Local `.lh` certificate generator (read the header comment) |
| `releases/` | Per-version release notes |

## Bookmarks

- **Help:** `https://localhost.lh/help`
- **Service hubs (UI credentials, AI providers):** `https://localhost.lh/hub`
- **Hosted apps:** `https://localhost.lh/?tab=hostedAppsTab`
- **CI/CD:** `https://localhost.lh/?tab=cicdTab`
- **MCP:** `https://localhost.lh/?tab=mcpTab`
- **Platform / dev stacks:** `https://localhost.lh/?tab=platformTab`
- **Register wizard:** Hosted apps → Register application

Use **search** for keywords (`webhook`, `materialize`, `offload`, `composeFileFromManifest`, `schema.py`); use the **tree** for guided learning.

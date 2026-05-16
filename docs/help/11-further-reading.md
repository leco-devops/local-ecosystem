# Further reading

## In this Help manual

| Topic | Help link |
|-------|-----------|
| Onboarding new apps | [Onboarding overview](help:onboarding-overview) |
| Hosting layout | [Hosting layout](help:hosting-layout) |
| Override upstream without forking | [Hosting overrides](help:hosting-overrides) |
| Deploy / rebuild / offload | [Deploy & rebuild](help:deploy-rebuild) |
| Contributor codebase map | [Developer's guide](help:dev-overview) |

## In the dashboard Docs tab

Open **Docs** (`/?tab=docsTab`) for canonical technical references:

- Architecture (HLD/LLD)
- Deploy CLI, App blueprint, User manual
- AirLLM integration
- Development playbook
- Hosted apps Traefik runbook
- **Changelog**, **Release notes**, **Versioning policy** (category **Project**)

## Releases & versioning

| Resource | Where |
|----------|--------|
| Current version | Footer on every dashboard page · `GET /api/version` |
| Release index | [Releases & versioning](help:releases-versioning) · Docs → *Release notes* |
| Full history | `CHANGELOG.md` · Docs → *Changelog* |
| Maintainer workflow | `docs/VERSIONING.md` · `tools/release/bump-version.sh` |

## In the repository

| Path | Topic |
|------|--------|
| `README.md` | Project overview |
| `docs/LECO_APP_BLUEPRINT.md` | Bridge/profile, compose, CF, Traefik (canonical) |
| `docs/LECO_USER_MANUAL.md` | Operator CLI workflows |
| `docs/DEPLOY_CLI.md` | CLI/YAML field reference |
| `docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md` | 502, lh-network, probes |
| `hosting/README.md` | Writable hosting, wsp:, zip |
| `docs/DEPLOYMENT.md` | Stack deployment |
| `docs/DEVELOPMENT_PLAYBOOK.md` | Maintainer daily commands |
| `CHANGELOG.md` | Changelog |
| `docs/RELEASE_NOTES.md` | Release index |
| `docs/VERSIONING.md` | SemVer and release workflow |
| `releases/` | Per-version release notes |
| `AGENTS.md` | Agent/automation guardrails |

## Bookmarks

- **Help:** `https://localhost.lh/help`
- **Hosted apps:** `https://localhost.lh/?tab=hostedAppsTab`
- **Register wizard:** Hosted apps → Register application

Use **search** for keywords (`materialize`, `offload`, `composeFileFromManifest`, `schema.py`); use the **tree** for guided learning.

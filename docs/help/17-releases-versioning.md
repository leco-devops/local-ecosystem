# Releases & versioning

The **LEco DevOps Open Project** uses [Semantic Versioning](https://semver.org/) for the platform. The current version is in the repository root:

- **`VERSION`** — single line (e.g. `0.3.0`)
- **`version.json`** — machine-readable manifest (components, docs paths)

## Where to read more

| What | Where |
|------|--------|
| **Current release summary** | Dashboard **Docs** → *Release notes* |
| **Full changelog** | Dashboard **Docs** → *Changelog* or `CHANGELOG.md` in the repo |
| **Versioning policy** | Dashboard **Docs** → *Versioning policy* or `docs/VERSIONING.md` |
| **Per-release detail** | `releases/vX.Y.Z.md` in the repository |

## Live version in the dashboard

- Footer shows the platform version on each page.
- **`GET /api/version`** returns JSON (version, components, documentation links).

## Releasing (maintainers)

1. Update `CHANGELOG.md` under a new `## [X.Y.Z]` section.
2. Copy `releases/TEMPLATE.md` → `releases/vX.Y.Z.md` and fill highlights + updated files:
   ```bash
   ./tools/release/list-release-files.sh vPREV..HEAD
   ```
3. Bump version:
   ```bash
   ./tools/release/bump-version.sh X.Y.Z
   ```
4. Update `docs/RELEASE_NOTES.md` index, commit, tag `vX.Y.Z`, push.

See **Developer's guide → Debugging & validation** for compile checks before tagging.

## CLI vs platform

- **Platform** (`VERSION`) — dashboard, ecosystem-stack, Traefik, hosting shipped together.
- **`leco-devops` CLI** — separate version in `tools/deploy-cli/pyproject.toml` (PyPI package `leco-app`).

Both are listed in `version.json` under `components`.

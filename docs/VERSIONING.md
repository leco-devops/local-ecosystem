# Versioning — LEco DevOps Open Project

This document defines how the platform is versioned, how release notes are produced, and where to find changelogs in technical documentation.

## Scope

| Artifact | Location | Semantics |
|----------|----------|-----------|
| **Platform version** | `VERSION` (single line) + `version.json` | SemVer for the **whole repository** (dashboard, ecosystem-stack, Traefik, hosting, docs shipped together). |
| **CLI package** | `tools/deploy-cli/pyproject.toml` | Independent SemVer for **`leco-devops`** on PyPI (`leco-app`). Bumped when CLI behavior or schema changes. |
| **Update catalog** | `version.json` → `components.update_catalog` | Internal version for `leco-update-catalog` watcher API/output shape. |
| **Changelog** | `CHANGELOG.md` | Human-readable history ([Keep a Changelog](https://keepachangelog.com/)). |
| **Release notes** | `releases/vX.Y.Z.md` | Per-release narrative: highlights, breaking changes, **updated files** manifest. |
| **Release index** | `docs/RELEASE_NOTES.md` | Table of releases with links and one-line summaries. |

The **application name** in the UI is **LEco DevOps**. The **project / repository brand** is **LEco DevOps Open Project** (see `AGENTS.md`).

## Semantic versioning (platform)

- **MAJOR** — incompatible operational or manifest contract changes (e.g. Traefik merge format, registry schema break).
- **MINOR** — new features, services, or dashboard capabilities (backward compatible).
- **PATCH** — bug fixes, doc-only releases, generated catalog refresh without behavior change.

Pre-1.0 (`0.x.y`): MINOR may include small breaking changes; document them in release notes.

## Release workflow

1. **Finish changes** on a branch; ensure `python3 -m compileall -q dashboard tools/deploy-cli/leco_app` passes.
2. **Update changelog** — move `[Unreleased]` entries into a new `## [X.Y.Z] - YYYY-MM-DD` section in `CHANGELOG.md`.
3. **Write release note** — copy `releases/TEMPLATE.md` to `releases/vX.Y.Z.md`; fill highlights and run file manifest (below).
4. **Bump version:**
   ```bash
   ./tools/release/bump-version.sh X.Y.Z
   ```
   Updates `VERSION`, `version.json`, and stamps `released` in `version.json`.
5. **Update index** — add a row to `docs/RELEASE_NOTES.md`.
6. **Commit**, tag, push:
   ```bash
   git tag -a vX.Y.Z -m "LEco DevOps Open Project vX.Y.Z"
   git push origin vX.Y.Z
   ```

## File manifest (updated files)

Each release note can list paths touched since the previous tag:

```bash
./tools/release/list-release-files.sh v0.2.0..HEAD
# or, before tags exist:
./tools/release/list-release-files.sh v0.2.0..v0.3.0
```

Output is suitable for pasting into `releases/vX.Y.Z.md` under **Updated files**.

## Runtime discovery

| Consumer | How |
|----------|-----|
| **Dashboard API** | `GET /api/version` → JSON from `version.json` + `VERSION`. |
| **Dashboard UI** | Footer shows platform version; **Docs** tab lists changelog and release docs. |
| **Help** | `/help?topic=releases-versioning` → user-oriented summary with links. |
| **Automation** | Read `VERSION` or `version.json` from repo root (mount `/project` in container). |

## Component versions

`version.json` tracks versions that may diverge from the platform:

```json
"components": {
  "platform": { "version": "0.3.0" },
  "leco_cli": { "version": "0.1.10", "package": "leco-app" },
  "update_catalog": { "version": "1.0.0" }
}
```

When releasing a **CLI-only** fix, bump `pyproject.toml` and note it in `CHANGELOG.md` under **leco-devops**; platform `VERSION` may stay unchanged.

## Documentation map

| Doc | Purpose |
|-----|---------|
| [CHANGELOG.md](../CHANGELOG.md) | Full chronological change list |
| [RELEASE_NOTES.md](RELEASE_NOTES.md) | Release index and current version |
| [releases/README.md](../releases/README.md) | Directory layout for `releases/v*.md` |
| [DEVELOPMENT_PLAYBOOK.md](DEVELOPMENT_PLAYBOOK.md) | Day-to-day development (links here) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System context |

## Agents and contributors

- Do not bump `VERSION` in drive-by PRs unless the PR is the release PR.
- Add user-visible features under `[Unreleased]` in `CHANGELOG.md` in the same PR when practical.
- Register new technical docs in `dashboard/docs_catalog.py` if they should appear in the **Docs** tab.

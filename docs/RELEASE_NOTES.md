# Release notes — LEco DevOps Open Project

**Current platform version:** `0.3.0` (see [`VERSION`](../VERSION) and [`version.json`](../version.json))

**Application:** LEco DevOps · **License:** MIT · **Open source** · Maintained by [Techtonic Systems Media and Research LLC](https://techtonic.systems/)

---

## Quick links

| Resource | Path |
|----------|------|
| Full changelog | [CHANGELOG.md](../CHANGELOG.md) |
| Versioning policy | [VERSIONING.md](VERSIONING.md) |
| Release note files | [releases/](../releases/) |
| Live version API | `GET /api/version` on the dashboard |
| CLI package version | `tools/deploy-cli/pyproject.toml` |

---

## Releases

| Version | Date | Summary | Detail |
|---------|------|---------|--------|
| **0.3.0** | 2026-05-16 | Update-catalog UX, inline preloaders, versioning | [releases/v0.3.0.md](../releases/v0.3.0.md) |
| **0.2.0** | 2026-05-16 | AirLLM, update-catalog service, Help manual | [releases/v0.2.0.md](../releases/v0.2.0.md) |
| **0.1.0** | 2026-05-01 | Initial platform: dashboard, stack, CLI, CF-local | [releases/v0.1.0.md](../releases/v0.1.0.md) |

---

## v0.3.0 highlights

- **Updates & catalogs** on Overview: watcher status, schedule editor (every N hours or fixed UTC), mark-all-read, unread highlights.
- **Toolbar:** auto-refresh interval remembered in the browser.
- **Actions:** inline button spinners for server calls (with global preloader and tab indicators).
- **Versioning:** this document, `CHANGELOG.md`, `GET /api/version`, and release file manifests.

See [releases/v0.3.0.md](../releases/v0.3.0.md) for the full note and updated-files list.

---

## v0.2.0 highlights

- **AirLLM** service and Infrastructure model manager.
- **`leco-update-catalog`** Docker watcher and live LLM/stack catalogs in Help.
- **`/help`** user manual with developer guide and Mermaid architecture diagrams.

See [releases/v0.2.0.md](../releases/v0.2.0.md).

---

## Upgrading

1. Pull the tag or branch for the target version.
2. Rebuild affected containers (at minimum after dashboard changes):
   ```bash
   ./ecosystem-stack/ecosystem-stack.sh restart dashboard
   ```
3. If `leco-update-catalog` is used:
   ```bash
   ./ecosystem-stack/services/update-catalog.sh start
   ```
4. Hard-refresh the browser after dashboard static asset bumps (`?v=` on `dashboard.js` / `dashboard.css`).

Operational detail: [DEPLOYMENT.md](DEPLOYMENT.md).

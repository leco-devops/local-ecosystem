# LEco DevOps Open Project vX.Y.Z

**Release date:** YYYY-MM-DD  
**Platform version:** X.Y.Z  
**Git tag:** `vX.Y.Z`

## Summary

One paragraph describing the release theme and who should upgrade.

## Highlights

### Added

- …

### Changed

- …

### Fixed

- …

### Breaking changes

- … (or “None.”)

## Components

| Component | Version | Notes |
|-----------|---------|-------|
| Platform | X.Y.Z | `VERSION` |
| leco-devops CLI | … | `tools/deploy-cli/pyproject.toml` |
| leco-update-catalog | … | `version.json` |

## Upgrade notes

1. Pull `vX.Y.Z`.
2. `./ecosystem-stack/ecosystem-stack.sh restart dashboard` (and other services if noted).
3. Hard-refresh browser.

## Updated files

<!-- Paste output of: ./tools/release/list-release-files.sh vPREV..vX.Y.Z -->

```
(paste here)
```

## Commits (optional)

<!-- git log vPREV..vX.Y.Z --oneline -->

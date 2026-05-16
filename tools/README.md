# Tools

This directory groups optional tooling. Each subfolder is its own project.

## leco-devops (Python CLI)

- **Path:** [`deploy-cli/`](deploy-cli/)
- **Install:** `cd deploy-cli && pip install -e .` (run from repo root: `cd tools/deploy-cli`)

The **`tools/`** directory has no `pyproject.toml`; editable installs must use **`tools/deploy-cli/`**.

See [deploy-cli/README.md](deploy-cli/README.md) and [docs/DEPLOY_CLI.md](../docs/DEPLOY_CLI.md).

## Release tooling

- **Bump platform version:** [`release/bump-version.sh`](release/bump-version.sh)
- **List changed files for a release note:** [`release/list-release-files.sh`](release/list-release-files.sh)

Policy: [docs/VERSIONING.md](../docs/VERSIONING.md) · Index: [docs/RELEASE_NOTES.md](../docs/RELEASE_NOTES.md)

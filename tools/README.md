# Tools

This directory groups optional tooling. Each subfolder is its own project.

## leco-devops (Python CLI)

- **Path:** [`deploy-cli/`](deploy-cli/)
- **Install:** `cd deploy-cli && pip install -e .` (run from repo root: `cd tools/deploy-cli`)

The **`tools/`** directory has no `pyproject.toml`; editable installs must use **`tools/deploy-cli/`**.

See [deploy-cli/README.md](deploy-cli/README.md) and [docs/DEPLOY_CLI.md](../docs/DEPLOY_CLI.md).

## Claude Code plugin

- **Path:** [`claude-plugin/`](claude-plugin/)
- **Install:** `claude plugin marketplace add ./` (repo root) then `claude plugin install leco@leco-devops-open-project`

Packages the **`leco-devops` MCP server** ([`mcp-server/`](mcp-server/)) with an operations skill, the `/leco:status`, `/leco:up`, `/leco:diagnose`, `/leco:onboard`, and `/leco:routes` commands, and a read-only `leco-diagnostician` subagent. Requires `pip install -e tools/mcp-server` first.

Marketplace entry: [`.claude-plugin/marketplace.json`](../.claude-plugin/marketplace.json) · Project-scoped server: [`.mcp.json`](../.mcp.json)

See [claude-plugin/README.md](claude-plugin/README.md).

## Release tooling

- **Bump platform version:** [`release/bump-version.sh`](release/bump-version.sh)
- **List changed files for a release note:** [`release/list-release-files.sh`](release/list-release-files.sh)

Policy: [docs/VERSIONING.md](../docs/VERSIONING.md) · Index: [docs/RELEASE_NOTES.md](../docs/RELEASE_NOTES.md)

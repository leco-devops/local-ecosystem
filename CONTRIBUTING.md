# Contributing to LEco DevOps Open Project

Thanks for contributing to this **community-driven open-source** project.

> **[MIT License](LICENSE)** · [Open source governance](docs/OPEN_SOURCE.md) · Operational steward: [Techtonic Systems Media And Research LLC](https://techtonic.systems/)  
> **Official repository:** [https://github.com/leco-devops/local-ecosystem](https://github.com/leco-devops/local-ecosystem)

## Project scope

- **Project:** LEco DevOps Open Project — **community-owned** under MIT; you contribute via issues and pull requests.
- **Application:** LEco DevOps (the web UI and CLI experience inside this project).
- **Operational steward:** [Techtonic Systems Media And Research LLC](https://techtonic.systems/) — release coordination and project contact (not exclusive ownership).
- **Contact:** [leco@techtonic.systems](mailto:leco@techtonic.systems)

## Top contributors

| Role | Name | Links |
|------|------|--------|
| **Manager & moderator** | [Techtonic Systems Media And Research LLC](https://techtonic.systems/) | [Website](https://techtonic.systems/) · [leco@techtonic.systems](mailto:leco@techtonic.systems) |
| **Contributor** | Rajneesh Maurya | [GitHub](https://github.com/rmaurya) · [LinkedIn](https://www.linkedin.com/in/rajneeshmaurya/) |

## How to contribute

1. Open an issue for bugs, regressions, or feature proposals.
2. Create a focused branch from your latest default branch state.
3. Keep changes scoped and include tests or verification notes.
4. Open a pull request with a clear summary and a test plan.
5. For user-visible changes, add a bullet under `[Unreleased]` in **[CHANGELOG.md](CHANGELOG.md)** (see **[docs/VERSIONING.md](docs/VERSIONING.md)**).

## Local development

- Start with **[docs/SETUP.md](docs/SETUP.md)** for first-time machine setup.
- Use **[docs/DEVELOPMENT_PLAYBOOK.md](docs/DEVELOPMENT_PLAYBOOK.md)** for architecture, service wiring, and extension guidance.
- Read architecture docs before large changes: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**, **[docs/HLD.md](docs/HLD.md)**, **[docs/LLD.md](docs/LLD.md)**, **[docs/LECO_TOOLING.md](docs/LECO_TOOLING.md)**.
- Full repository guide: **[docs/PROJECT.md](docs/PROJECT.md)**.
- For LEco app tooling details, see **[docs/DEPLOY_CLI.md](docs/DEPLOY_CLI.md)** and **[docs/LECO_USER_MANUAL.md](docs/LECO_USER_MANUAL.md)**.

## Safety notes

- Some Control and lifecycle actions are destructive (`remove`, `reset`, volume wipes).
- Use a valid `DASHBOARD_CONTROL_TOKEN` in production-like environments.
- Validate Docker and Traefik impact before merging operational changes.

## License

By contributing, you agree that your contributions are licensed under the MIT License in **[LICENSE](LICENSE)**.

# Contributing to LEco DevOps Open Project

Thanks for contributing.

## Project scope

- **Project:** LEco DevOps Open Project (this repository and community).
- **Application:** LEco DevOps (the web UI and CLI experience inside this project).
- **Maintainer:** Rajneesh Maurya (individual).

## How to contribute

1. Open an issue for bugs, regressions, or feature proposals.
2. Create a focused branch from your latest default branch state.
3. Keep changes scoped and include tests or verification notes.
4. Open a pull request with a clear summary and a test plan.

## Local development

- Start with **[docs/SETUP.md](docs/SETUP.md)** for first-time machine setup.
- Use **[docs/DEVELOPMENT_PLAYBOOK.md](docs/DEVELOPMENT_PLAYBOOK.md)** for architecture, service wiring, and extension guidance.
- Read architecture docs before large changes: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**, **[docs/HLD.md](docs/HLD.md)**, **[docs/LLD.md](docs/LLD.md)**, **[docs/LECO_TOOLING.md](docs/LECO_TOOLING.md)**.
- For LEco app tooling details, see **[docs/DEPLOY_CLI.md](docs/DEPLOY_CLI.md)** and **[docs/LECO_USER_MANUAL.md](docs/LECO_USER_MANUAL.md)**.

## Safety notes

- Some Control and lifecycle actions are destructive (`remove`, `reset`, volume wipes).
- Use a valid `DASHBOARD_CONTROL_TOKEN` in production-like environments.
- Validate Docker and Traefik impact before merging operational changes.

## License

By contributing, you agree that your contributions are licensed under the MIT License in **[LICENSE](LICENSE)**.

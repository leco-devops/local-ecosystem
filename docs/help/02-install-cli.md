# Install — LEco CLI (`leco-devops`)

The CLI deploys **third-party apps** (not the core ecosystem stack).

```bash
cd tools/deploy-cli
pip install -e .
leco-devops --help
```

Entry point name is **`leco-devops`** (PyPI package name remains `leco-app`).

## Point CLI at your ecosystem repo

```bash
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
```

Required for `ecosystem-register`, `onboard`, and Traefik merge commands.

## Smoke test

```bash
leco-devops version
leco-devops detect .
```

## Common commands (hosted apps)

```bash
leco-devops init              # scaffold leco.app.yaml + leco.yaml
leco-devops deploy            # docker compose up
leco-devops onboard -E "$LECO_ECOSYSTEM_ROOT"   # register + merge routes
leco-devops ecosystem-unregister <slug> -E "$LECO_ECOSYSTEM_ROOT"
```

Full reference: open **Docs** tab → *Deploy CLI* or [CLI basics](help:cli-basics).

Next: [DNS setup](help:install-dns)

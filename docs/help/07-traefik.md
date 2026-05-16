# Traefik routes

- **Canonical routes:** `traefik/dynamic.yml` in git
- **Runtime merge target:** `hosting/traefik/dynamic.yml` (hosted apps)
- **Stack copy on Traefik start:** `hosting/traefik/01-stack-core.yml`

## Heal after upgrade or 404

```bash
./ecosystem-stack/ecosystem-stack.sh heal traefik
```

## Dashboard

**Routes** tab — view/edit merged YAML, load fragments from registered apps.

## CLI fragment

```bash
leco-devops traefik-fragment --cwd /path/to/app
```

See **Docs** → *Hosted apps Traefik runbook*.

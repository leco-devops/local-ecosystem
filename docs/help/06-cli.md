# LEco CLI (`leco-devops`)

Install from repo:

```bash
cd tools/deploy-cli && pip install -e .
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem
```

## Core workflows

```bash
leco-devops init -y
leco-devops deploy
leco-devops onboard -E "$LECO_ECOSYSTEM_ROOT"
leco-devops ecosystem-unregister myapp -E "$LECO_ECOSYSTEM_ROOT"
```

## Hosted app commands

```bash
leco-devops detect /path/to/app
leco-devops scaffold myapp -E "$LECO_ECOSYSTEM_ROOT" \
  --template sample-node-varnish-multiprocess --source-path /abs/upstream
leco-devops ecosystem-register -E "$LECO_ECOSYSTEM_ROOT" \
  --registry-manifest-relpath hosting/app-available/myapp/leco.app.yaml --merge-traefik
leco-devops offload -f hosting/app-available/myapp/leco.app.yaml
leco-devops run-hooks -f hosting/app-available/myapp/leco.app.yaml --phase build
leco-devops traefik-fragment -f hosting/app-available/myapp/leco.app.yaml
leco-devops runtimes -f hosting/app-available/myapp/leco.app.yaml --detect
leco-devops provision-local-cf -f hosting/app-available/myapp/leco.app.yaml
```

## Stack helpers (shell)

```bash
./leco-cli.sh                    # interactive menu (stack, hosted apps, ollama, airllm)
./leco-cli.sh ollama install llama3.2:3b
./leco-cli.sh airllm install Qwen/Qwen2.5-0.5B-Instruct
./ecosystem-stack/ecosystem-stack.sh start|stop|restart [service]
./ecosystem-stack/ecosystem-stack.sh heal traefik
```

## Manifest files

| File | Role |
|------|------|
| `leco.app.yaml` | Bridge: name, root, profile pointer |
| `leco.yaml` | URLs, lifecycle, infrastructure (v3) |
| `config/leco-registry.yaml` | Hosted apps registry |

Field-level detail: **Docs** tab → *Deploy CLI*, *App blueprint*, *User manual*.

Next: [Hosted apps](help:hosted-apps) · [Onboarding](help:onboarding-overview)

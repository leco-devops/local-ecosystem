# Removal & uninstall

## Stop the stack (keep data)

```bash
./ecosystem-stack/ecosystem-stack.sh stop
```

## Remove LLM data only

```bash
./leco-cli.sh ollama remove-model <name>    # one model
./leco-cli.sh airllm reset                  # container + HF cache + shard volumes
docker volume rm ollama                    # entire Ollama blob store (if unused)
```

## Remove one ecosystem service

```bash
./ecosystem-stack/ecosystem-stack.sh reset airllm   # destructive for airllm volumes
./ecosystem-stack/ecosystem-stack.sh remove airllm  # container only
```

## Unregister hosted app

```bash
leco-devops ecosystem-unregister <slug> -E "$LECO_ECOSYSTEM_ROOT"
```

## Remove Docker artifacts (broad)

```bash
docker ps -aq --filter name=traefik --filter name=ollama --filter name=airllm | xargs docker rm -f
docker volume ls | grep -E 'ollama|airllm'
docker network rm lh-network   # only if no containers attach
```

## Uninstall CLI

```bash
pip uninstall leco-app
rm -f "$(which leco-devops)"   # if shim remains
```

## Remove clone

```bash
cd ..
rm -rf local-ecosystem
```

Remove `/etc/hosts` entries for `*.lh` if you added them manually.

## macOS Docker Desktop disk image

Deleting the project does **not** shrink Docker Desktop's virtual disk. In Docker Desktop: **Troubleshoot → Clean / purge data**, or remove the disk image file after uninstalling Docker.

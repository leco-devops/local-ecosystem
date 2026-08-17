# Working without the MCP server

When the `leco-devops` MCP tools are unavailable — the plugin is installed but the Python
package is not, the dashboard is down, or you are on a machine that never had it — the same
operations are reachable from three shell entrypoints. Run them from the repository root.

**Announce the degradation.** Tell the user you are falling back to the CLI and why, before
you start. A shell command has no `confirm=` gate and no `LECO_MCP_ALLOW_DESTRUCTIVE`
interlock, so *you* are the safety layer: never run a destructive command without explicit
user agreement in the conversation.

---

## Which entrypoint

| Entrypoint | Scope |
|-----------|-------|
| `./leco-cli.sh` | The friendly front door. Wraps the other two. Start here |
| `./ecosystem-stack/ecosystem-stack.sh` | Raw stack orchestration; `leco-cli.sh stack …` shells into it |
| `leco-devops` | The app toolchain (manifests, register, deploy, dev stacks, Traefik fragments). Needs `pip install -e tools/deploy-cli` |

`./leco-cli.sh` with no arguments opens an interactive menu — **never invoke it bare**, it
will hang waiting for a keypress. Always pass a subcommand. Same for
`./ecosystem-stack/ecosystem-stack.sh menu`.

---

## Diagnostics

```bash
./leco-cli.sh status         # docker, network, services, hosted apps
./leco-cli.sh diagnose       # detailed: DNS, certs, Traefik files, leco-devops presence
./leco-cli.sh urls           # the common *.lh URLs
```

`diagnose` (alias `doctor`) is the closest analogue to `leco_status(detail="services")` plus
`leco_urls`. It is the right first command when you have no MCP.

Reading the dashboard API directly also works and is often the most precise option, because
it is exactly what the MCP tools call:

```bash
curl -s http://localhost:8090/api/version        | python3 -m json.tool
curl -s http://localhost:8090/api/overview       | python3 -m json.tool   # large
curl -s http://localhost:8090/api/control/targets| python3 -m json.tool
curl -s http://localhost:8090/api/hosted-apps    | python3 -m json.tool
curl -s http://localhost:8090/api/traefik/routes | python3 -m json.tool
curl -s "http://localhost:8090/api/leco/browse?root=wsp" | python3 -m json.tool
```

If the dashboard enforces `DASHBOARD_CONTROL_TOKEN`, mutating endpoints need the header;
read endpoints do not.

---

## Stack lifecycle

```bash
./leco-cli.sh stack start [service]
./leco-cli.sh stack stop  [service]
./leco-cli.sh stack restart [service]
./leco-cli.sh stack status [service]
./leco-cli.sh stack logs <service>          # follows
./leco-cli.sh stack deploy                  # bulk: stop all but dashboard, then start all
./leco-cli.sh stack repair-network
```

Same dependency order as through the tools: **Traefik first**, `postgres` before `n8n`,
`paperclip-postgres` before `paperclip`. `stack deploy` with no service does the bulk
sequence for you.

Destructive, and there is no interlock here:

```bash
./leco-cli.sh stack remove [service]   # deletes containers
./leco-cli.sh stack reset  [service]   # deletes containers AND volumes
```

Per-service shortcuts: `./leco-cli.sh dashboard|traefik|cf|ollama|airllm <action>`.

---

## Routing repair

```bash
./leco-cli.sh repair                              # network repair + Traefik heal
./leco-cli.sh traefik heal                        # rebuild hosting/traefik/*, restart Traefik if running
./leco-cli.sh traefik ensure-files                # refresh 01-stack-core.yml from traefik/dynamic.yml
./ecosystem-stack/ecosystem-stack.sh heal traefik # same thing, one layer down
```

`traefik heal` / `ensure-files` is the CLI equivalent of `leco_platform_traefik_apply()` and
the fix for a **global 404** caused by a stale or invalid `hosting/traefik/01-stack-core.yml`.

Inspect what Traefik actually loaded:

```bash
ls hosting/traefik/                                    # 01-stack-core.yml, 20-dev-stacks.yml, dynamic.yml
python3 -c "import yaml,sys;yaml.safe_load(open('hosting/traefik/dynamic.yml'))"   # parse check
docker logs traefik --tail 100
```

Check a hostname without a browser (Traefik is HTTP :80 inside Docker):

```bash
curl -sI -H "Host: myapp.lh" http://127.0.0.1/
docker network inspect lh-network --format '{{range .Containers}}{{.Name}} {{end}}'
```

That last command answers the single most common question: **is the app's container actually
on `lh-network`?** If it is not, that is your 502.

---

## Hosted apps

```bash
./leco-cli.sh apps list
./leco-cli.sh apps status <slug>
./leco-cli.sh apps deploy <slug>
./leco-cli.sh apps stop   <slug>
./leco-cli.sh apps logs   <slug>
./leco-cli.sh apps register <slug>       # re-merge Traefik for an already-registered app
./leco-cli.sh apps fragment <slug>       # print the Traefik fragment the manifest implies
./leco-cli.sh apps provision <slug>      # local KV/R2/D1 from wrangler.toml
./leco-cli.sh apps onboard <path>        # path to leco.app.yaml or its directory
```

`apps register` is the fallback for "app deployed but 404s" — it re-runs the Traefik merge.
`apps fragment` is the fallback for `leco_route_fragment_from_app`; diff it against
`hosting/traefik/dynamic.yml`.

Destructive:

```bash
./leco-cli.sh apps offload <slug>      # down -v + strip Traefik routes; files kept
./leco-cli.sh apps unregister <slug>   # full offboard: compose down + strip routes + registry
```

---

## `leco-devops` directly

From inside the app directory (or with `-f` / `--cwd`):

```bash
export LECO_ECOSYSTEM_ROOT=/path/to/local-ecosystem

leco-devops detect                              # JSON: compose files, wrangler, archetype
leco-devops init                                # wizard → leco.app.yaml + leco.yaml stub
leco-devops init --manifest-only                # no compose yet
leco-devops onboard                             # deploy + register + Traefik merge
leco-devops ecosystem-register --merge-traefik  # register/re-merge only
leco-devops deploy
leco-devops status
leco-devops logs -f
leco-devops down
leco-devops traefik-fragment -o /tmp/app.yml
leco-devops runtimes -f leco.app.yaml           # adapters, declared runtimes, secret wiring
leco-devops offload -E /path/to/local-ecosystem # remove app from localhost (--dry-run first)
```

Platform and dev stacks:

```bash
leco-devops platform show
leco-devops platform presets
leco-devops platform services
leco-devops platform traefik-apply
leco-devops platform bind billing -f hosting/app-available/myapp/leco.app.yaml

leco-devops dev-stack list
leco-devops dev-stack create shop --preset woocommerce --sample-data
leco-devops dev-stack create api --component postgres:16 --component redis:7
leco-devops dev-stack start shop --stream
leco-devops dev-stack repair magento-full        # keeps data — try this first
leco-devops dev-stack reinstall magento-full -y  # WIPES data
leco-devops dev-stack destroy <id>               # WIPES data and removes the stack
leco-devops dev-stack snapshot <id>
leco-devops dev-stack access <id>                # credentials — treat as sensitive
```

`leco-devops runtimes` is the only way to see Worker secret wiring
(`expected: N, wired: M, missing: …`) without the dashboard.

---

## Reinstating the MCP server

If the tools are gone because the package is not installed, this is usually the whole fix:

```bash
pip install -e tools/mcp-server
leco-mcp doctor            # connectivity + configuration report, exits non-zero if unreachable
```

`leco-mcp doctor` prints the resolved dashboard URL, whether the dashboard requires a control
token, whether this server has one, which gates are enabled, and the full tool list. Run it
before concluding anything about why the tools are missing.

Transports: `leco-mcp stdio` (default — what Claude Code uses) and
`leco-mcp http --host 127.0.0.1 --port 8099` (streamable HTTP at `/mcp`, for sharing one
server between clients).

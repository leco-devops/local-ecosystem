# AI-assisted onboarding — findings from a real failure

**Written by:** an agent that had just onboarded the same application by hand, so the correct answer was
known before the AI produced its own. Every claim below names the file and line it came from.

**Subject:** `hosting/app-available/utility-server-edge`, onboarded through
`dashboard/ai_orchestrator.py` with `deepinfra/deepseek-ai/DeepSeek-R1-Distill-Qwen-32B`, 30k budget.

**Scope:** analysis only. Nothing in this repository was modified except this file.

---

## 0. Read this first

**The run destroyed a working, verified configuration.** `hosting/app-available/utility-server-edge/`
contained a hand-authored `leco.yaml`, `leco.app.yaml` and `docker-compose.leco-hosting.yml` that had been
deployed and confirmed serving — panels answering 200 through Traefik with TLS, sixteen production tenants
resolving. All four files now carry the AI's output at 20:40. `leco.yaml` went from ~3 KB to 748 bytes and
now routes `utility-server-edge.lh` to `utility-server-edge-server:3000`, **a container that does not
exist**.

No backup was taken, no diff was shown, and no confirmation was asked.

`ai_orchestrator.py:398-400`:

```python
action = "overwritten" if fp.exists() else "created"
fp.write_text(content, encoding="utf-8")
```

It knows it is overwriting — it computes the word — and writes anyway. The UI then reported all four as
`created:`, so the operator was told nothing was lost. **This is the highest-priority fix in this
document**, and it is independent of every accuracy problem below: an onboarding tool that silently
replaces a working deployment is dangerous even when its analysis is perfect.

---

## 1. What the AI produced, against what is true

| Field | AI answer | Truth | Cost |
|---|---|---|---|
| `listening_port` | **3000** | **8787** (`core-router`) | Traefik routes to a dead port |
| service | `infra/dev/control/server.mjs` | `infra/dev/dev.mjs` | The chosen entry is a *dev control panel*, not the app |
| `port` (service) | `null` | 8787 | Contradicts `listening_port` in the same object |
| services found | UI says **0**, JSON has **1** | 10 workers, one container | See §4 |
| `has_wrangler` | `false` | 18 `wrangler.jsonc` files | Archetype misidentified as plain node |
| `compose_files` | `[]` | `infra/docker/docker-compose.yml` | A working compose was ignored and a new one written |
| `uses_chromium` | `true` | True, but as a **separate container** | Would install Chromium into the app image for nothing |
| public URLs | one host + `/api` | **four** origins | Panels share an origin, so session cookies cross-scope |

The model's own reasoning is worth quoting, because it is honest about the guess:

> *"We'll set 3000 arbitrarily?"* … *"We don't know the port of the control server. We'll leave it as 3000
> arbitrarily."*

**It was not being careless. It was never shown the answer.** Ports for all ten workers are declared in
`infra/dev/topology.mjs`, a file the collector cannot reach. Fix the collector and this guess disappears.

---

## 2. Root causes, verified

### 2.1 The same file is collected twice on macOS — 41% of the budget wasted

`ai_file_collector.py:112` lists both spellings:

```python
"README.md", "readme.md",
```

and dedups by the **name string** (`:174`, `:204`):

```python
collected_names: set[str] = set()
...
if name in collected_names:
    continue
```

APFS is case-insensitive by default, so `root / "README.md"` and `root / "readme.md"` are the *same file*
and both pass `is_file()`. The strings differ, so both are collected.

Observed: `README.md` and `readme.md`, 182 lines and ~1,445 tokens **each** — 2,890 of 3,487 collected
tokens, **41% of everything the model was shown, spent on one file sent twice.** Both were also truncated,
so the duplicate displaced content that would have fit.

**Fix:** dedup on `fp.resolve()` (or `fp.stat().st_ino`), not on the candidate name.

### 2.2 Nothing below the root is searched, except a hardcoded `conf/`

The only subdirectory walk is `ai_file_collector.py:231-256`, for `.conf/.vcl/.ini/.cfg` under `conf/`.
Everything else is `root / name` against a fixed list.

Invisible as a result:

| File | Contains |
|---|---|
| `infra/docker/docker-compose.yml` | The **working** compose, 4 services, every port |
| `infra/dev/topology.mjs` | Every worker's port and inspector port, as data |
| `workers/*/wrangler.jsonc` (18) | Bindings, routes, compat dates |

`package.json` *names* the compose file — `"dev:docker": "docker compose -f infra/docker/docker-compose.yml up --build"` — and
the collector read `package.json` and did not follow it.

**Fix:** parse paths out of the scripts it already reads and collect those, and glob one or two levels for
`docker-compose*.y*ml` and `wrangler.*` before falling back to root-only.

### 2.3 `.mjs` is not recognised — the modern default is invisible

`ai_file_collector.py:140`:

```python
if p.endswith(".js") or p.endswith(".ts"):
```

`"dev.mjs".endswith(".js")` is **`False`**. Every entry script in this project is `.mjs`, so none was
collected. The model saw the *script names* in `package.json` and never the files, which is exactly why it
described `infra/dev/control/server.mjs` as "assumed to be the primary development service" — it was
reasoning from a string.

The same line should also accept `.cjs` and `.mts`.

### 2.4 The script-key allowlist is too narrow

`ai_file_collector.py:136`:

```python
if key in ("start", "dev", "worker", "cron", "queue", "serve"):
```

This project's control surface is `"control": "node infra/dev/control/server.mjs"` — not in the list. Any
project whose scripts are named for its domain rather than for this list is invisible.

**Fix:** invert it. Collect the file referenced by *any* script whose command starts with a known runtime
(`node`, `bun`, `deno`, `tsx`, `python`), and let the model judge relevance — it is better at that than a
six-word allowlist.

### 2.5 `wrangler.jsonc` is missing from the candidate list

`ai_file_collector.py:113` lists `wrangler.toml` and `wrangler.json`. **`wrangler.jsonc` is what
`wrangler init` has generated by default since 2024**, and all 18 workers here use it. Hence
`has_wrangler: false` on a repository that is nothing but Workers.

---

## 3. `workers-runtime` cannot host modern Workers

Separate from onboarding, and it will surface the moment someone trusts it.

`cloudflare-local/adapters/workers-runtime/package.json` pins **`miniflare: ^2.14.4`**.

Miniflare 2 predates `WorkerEntrypoint` and service-binding RPC, which arrived in Miniflare 3 / workerd.
This application has **ten** workers exporting a `WorkerEntrypoint` and calling each other over RPC, at
`compatibility_date: 2026-08-01`. The adapter cannot run any of it.

The manual onboarding therefore ran `wrangler dev` — real workerd — inside the app's own container and used
LEco for hostnames, TLS, Traefik and the shared network. That worked, and is probably the right division of
labour in general: **LEco is excellent at the edge concerns and does not need to own the runtime.**

**Suggestion:** either move the adapter to `miniflare@^3` / `wrangler dev`, or document that it targets
Workers written before service-binding RPC and let apps bring their own runtime. The current state promises
a capability it does not have.

---

## 4. UI reports "0 service(s)" while writing one

The screenshot shows `✓ Analyze — 0 service(s)` for a response whose JSON contains one service object. The
counter is reading a field the model did not populate, or the wrong one. Minor next to the rest, but it is
the number an operator uses to decide whether to trust the run — and here it said "nothing found" while
four files were being written.

---

## 5. Suggested fixes, in the order I would do them

| # | Fix | File | Why first |
|---|---|---|---|
| 1 | **Never overwrite without consent.** Back up to `*.bak`, show a diff, or refuse when the target is non-empty | `ai_orchestrator.py:398` | It destroys working deployments. Correctness of everything else is secondary to not losing work |
| 2 | Dedup collected files by resolved path | `ai_file_collector.py:174,204` | One line; recovers 41% of the token budget on any macOS checkout |
| 3 | Accept `.mjs`/`.cjs`/`.mts` | `ai_file_collector.py:140` | One line; unblocks every modern ESM project |
| 4 | Add `wrangler.jsonc` | `ai_file_collector.py:113` | One line; fixes archetype detection for all current Workers apps |
| 5 | Follow paths found in `package.json` scripts, and glob 1–2 levels for compose/wrangler | `ai_file_collector.py` | Removes the *cause* of the invented port rather than the symptom |
| 6 | Widen the script-key allowlist to any runtime-invoking script | `ai_file_collector.py:136` | Cheap, and monorepos name scripts for their domain |
| 7 | Prefer an existing compose over generating one | generator | "Deploy first, configure never" is the stated USP; a working compose is the strongest signal available and was ignored |
| 8 | Never let the model invent a port — require a source, else leave null and prompt | prompt + schema | A wrong port produces a stack that builds, starts, and serves nothing |
| 9 | Fix the service counter | dashboard | It is the operator's trust signal |
| 10 | Resolve the `workers-runtime` / Miniflare 2 gap | §3 | Silent capability gap |

Items 2–4 are three lines and fix the three most visible symptoms.

---

## 6. What the tool got right

Worth recording, because the failures above are all in one narrow layer and the rest held up:

- Node 22 read correctly from `engines`.
- `data_stores: []` and `cache_layer: null` — correct, and genuinely non-obvious. The legacy predecessor
  ran MongoDB, Redis and Varnish; a naive read of the README would have carried those over. It did not.
- It recognised the app as Cloudflare-native, and said so in `notes` rather than pretending.
- It flagged its own uncertainty in the reasoning trace instead of presenting the guesses as findings.

The model behaved well on the evidence it was given. **Almost every wrong answer traces to a file the
collector could not reach**, which is the encouraging version of this report: the fixes are small, they are
in one file, and they are mostly one-liners.

---

## 7. Restoring the destroyed config

The hand-authored configuration is reconstructable and its author still has it. It differed in ways worth
keeping, as a reference for what a correct manifest looks like here:

- routing for **four** hostnames (`utility-server.lh`, `panel.`, `ops.`, `www.`) rather than one plus
  `/api`, because the panels are separate origins and a shared cookie scope would be a security defect;
- `composeFile` pointed at the app's **existing** `infra/docker/docker-compose.yml`, not a generated one;
- an overlay that joined `lh-network` and remapped host ports into the 18xxx range, because 8787 is held by
  another application on this machine;
- healthchecks against `/healthz` on the panel workers, not the bare front door — `core-router` correctly
  answers 404 for a hostname with no tenant, so a naive check reports the mesh unhealthy exactly when it is
  working.

**Restored, and now verified serving** (2026-08-17). All four properties above are back in
`hosting/app-available/utility-server-edge/`; the AI's output is kept under `.ai-output-20260817-2040/`
for reference. The stack was deployed through LEco's own control path and probed with the new
`leco_verify`:

```
utility-server.lh          ok   tls=True  http=404
www.utility-server.lh      ok   tls=True  http=200
panel.utility-server.lh    ok   tls=True  http=200
ops.utility-server.lh      ok   tls=True  http=200
```

The 404 on the bare front door is the correct answer, for the reason given above — which is why
`leco_verify` classifies instead of asserting a status code.

---

## 8. What was fixed, against the list in §5

| # | Fix | Status |
|---|---|---|
| 1 | Never overwrite without consent | **Done** — `write_generated_files` skips existing files unless `overwrite`, and backs up to `*.bak-<stamp>` |
| 2 | Dedup by resolved path | **Done** — keyed on `(st_dev, st_ino)`; `resolve()` alone is insufficient on a case-insensitive filesystem |
| 3 | Accept `.mjs`/`.cjs`/`.mts` | **Done** |
| 4 | Add `wrangler.jsonc` | **Done** |
| 5 | Follow paths in `package.json` scripts; glob for compose/wrangler | **Done** — collection went from 7 files to 25 on this app |
| 6 | Widen the script-key allowlist | **Done** — any script whose command starts with a known runtime |
| 7 | Prefer an existing compose | **Done** |
| 8 | Never invent a port — require a source | **Done, and this is the structural one.** `leco_app_evidence` returns an `owner_source` naming the file each port came from, and lists what it could not determine under `unknowns`. On this app it now resolves all ten worker ports from `infra/dev/topology.mjs`, including `FRONT_DOOR_PORT: 8787` — the exact number the model previously guessed as 3000 — and reports the two unattributed fixture ports as UNKNOWN rather than filling them in |
| 9 | Fix the service counter | **Done** — `_count_services` handles the list, dict and inline shapes |
| 10 | `workers-runtime` / Miniflare 2 gap | **Documented, not closed.** The adapter still pins Miniflare 2. The division of labour in §3 — app brings the runtime, LEco supplies the edge — is now written up in [ONBOARDING_COMPLEX_APPS.md](ONBOARDING_COMPLEX_APPS.md) §6 |

The encouraging reading in §6 held up: almost every wrong answer traced to a file the collector
could not reach, and the fixes were small and in one place.

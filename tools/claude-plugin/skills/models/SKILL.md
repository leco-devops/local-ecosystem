---
name: models
description: Manage local LLM models on LEco DevOps — list what is installed and resident, search the upstream catalog for what could be installed, pull, inspect, unload, or delete, for the ollama and airllm runtimes. Argument (optional) is a model name or search term, optionally with the runtime.
---

Manage LEco DevOps local LLM models.

Target: **$ARGUMENTS**

Background for anything below: the `leco:operate` skill and its `references/`.

## Two questions, two tools — pick deliberately

- **"What do I have?"** → `leco_llm_models(runtime=…, installed_only=…)`. Returns what is
  installed on disk **and** what is currently resident in memory. Those are different states:
  a model can be installed and not loaded.
- **"What could I get?"** → `leco_llm_catalog(runtime=…, query=…, limit=…)`. The tracked
  upstream catalog. Nothing here is installed.

Answering the first question with the second is the standard mistake and produces a confident
list of models the machine does not have.

## Runtimes are separate stores

`runtime` is `ollama` or `airllm`, and they keep **independent** model stores. A model
present in one is not present in the other, and every one of these tools takes the runtime as
a parameter. State which runtime you are talking about in your report.

If every model call fails at once, the runtime container is down, not the model: start
`ai-ollama` or `ai-airllm` via `/leco:up` and retry.

## Actions

`leco_llm_model_action(action, model=…, runtime=…, confirm=…)`:

- **`pull`** starts in the **background** and returns immediately. Poll `leco_llm_models` to
  watch it land. **Do not block, and do not re-issue the pull because it "did not work"** —
  you will queue a second download of a multi-gigabyte file.
- **`pull_all`** pulls the entire tracked set. That is potentially tens of gigabytes; confirm
  with the user before starting it, and tell them it runs in the background too.
- **`unload`** evicts a model from memory only. It frees RAM/VRAM and keeps the model
  installed — the right action when something else needs the GPU.
- **`delete`** removes it from disk and is **destructive**: it needs `confirm=true` **and** a
  server started with `LECO_MCP_ALLOW_DESTRUCTIVE=1`, plus the user's agreement first. If the
  call is blocked, report the block and stop; do not `docker exec` into the runtime to remove
  it another way.

`leco_llm_model_inspect(model, runtime)` returns the manifest, layers, template and
parameters — the place to check a prompt template or context length before blaming a model
for bad output.

## Without the MCP server

Say so first, then `./leco-cli.sh ollama <action>` / `./leco-cli.sh airllm <action>` for the
runtime containers. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`. Never invoke
`./leco-cli.sh` bare — it opens an interactive menu and hangs. The shell path has no
confirmation gate, so a delete there is immediate and unrecoverable.

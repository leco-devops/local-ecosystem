---
name: docs
description: Search and read the LEco DevOps platform's own documentation — the operator and developer help manuals, the architecture and operations docs, and the tracked ecosystem update feed. Argument is what to look for, or a doc/topic id to read in full.
---

Search the LEco DevOps documentation.

Looking for: **$ARGUMENTS**

## Search order — the platform indexes its own docs, so use that first

1. **`leco_help(search="…")`** — full-text across every operator and developer manual page.
   This is the first call for a how-do-I or why-does-it question. `leco_help(topic_id=…)`
   then reads one page in full, and `leco_help()` with no argument lists the tree.
2. **`leco_docs()`** — the architecture and operations documents (`docs/`). `doc_id=` reads
   one, `category=` narrows the list. Use this for design and structure questions that the
   help manual does not cover.
3. **The `leco:operate` skill's `references/`** — `architecture.md`, `troubleshooting.md`,
   `onboarding.md`, `tool-map.md`, `cli-fallback.md`. These are the condensed operating
   knowledge; the docs are the full text.
4. **Grepping the repository — last.** It is slower, it returns source rather than
   explanation, and it will happily surface a stale comment. Reach for it only when the
   indexed docs genuinely do not cover the thing.

The help and docs tools serve exactly the content the dashboard's Docs and Help tabs serve,
so anything you cite the user can open in the UI. **Cite the topic or doc id** rather than
paraphrasing without a reference — that is the difference between an answer they can verify
and one they have to trust.

## Update feed

`leco_updates(unread_only=True)` lists tracked ecosystem, stack and image updates.
`leco_updates_mark_read()` clears the unread flag — call it **only** when the user has
actually seen the list, never as tidy-up after reading it yourself.

## Report

Answer the question, then cite where it came from (topic id, doc id, or file path). If the
docs contradict what you observe on the machine, say so explicitly rather than picking one —
a drifted doc is itself a finding.

## Without the MCP server

Say so first, then read `docs/` and `docs/help/` directly in the repository —
`docs/MCP_SERVER.md`, `docs/HOSTED_APPS_TRAEFIK_RUNBOOK.md`, `docs/LECO_APP_BLUEPRINT.md`,
`docs/DEV_STACK_ISOLATION.md`, `docs/DEPLOY_CLI.md` — or
`curl -s http://localhost:8090/api/docs/content` if only the tools are missing and the
dashboard is up. Mapping:
`${CLAUDE_PLUGIN_ROOT}/skills/operate/references/cli-fallback.md`.

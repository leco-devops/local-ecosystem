---
name: credentials
description: Read or rotate LEco DevOps local-dev UI credentials for stack services such as n8n, Open WebUI, SFTP and FTP. Argument (optional) is the service slug. Gated — the tools only exist when the MCP server was started with LECO_MCP_ALLOW_CREDENTIALS=1, and the values are plaintext secrets.
---

Work with the LEco DevOps UI credential vault.

Target: **$ARGUMENTS**

Background for anything below: the `leco:operate` skill and its `references/`.

## The gate comes first

`leco_ui_credentials`, `leco_ui_credentials_set` and `leco_ui_credentials_reset` are
**disabled unless the server was started with `LECO_MCP_ALLOW_CREDENTIALS=1`**. If they are
absent, that is the answer: tell the user the variable, tell them the MCP server must be
restarted for it to take effect, and stop. **Do not enable it yourself**, do not edit
`.mcp.json` to enable it, and do not read the credential files off disk to get around it.

## Treat the values as radioactive

`leco_ui_credentials(slug="")` returns **plaintext local-dev secrets**.

Never put a returned value into a log, a commit, a commit message, a PR or issue body, a
file, a summary, or anything else that leaves this machine. Refer to credentials **by name**
("the n8n admin password"). If the user needs an actual value, give it once, in the
conversation, only when they have explicitly asked for it — and say where in the dashboard UI
they can read it themselves instead.

The same rule covers anything that surfaces a secret incidentally: dev stack access output,
connection strings in a snapshot's `services` section, tokens in logs.

## Changing a credential

`leco_ui_credentials_set(slug, values={…})` writes the new value.

For a **protocol service (SFTP / FTP)** this is not a metadata edit: it rewrites
`file-transfer/.env` and **recreates the container**, so **active sessions drop**. Warn the
user before running it, not after, and check nothing is mid-transfer.

`leco_ui_credentials_reset(slug, confirm=True)` regenerates credentials and is
**destructive**: it requires `confirm=true` **and** a server started with
`LECO_MCP_ALLOW_DESTRUCTIVE=1`, on top of the credential gate — and it needs the user's
agreement first, because anything holding the old credential stops working. If the call
returns `Blocked destructive action`, **report the block and its reason and stop**; do not
edit `config/ui-credentials.yaml` or `file-transfer/.env` to accomplish the same thing.

## Credentials that live somewhere else

- **Dev stack credentials** are a different subsystem: `leco_dev_stack_access(id)` reads
  them, `leco_dev_stack_reset_admin(id)` rotates the template admin. See `/leco:dev-stack`.
- **The dashboard control token** is not in the vault. It is `DASHBOARD_CONTROL_TOKEN` on the
  dashboard and `LECO_MCP_CONTROL_TOKEN` on the MCP server, and they must match — see
  `/leco:mcp-server`.

## Without the MCP server

Say so first. The vault is `config/ui-credentials.yaml` (gitignored) with the registry in
`ecosystem-stack/config/ui-login-registry.json`; `docs/UI_CREDENTIAL_VAULT.md` explains the
model. Open the file only when the user has asked for a specific value, and quote only that
one value — do not print the file. There is no confirmation gate on this path at all, so a
rotation you run in the shell happens immediately.

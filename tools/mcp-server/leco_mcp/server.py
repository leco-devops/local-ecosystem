"""Server assembly: build the MCPServer, register every tool module."""

from __future__ import annotations

import time

from mcp.server.mcpserver import MCPServer

from . import prompts
from .activity import ActivityLog, ActivityMiddleware, register_insights_route
from .client import LecoClient
from .config import Settings
from .runtime import Deps
from .tools import REGISTRARS

SERVER_NAME = "leco-devops"
SERVER_VERSION = "0.1.0"

INSTRUCTIONS = """\
LEco DevOps — a local cloud edge on `*.lh`: Traefik routing, Docker Compose orchestration,
isolated dev stacks, local AI runtimes, and one-click onboarding of application repos.

Every tool here calls the LEco DevOps dashboard API, so the dashboard UI and these tools do
exactly the same things. Changes you make are visible in the UI immediately.

Where to start
- leco_server_info   confirm connectivity and which gates are enabled
- leco_status        overall health (service counts, URL probes, alerts)
- leco_control_targets  everything you can start/stop/deploy, with live state

Onboarding a SIMPLE app repo (one service, one port, one hostname)
  leco_browse -> leco_detect -> leco_onboard (detect + manifest + register + deploy)

Onboarding a COMPLEX app — anything with more than one public hostname, a container that
publishes several ports, a compose file of its own, or many workers/processes in one
container. leco_onboard alone will register it and it will not route. Do this instead:

  1 leco_app_evidence(path)        facts: services, container names, {published, target}
                                   port pairs, workers, declared port tables, and `unknowns`
  2 author the manifest, then leco_manifest_save
  3 leco_manifest_overlay(slug, action="write")   join lh-network, remap host ports
  4 leco_compose_validate(...)     prove the merge resolves as intended before deploying
  5 leco_register(path, app_id)    registry + Traefik + deploy
  6 leco_certs_refresh()           a new hostname is not in the certificate until you re-issue
  7 leco_verify(slug=app_id)       every declared origin, classified

Non-negotiable in that flow:
- **Ports come from evidence, never from invention.** Every port must trace to a compose port
  pair or a declared port table with a named source file. If the evidence does not say which
  port a component listens on, ask — do not choose a plausible number. A wrong port yields a
  stack that builds, starts, and serves nothing, and reads as an app bug for hours.
- **Route to the container port.** A container publishing many ports needs one route per
  *container* (`target`) port — not per published host port, and not one route for the whole
  container. Traefik reaches the container over lh-network, where the host publish does not
  exist.
- **In an overlay, `ports: !override` replaces the inherited list; `ports: !reset` clears it**
  and drops everything written under it; a plain `ports:` appends. All three merge without
  error, so run leco_compose_validate and read the resolved ports rather than trusting the
  file you wrote.
- **Never overwrite a manifest or overlay blind.** leco_manifest_overlay reports an existing,
  differing file instead of replacing it; pass overwrite=true deliberately, which keeps a
  timestamped backup. Hosting slots are gitignored — a silent overwrite is unrecoverable.

Use the step tools (leco_manifest_generate / leco_manifest_save / leco_register) when the
app needs hand-tuned routes or ports.

Diagnosing a URL that does not answer
  leco_verify classifies each origin as ok / route_missing / backend_unreachable /
  tls_invalid / unhealthy. Those need different fixes; a bare 502 names none of them.

Infrastructure on/off
  leco_control(target_id, action). Bulk targets exist: stack-ecosystem-all,
  stack-infra-all, stack-file-transfer-all, stack-cf-all. Respect dependency order —
  Traefik first; postgres before n8n; paperclip-postgres before paperclip.

Safety
  `remove` and `reset` delete containers and volumes; `destroy`/`reinstall` wipe a dev
  stack. Those require confirm=true AND a server started with
  LECO_MCP_ALLOW_DESTRUCTIVE=1. If a call is blocked, report that to the user rather than
  looking for a way around it.

Cost
  leco_status and hosted-app snapshots are large and slow (live Docker stats). Prefer the
  default compact views and narrow with filters instead of pulling full payloads.
"""


def build_server(settings: Settings | None = None) -> MCPServer:
    """Construct the MCP server with every tool, prompt, and telemetry hook registered."""
    settings = settings or Settings.from_env()
    deps = Deps(settings=settings, client=LecoClient(settings))
    activity = ActivityLog(
        settings.activity_log_path or None, max_events=settings.activity_max_events
    )

    server = MCPServer(
        name=SERVER_NAME,
        title="LEco DevOps",
        version=SERVER_VERSION,
        instructions=INSTRUCTIONS,
        website_url="https://leco-project.us",
        log_level=settings.log_level if settings.log_level in
        ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else "INFO",
        # Wraps dispatch, so it sees the handshake, every tools/call, and the raised
        # failures that never reach the caller as anything but an isError result.
        middleware=[ActivityMiddleware(activity)],
    )

    for register in REGISTRARS:
        register(server, deps)
    prompts.register(server)

    register_insights_route(
        server,
        activity,
        name=SERVER_NAME,
        version=SERVER_VERSION,
        settings=settings,
        tool_count=lambda: len(server._tool_manager.list_tools()),  # noqa: SLF001 - diagnostics
        started_at=time.monotonic(),
    )
    return server

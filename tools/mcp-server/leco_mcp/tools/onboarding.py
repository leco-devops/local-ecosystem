"""Onboarding: point LEco at an app repo and get it deployed on ``*.lh``.

This is the Hosted apps → *Register application* wizard, exposed step by step plus a single
composite tool (``leco_onboard``) that runs the whole path.

Pipeline:
    browse → detect → generate manifest → (optionally edit/validate) → register → deploy

Paths are not free-form: the dashboard only accepts directories under the project root or
under the configured workspace parent. ``leco_browse`` shows what is reachable, and every
path is expressed the way the dashboard expects it (``wsp:relative/path`` or a repo-relative
path), which ``leco_detect`` echoes back as ``path_field``.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..shaping import guard_size, pick

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

#: Kept whatever ``sections`` asks for: the summary is the cheap orientation and ``unknowns``
#: is the part a caller most needs and is most likely to filter away by accident.
EVIDENCE_ALWAYS_KEEP = ("ok", "root", "path_field", "scan_root_path_field", "summary", "unknowns")

HOSTING_OVERLAY_NAME = "docker-compose.leco-hosting.yml"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _safe_slug(slug: str) -> str:
    s = (slug or "").strip().lower()
    if not _SLUG_RE.match(s):
        raise ValueError(
            f"Invalid app slug {slug!r}. Use lowercase letters, digits, '.', '_' or '-'."
        )
    return s

DETECT_SUMMARY_KEYS = (
    "path_field",
    "scan_root_path_field",
    "root",
    "suggested_archetype",
    "suggested_label",
    "compose_files",
    "has_wrangler",
    "has_wrangler_pages",
    "wrangler_config",
    "wrangler_configs",
    "wrangler_pages_configs",
    "host_ports",
    "existing_manifest",
    "manifest_path",
    "main_url_preview",
    "main_url_preview_https",
    "main_url_host_slug",
    "main_url_warnings",
    "registration_yaml_status",
    "seed_data",
)


def register(server: MCPServer, deps: Deps) -> None:
    client = deps.client
    settings = deps.settings

    @server.tool(name="leco_browse", annotations=READ_ONLY)
    async def leco_browse(root: str = "wsp", path: str = "") -> dict[str, Any]:
        """List directories LEco is allowed to onboard from.

        root="wsp"     the workspace parent (where your other repos live) — the usual case
        root="project" inside the local-ecosystem checkout itself

        Use the returned entries to build the `path` for leco_detect / leco_onboard.
        """
        r = root.strip().lower()
        if r not in ("project", "wsp"):
            raise ValueError('root must be "project" or "wsp".')
        return guard_size(
            await client.get("/api/leco/browse", params={"root": r, "path": path.strip()}),
            settings.max_response_chars,
        )

    @server.tool(name="leco_detect", annotations=READ_ONLY)
    async def leco_detect(path: str, app_id: str = "", full: bool = False) -> dict[str, Any]:
        """Scan an app repo: compose files, Wrangler config, env files, ports, archetype.

        path:   directory as shown by leco_browse (e.g. "wsp:MyApp" or a repo-relative path)
        app_id: proposed slug — drives the previewed *.lh hostname. Defaults to the folder name.

        Returns what LEco found plus a preview of the manifest it would generate. Nothing is
        written. Read main_url_warnings: no compose and no wrangler means the app will not
        route until infrastructure is configured.

        full=true also returns the previewed YAML documents verbatim.
        """
        payload = await client.post(
            "/api/leco/detect",
            json_body={"path": path.strip(), "app_id": app_id.strip()},
            authed=False,
        )
        if full:
            return guard_size(payload, settings.max_response_chars)
        out = pick(payload, "ok", *DETECT_SUMMARY_KEYS)
        signals = payload.get("config_signals") or {}
        # config_signals is 25 booleans; only the true ones carry information.
        out["config_signals"] = sorted(k for k, v in signals.items() if v)
        out["manifest_yaml_preview_chars"] = len(payload.get("manifest_yaml_preview") or "")
        out["localhost_yaml_preview_chars"] = len(payload.get("localhost_yaml_preview") or "")
        out["_hint"] = "Call again with full=true to read the previewed YAML documents."
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_app_evidence", annotations=READ_ONLY)
    async def leco_app_evidence(
        path: str,
        sections: list[
            Literal[
                "compose",
                "overlays",
                "workers",
                "declared_ports",
                "port_attribution",
                "entry_points",
                "unknowns",
            ]
        ]
        | None = None,
    ) -> dict[str, Any]:
        """Structured infrastructure facts for a repo — the input to a correct manifest.

        Run this BEFORE writing any manifest for a non-trivial application. leco_detect says
        what kind of app it is; this says what it is actually made of:

          compose           per file: each service with service_name, container_name, image or
                            build, networks, and port pairs as {published, target}
          overlays          LEco's own docker-compose.leco-*.yml in the hosting slot, with the
                            compose merge directives (!override / !reset) they use
          workers           every wrangler config (.toml/.json/.jsonc) with the worker name
                            read from inside the file
          declared_ports    port tables the repo declares as data, each attributed to its file
          port_attribution  container port -> declaring owner, so "which worker answers on
                            8789" has a source instead of a guess
          entry_points      package.json scripts and the files those commands actually name
          unknowns          what could NOT be determined, stated plainly

        **Ports must come from here, never from invention.** A container publishing many ports
        needs one Traefik route per *container* port (`target`), not per published host port —
        Traefik reaches the container over lh-network where the host publish does not exist.
        Read `unknowns` before you write anything: a named gap is a question to ask the
        operator, not a blank to fill.

        path: directory as shown by leco_browse ("wsp:MyApp" or a repo-relative path). Point it
        at a hosting slot (hosting/app-available/<slug>) to also get the overlay evidence.
        """
        payload = await client.get("/api/leco/evidence", params={"path": path.strip()})
        if sections:
            keep = {*EVIDENCE_ALWAYS_KEEP, *sections}
            payload = {k: v for k, v in payload.items() if k in keep}
        return guard_size(payload, settings.max_response_chars)

    @server.tool(name="leco_compose_validate", annotations=READ_ONLY)
    async def leco_compose_validate(
        path: str,
        compose_file: str,
        overlay_files: list[str] | None = None,
        overlay_yaml: str = "",
        project_name: str = "",
        timeout: int = 60,
    ) -> dict[str, Any]:
        """Run `docker compose config` on a compose file plus overlays; return the RESOLVED merge.

        This is the step that turns "I wrote an overlay" into "the overlay does what I meant".
        Compose list-merge semantics are not guessable from the file:

          ports:            (plain)   APPENDS to the inherited list — the original publishes stay
          ports: !override            REPLACES the inherited list — what a remap almost always wants
          ports: !reset               CLEARS the inherited list — entries under it are DROPPED

        All three merge without error. Only the resolved output tells them apart, and `!reset`
        silently producing zero published ports is a real bug this call caught on this repo.

        path:          allowed directory (same form as leco_detect); every file resolves under it
        compose_file:  path relative to `path` — leco_app_evidence reports it as
                       compose.files[].path_from_hosting_dir for a hosting slot
        overlay_files: additional -f files, applied in order, later files winning
        overlay_yaml:  validate un-saved overlay text without writing it anywhere permanent

        Returns per service: networks and resolved {published, target} port pairs, plus
        summary.services_on_lh_network. On failure it returns docker's stderr, which names the
        offending service and key.
        """
        if not path.strip() or not compose_file.strip():
            raise ValueError("path and compose_file are both required.")
        body: dict[str, Any] = {
            "path": path.strip(),
            "compose_file": compose_file.strip(),
            "overlay_files": [str(x).strip() for x in (overlay_files or []) if str(x).strip()],
            "project_name": project_name.strip(),
            "timeout": max(5, min(int(timeout), 180)),
        }
        if overlay_yaml.strip():
            body["overlay_yaml"] = overlay_yaml
        return guard_size(
            await client.post("/api/leco/compose/validate", json_body=body, authed=False),
            settings.max_response_chars,
        )

    @server.tool(name="leco_manifest_overlay", annotations=WRITES)
    async def leco_manifest_overlay(
        slug: str,
        action: Literal["read", "validate", "write"] = "read",
        overlay_yaml: str = "",
        compose_file: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Read, validate and write a hosting slot's docker-compose.leco-hosting.yml.

        The hosting overlay is the only file LEco adds to an application's compose merge. It
        exists to do two things the app must not be edited for: join `lh-network` so Traefik
        can reach the container by name, and move published host ports off ranges another app
        on this machine already holds.

        **Get the merge directive right.** In an overlay:
          ports: !override   REPLACES the inherited list  <- what a port remap needs
          ports: !reset      CLEARS it; entries written under it are dropped, so the container
                             publishes nothing and the conflict the overlay existed to fix is
                             still there
          ports:  (plain)    APPENDS, leaving the original publishes in place
        All three are valid YAML and all three merge without error.

        action:
          read      current overlay text and the merge directives it uses
          validate  merge `overlay_yaml` (or the file on disk) and report the resolved result
          write     validate first, then write — **an overlay that does not merge cleanly is
                    refused, never written**

        Writing is safe by construction, following dashboard/ai_orchestrator.write_generated_files:
        an existing file whose content differs is REPORTED, not replaced. Pass overwrite=true
        to replace it, and a timestamped `.bak-<stamp>` copy is kept first. This guard exists
        because an AI run once replaced a deployed, verified configuration with no backup and
        no diff (docs/AI-ONBOARDING-FINDINGS.md §0).

        compose_file is the app compose to merge against, relative to the hosting slot —
        leco_app_evidence reports it as compose.files[].path_from_hosting_dir. Omit it and the
        overlay is validated against whatever evidence discovers.

        Requires the repository on local disk (a stdio MCP server). The HTTP container mounts
        no repository and will say so rather than write half a file.
        """
        app = _safe_slug(slug)
        slot = Path(settings.project_root) / "hosting" / "app-available" / app
        overlay_path = slot / HOSTING_OVERLAY_NAME
        rel_slot = f"hosting/app-available/{app}"
        act = action.strip().lower()

        if not slot.is_dir():
            raise ValueError(
                f"No hosting slot at {slot}. Either the app is not materialized, or this MCP "
                "server has no repository on disk (the HTTP container mounts only its generated "
                "config). Use a stdio server on the machine running LEco."
            )

        existing = overlay_path.read_text(encoding="utf-8") if overlay_path.is_file() else None

        if act == "read":
            return guard_size(
                {
                    "ok": True,
                    "slug": app,
                    "path": str(overlay_path),
                    "exists": existing is not None,
                    "overlay_yaml": existing,
                    "_hint": "action='validate' merges it with docker compose config before you trust it.",
                },
                settings.max_response_chars,
            )

        text = overlay_yaml if overlay_yaml.strip() else (existing or "")
        if not text.strip():
            raise ValueError(
                "overlay_yaml is required (nothing on disk to fall back to at "
                f"{overlay_path})."
            )

        target_compose = compose_file.strip()
        if not target_compose:
            evidence = await client.get("/api/leco/evidence", params={"path": rel_slot})
            files = (evidence.get("compose") or {}).get("files") or []
            for row in files:
                candidate = row.get("path_from_hosting_dir") or row.get("file")
                if candidate:
                    target_compose = str(candidate)
                    break
            if not target_compose:
                raise ValueError(
                    "No app compose file was discovered for this slot, so the overlay cannot be "
                    "merge-checked. Pass compose_file explicitly (see leco_app_evidence)."
                )

        merged = await client.post(
            "/api/leco/compose/validate",
            json_body={
                "path": rel_slot,
                "compose_file": target_compose,
                "overlay_yaml": text,
            },
            authed=False,
        )
        merged["compose_file"] = target_compose

        if act == "validate":
            return guard_size({"slug": app, "path": str(overlay_path), **merged},
                              settings.max_response_chars)

        if act != "write":
            raise ValueError("action must be one of: read, validate, write.")

        if not merged.get("ok"):
            return guard_size(
                {
                    "ok": False,
                    "written": False,
                    "slug": app,
                    "path": str(overlay_path),
                    "error": "The overlay does not merge cleanly, so nothing was written.",
                    "compose_error": merged.get("error"),
                    "stderr": merged.get("stderr"),
                    "compose_file": target_compose,
                },
                settings.max_response_chars,
            )

        result: dict[str, Any] = {
            "slug": app,
            "path": str(overlay_path),
            "compose_file": target_compose,
            "merge": merged.get("summary"),
            "services": [
                {
                    "service_name": s.get("service_name"),
                    "container_name": s.get("container_name"),
                    "networks": s.get("networks"),
                    "port_pairs": [
                        {"published": p.get("published"), "target": p.get("target")}
                        for p in s.get("port_pairs") or []
                    ],
                }
                for s in merged.get("services") or []
            ],
        }

        if existing is None:
            overlay_path.write_text(text, encoding="utf-8")
            return guard_size({"ok": True, "written": True, "action_taken": "created", **result},
                              settings.max_response_chars)
        if existing == text:
            return guard_size(
                {"ok": True, "written": False, "action_taken": "unchanged", **result},
                settings.max_response_chars,
            )
        if not overwrite:
            return guard_size(
                {
                    "ok": False,
                    "written": False,
                    "action_taken": "skipped_exists",
                    "reason": (
                        f"{HOSTING_OVERLAY_NAME} already exists at {overlay_path} and differs "
                        "from what you passed. Nothing was written. Read it with action='read', "
                        "decide deliberately, then call again with overwrite=true — a "
                        "timestamped backup is kept either way."
                    ),
                    "existing_chars": len(existing),
                    "proposed_chars": len(text),
                    **result,
                },
                settings.max_response_chars,
            )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = overlay_path.with_name(f"{overlay_path.name}.bak-{stamp}")
        try:
            backup.write_text(existing, encoding="utf-8")
        except OSError as exc:
            return guard_size(
                {
                    "ok": False,
                    "written": False,
                    "action_taken": "failed",
                    "reason": (
                        f"Could not write the backup {backup.name}: {exc}. "
                        f"{HOSTING_OVERLAY_NAME} was left untouched — losing the original is "
                        "exactly what this guard prevents."
                    ),
                    **result,
                },
                settings.max_response_chars,
            )
        overlay_path.write_text(text, encoding="utf-8")
        return guard_size(
            {"ok": True, "written": True, "action_taken": "overwritten",
             "backup": str(backup), **result},
            settings.max_response_chars,
        )

    @server.tool(name="leco_manifest_status", annotations=READ_ONLY)
    async def leco_manifest_status(path: str, app_id: str = "") -> dict[str, Any]:
        """Whether leco.app.yaml and the localhost profile already exist for an app path."""
        return await client.post(
            "/api/leco/yaml-status",
            json_body={"path": path.strip(), "app_id": app_id.strip()},
            authed=False,
        )

    @server.tool(name="leco_manifest_generate", annotations=WRITES)
    async def leco_manifest_generate(path: str, app_id: str) -> dict[str, Any]:
        """Write leco.app.yaml + the localhost profile into the app repo.

        Overwrites existing LEco manifests at that path — call leco_manifest_status first if
        you need to preserve hand edits. Nothing is deployed or registered by this step.
        """
        if not path.strip() or not app_id.strip():
            raise ValueError("path and app_id are both required.")
        return guard_size(
            await client.post(
                "/api/leco/generate-yaml",
                json_body={"path": path.strip(), "app_id": app_id.strip()},
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_manifest_read", annotations=READ_ONLY)
    async def leco_manifest_read(path: str, app_id: str = "") -> dict[str, Any]:
        """Read the LEco manifest and localhost profile currently on disk for an app path."""
        payload = await client.post(
            "/api/leco/detect",
            json_body={"path": path.strip(), "app_id": app_id.strip()},
            authed=False,
        )
        return guard_size(
            {
                "ok": payload.get("ok"),
                "path_field": payload.get("path_field"),
                "manifest_yaml": payload.get("existing_manifest_yaml"),
                "localhost_yaml": payload.get("existing_localhost_yaml"),
                "registration_yaml_status": payload.get("registration_yaml_status"),
            },
            settings.max_response_chars,
        )

    @server.tool(name="leco_manifest_validate", annotations=READ_ONLY)
    async def leco_manifest_validate(
        manifest_yaml: str = "",
        localhost_yaml: str = "",
        path: str = "",
    ) -> dict[str, Any]:
        """Validate manifest / profile YAML against the LEco schema before writing it.

        Pass the YAML text you intend to save. `path` (optional) lets the validator also check
        that referenced files exist relative to that app root.
        """
        return guard_size(
            await client.post(
                "/api/leco/validate-yaml",
                json_body={
                    "manifest_yaml": manifest_yaml,
                    "localhost_yaml": localhost_yaml,
                    "path": path.strip(),
                },
                authed=False,
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_manifest_save", annotations=WRITES)
    async def leco_manifest_save(
        path: str, app_id: str, manifest_yaml: str, localhost_yaml: str
    ) -> dict[str, Any]:
        """Validate and write edited manifest + localhost profile YAML to the app repo.

        Use this after leco_manifest_generate when the app needs hand-tuning — extra routes,
        different ports, a custom health URL. Invalid YAML is rejected, not written.
        """
        return guard_size(
            await client.post(
                "/api/leco/save-yaml",
                json_body={
                    "path": path.strip(),
                    "app_id": app_id.strip(),
                    "manifest_yaml": manifest_yaml,
                    "localhost_yaml": localhost_yaml,
                },
            ),
            settings.max_response_chars,
        )

    @server.tool(name="leco_manifest_urls", annotations=READ_ONLY)
    async def leco_manifest_urls(
        localhost_yaml: str, set_urls: list[dict[str, str]] | None = None
    ) -> dict[str, Any]:
        """Read or rewrite the public URL rows inside a localhost profile.

        Call with just localhost_yaml to extract the current rows. Pass set_urls (each row
        {"role": "frontend"|"api"|"admin", "label": ..., "publicUrl": "https://x.lh"}) to get
        back merged YAML — then write it with leco_manifest_save.
        """
        if set_urls is None:
            return await client.post(
                "/api/leco/extract-localhost-urls",
                json_body={"localhost_yaml": localhost_yaml},
                authed=False,
            )
        return await client.post(
            "/api/leco/merge-localhost-urls",
            json_body={"localhost_yaml": localhost_yaml, "urls": set_urls},
            authed=False,
        )

    @server.tool(name="leco_manifest_samples", annotations=READ_ONLY)
    async def leco_manifest_samples(name: str = "") -> dict[str, Any]:
        """Preset manifest + profile templates (WordPress, Node API, SPA, Workers, …).

        Call with no name to list available samples; pass a name to get that sample's YAML.
        """
        payload = await client.get("/api/leco/register-samples")
        samples = payload.get("samples") or []
        if not name:
            return {
                "count": len(samples),
                "samples": [
                    pick(s, "id", "name", "label", "description", "archetype") for s in samples
                ],
            }
        needle = name.strip().lower()
        for s in samples:
            if needle in {
                str(s.get("id") or "").lower(),
                str(s.get("name") or "").lower(),
                str(s.get("label") or "").lower(),
            }:
                return guard_size(s, settings.max_response_chars)
        raise ValueError(f"No sample named {name!r}. Call with no name to list them.")

    @server.tool(name="leco_register", annotations=WRITES)
    async def leco_register(
        path: str,
        app_id: str,
        label: str = "",
        deploy: bool = True,
        url_overrides: list[dict[str, str]] | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Register an app with the ecosystem and (by default) deploy it.

        Requires leco.app.yaml + the localhost profile to already exist at `path` — run
        leco_manifest_generate first, or use leco_onboard to do both.

        Runs `leco-devops ecosystem-register`: writes config/leco-registry.yaml, merges the
        app's Traefik routes into hosting/traefik/dynamic.yml, and with deploy=true brings
        the compose project up on lh-network. Streams live output.
        """
        if not path.strip() or not app_id.strip():
            raise ValueError("path and app_id are both required.")
        body: dict[str, Any] = {
            "path": path.strip(),
            "app_id": app_id.strip(),
            "label": label.strip(),
            "deploy_stack": bool(deploy),
        }
        if url_overrides is not None:
            body["url_overrides"] = url_overrides
        out = await deps.run_stream(
            "/api/leco/register/stream", body, ctx=ctx, progress_label=f"register {app_id}"
        )
        out["app_id"] = app_id.strip()
        out["deploy_requested"] = bool(deploy)
        return guard_size(out, settings.max_response_chars)

    @server.tool(name="leco_onboard", annotations=WRITES)
    async def leco_onboard(
        path: str,
        app_id: str = "",
        label: str = "",
        deploy: bool = True,
        regenerate_manifest: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """One-shot onboarding: detect → generate manifest → register → deploy → verify.

        The autonomous path for "take this repo and run it on LEco". Each stage is reported
        separately so a failure is attributable.

        path:   app directory from leco_browse (e.g. "wsp:MyApp")
        app_id: slug and *.lh hostname; defaults to the detected folder slug
        regenerate_manifest=False reuses manifests already on disk instead of rewriting them.

        **This tool fits a simple app: one service, one port, one hostname.** For anything
        else it will produce a manifest that registers and does not route. A complex app is
        one with any of: more than one public hostname, a container publishing several ports,
        an existing compose file of its own, or many Workers/processes in one container. For
        those, drive the steps yourself in this order:

          1. leco_app_evidence(path)                 facts: services, container names,
                                                     {published, target} port pairs, workers,
                                                     declared port tables, and `unknowns`
          2. author leco.app.yaml + leco.yaml, then leco_manifest_save
          3. leco_manifest_overlay(slug, action="write")   lh-network + port remap, with
                                                     `ports: !override` (never `!reset`)
          4. leco_compose_validate(...)              prove the merge resolves as intended
          5. leco_register(path, app_id)             registry + Traefik + deploy
          6. leco_certs_refresh()                    new hostnames must enter the certificate
          7. leco_verify(slug=app_id)                every declared origin, classified

        Two rules that have already cost a real deployment:
        * **Never invent a port.** Every port must come from evidence — a compose port pair or
          a declared port table with a named source file. If evidence does not say, ask; do not
          pick a plausible number. A wrong port produces a stack that builds, starts and serves
          nothing.
        * **Route to the container port.** A container publishing ten ports needs ten routes,
          one per `target` (container) port, not per `published` (host) port. Traefik reaches
          the container over lh-network, where the host-side publish does not exist at all.

        On success the app is registered, routed through Traefik, and reachable at its main
        URL. Check the returned url_probe: a fresh deploy can take a moment to answer.
        """
        stages: list[dict[str, Any]] = []

        async def note(stage: str, message: str) -> None:
            if ctx is not None:
                try:
                    await ctx.log("info", f"[{stage}] {message}")
                except Exception:  # noqa: BLE001
                    pass

        # 1. Detect ------------------------------------------------------------------
        await note("detect", f"scanning {path}")
        detect = await client.post(
            "/api/leco/detect",
            json_body={"path": path.strip(), "app_id": app_id.strip()},
            authed=False,
        )
        if not detect.get("ok"):
            return {
                "ok": False,
                "failed_stage": "detect",
                "error": detect.get("error") or "detect failed",
                "stages": stages,
            }
        resolved_id = app_id.strip() or str(detect.get("main_url_host_slug") or "").strip()
        if not resolved_id:
            return {
                "ok": False,
                "failed_stage": "detect",
                "error": "Could not derive an app_id from the path — pass app_id explicitly.",
                "stages": stages,
            }
        stages.append(
            {
                "stage": "detect",
                "ok": True,
                **pick(
                    detect,
                    "suggested_archetype",
                    "suggested_label",
                    "compose_files",
                    "has_wrangler",
                    "host_ports",
                    "main_url_preview",
                    "main_url_warnings",
                    "path_field",
                ),
                "app_id": resolved_id,
            }
        )

        # 2. Manifest ----------------------------------------------------------------
        status = detect.get("registration_yaml_status") or {}
        has_manifest = bool(status.get("manifest_exists") or status.get("has_manifest"))
        if regenerate_manifest or not has_manifest:
            await note("manifest", f"writing leco.app.yaml for {resolved_id}")
            gen = await client.post(
                "/api/leco/generate-yaml",
                json_body={"path": path.strip(), "app_id": resolved_id},
            )
            stages.append({"stage": "manifest", "ok": bool(gen.get("ok", True)), **pick(gen, "manifest_path", "localhost_path", "written", "error")})
            if gen.get("ok") is False:
                return {
                    "ok": False,
                    "failed_stage": "manifest",
                    "error": gen.get("error"),
                    "stages": stages,
                }
        else:
            stages.append(
                {"stage": "manifest", "ok": True, "skipped": "manifests already on disk"}
            )

        # 3. Register (+ deploy) ------------------------------------------------------
        resolved_label = label.strip() or str(detect.get("suggested_label") or "").strip()
        await note("register", f"registering {resolved_id} (deploy={deploy})")
        reg = await deps.run_stream(
            "/api/leco/register/stream",
            {
                "path": path.strip(),
                "app_id": resolved_id,
                "label": resolved_label,
                "deploy_stack": bool(deploy),
            },
            ctx=ctx,
            progress_label=f"onboard {resolved_id}",
        )
        stages.append({"stage": "register", "ok": bool(reg.get("ok")), "result": reg.get("result"),
                       "error": reg.get("error")})
        if not reg.get("ok"):
            return {
                "ok": False,
                "failed_stage": "register",
                "error": reg.get("error") or "registration failed",
                "app_id": resolved_id,
                "stages": stages,
                "log": reg.get("log"),
            }

        # 4. Verify -------------------------------------------------------------------
        verify: dict[str, Any] = {"stage": "verify", "ok": True}
        try:
            snap = await client.get(f"/api/hosted-apps/{resolved_id}/snapshot")
            manifest_ui = snap.get("manifest_ui") or {}
            verify.update(
                {
                    "runtime": snap.get("runtime"),
                    "main_url": manifest_ui.get("main_url"),
                    "routes": manifest_ui.get("routes"),
                    "url_probes": snap.get("url_probes"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - verification is best-effort
            verify.update({"ok": False, "error": str(exc)})
        stages.append(verify)

        return guard_size(
            {
                "ok": True,
                "app_id": resolved_id,
                "main_url": verify.get("main_url") or detect.get("main_url_preview"),
                "deployed": bool(deploy),
                "stages": stages,
                "log": reg.get("log"),
                "next_steps": [
                    f"leco_app_snapshot(slug='{resolved_id}') for full detail",
                    f"leco_app_logs(slug='{resolved_id}') if a URL does not answer",
                    f"leco_app_validate(slug='{resolved_id}') to check manifest/route wiring",
                ],
            },
            settings.max_response_chars,
        )

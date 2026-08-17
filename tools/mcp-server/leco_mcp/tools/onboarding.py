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

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from ..runtime import Deps
from ..shaping import guard_size, pick

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True)
WRITES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False)

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

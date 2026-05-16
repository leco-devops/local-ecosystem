import json
import os
from pathlib import Path

from flask import Flask, Response, abort, jsonify, make_response, redirect, render_template, request, stream_with_context, url_for

from control import CONTROL_TOKEN, check_control_token, list_targets, run_action, run_action_streaming
from ollama_models import build_models_payload as build_ollama_models_payload, handle_inspect as handle_ollama_inspect, handle_models_action as handle_ollama_models_action, list_manifest_backups as list_ollama_manifest_backups
from airllm_models import build_models_payload as build_airllm_models_payload, handle_inspect as handle_airllm_inspect, handle_models_action as handle_airllm_models_action, list_manifest_backups as list_airllm_manifest_backups
from docs_catalog import get_doc_catalog, get_doc_content
from monitor import (
    collect_cloudflare_local_status,
    collect_overview,
    collect_reference_status,
    collect_service_logs,
    list_managed_services,
)
from ecosystem_updates import (
    collect_update_catalog_panel,
    load_catalog_meta,
    load_ecosystem_updates,
    load_llm_catalog,
    mark_all_read,
    save_schedule,
)
from help_manual import get_help_content, get_help_tree, search_help
from popular_models import load_airllm_catalog, load_ollama_catalog
from service_hub import get_hub_detail, list_hub_slugs
from ui_login_assist import (
    apply_cookies_to_flask_response,
    build_assist_public_url,
    try_server_side_login,
)
from ui_credentials import (
    build_assist_context,
    catalog_for_ui,
    credentials_for_ui,
    get_registry_entry,
    make_launch_token,
    save_credentials,
    verify_launch_token,
)
from ui_credential_reset import apply_reset
from version_info import load_version_payload
from ai_news import fetch_all_news, filter_news, refine_query_with_llm

app = Flask(__name__, template_folder="templates", static_folder="static")

_HTML_NO_CACHE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}


def _html_response(template: str, **context):
    """Serve HTML without browser caching so template edits are visible immediately."""
    resp = make_response(render_template(template, **context))
    for k, v in _HTML_NO_CACHE.items():
        resp.headers[k] = v
    return resp


@app.get("/favicon.ico")
def favicon_legacy():
    """Browsers default to /favicon.ico; serve the same asset as static/favicon.svg."""
    return redirect(url_for("static", filename="favicon.svg"), 302)


def _dashboard_boot_dict() -> dict:
    """Client boot: whether Control token is enforced, optional same-origin prefill (opt-in)."""
    inject_ui = os.getenv("DASHBOARD_INJECT_CONTROL_TOKEN_UI", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    prefill = CONTROL_TOKEN if (inject_ui and CONTROL_TOKEN) else None
    wsp_host = (
        os.getenv("DASHBOARD_WORKSPACE_PARENT_HOST") or os.getenv("LECO_WORKSPACE_PARENT_HOST") or ""
    ).strip()
    proj_host = (
        os.getenv("DASHBOARD_PROJECT_ROOT_HOST") or os.getenv("LECO_PROJECT_ROOT_HOST") or ""
    ).strip()
    ver = load_version_payload()
    return {
        "token_required": bool(CONTROL_TOKEN),
        "prefill_control_token": prefill,
        "workspace_parent_host": wsp_host or None,
        "project_root_host": proj_host or None,
        "platform_version": ver.get("version"),
        "application": ver.get("application"),
    }


@app.get("/api/version")
def api_version():
    return jsonify(load_version_payload())


@app.get("/api/overview")
def api_overview():
    return jsonify(collect_overview())


@app.get("/api/services")
def api_services():
    return jsonify({"services": list_managed_services()})


@app.get("/api/logs")
def api_logs():
    service = request.args.get("service", "traefik")
    search = request.args.get("search", "")
    level = request.args.get("level", "all")
    tail = request.args.get("tail", "500")
    since = request.args.get("since", "1800")

    try:
        tail_value = int(tail)
    except ValueError:
        tail_value = 500
    try:
        since_value = int(since)
    except ValueError:
        since_value = 1800

    payload = collect_service_logs(
        service_container=service,
        search=search,
        level=level,
        tail=tail_value,
        since_seconds=since_value,
    )
    return jsonify(payload)


@app.get("/api/cloudflare-local")
def api_cloudflare_local():
    return jsonify(collect_cloudflare_local_status())


@app.get("/api/reference")
def api_reference():
    return jsonify(collect_reference_status())


@app.get("/api/docs/catalog")
def api_docs_catalog():
    return jsonify(get_doc_catalog())


@app.get("/api/docs/content")
def api_docs_content():
    doc_id = (request.args.get("id") or "").strip()
    payload, err = get_doc_content(doc_id)
    if err:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify({"ok": True, **payload})


@app.get("/api/metrics/history")
def api_metrics_history():
    from timeseries import append_snapshot, get_history

    append_snapshot()
    limit = request.args.get("limit", type=int)
    return jsonify(get_history(limit))


@app.get("/api/host-metrics/injected")
def api_host_metrics_injected():
    """macOS host temp file, writer_status.json, scheduler_meta.json — for Metrics tab UI."""
    from host_metrics import host_injected_metrics_api_payload

    return jsonify(host_injected_metrics_api_payload())


@app.get("/api/control/targets")
def api_control_targets():
    from service_policies import load_policies

    data = list_targets()
    policies = load_policies()
    for t in data.get("targets", []):
        t["default_policy"] = policies.get(t["id"], "start")
    return jsonify(data)


@app.get("/api/control/default-policies")
def api_control_default_policies():
    from service_policies import load_policies

    return jsonify({"ok": True, "policies": load_policies()})


@app.put("/api/control/default-policies")
def api_control_set_default_policies():
    from service_policies import VALID_POLICIES, save_policies

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    updates = data.get("policies")
    if not isinstance(updates, dict):
        return jsonify({"ok": False, "error": "policies dict required"}), 400
    clean = {k: v for k, v in updates.items() if isinstance(v, str) and v in VALID_POLICIES}
    if not clean:
        return jsonify({"ok": False, "error": "no valid policies"}), 400
    saved = save_policies(clean)
    return jsonify({"ok": True, "policies": saved})


@app.get("/api/hosted-apps")
def api_hosted_apps():
    from hosted_apps import list_hosted_apps

    return jsonify(list_hosted_apps())


@app.get("/api/hosted-apps/<slug>/snapshot")
def api_hosted_snapshot(slug: str):
    from hosted_app_timeseries import maybe_append_from_aggregate
    from hosted_apps import snapshot_for_slug

    data = snapshot_for_slug(slug)
    if data.get("ok") and data.get("aggregate"):
        maybe_append_from_aggregate(slug, data["aggregate"])
    return jsonify(data)


@app.get("/api/hosted-apps/<slug>/metrics/history")
def api_hosted_metrics_history(slug: str):
    from hosted_app_timeseries import get_history, maybe_append_from_aggregate
    from hosted_apps import build_aggregate_for_timeseries

    agg = build_aggregate_for_timeseries(slug)
    if agg:
        maybe_append_from_aggregate(slug, agg)
    limit = request.args.get("limit", type=int)
    return jsonify(get_history(slug, limit))


@app.get("/api/hosted-apps/<slug>/logs")
def api_hosted_logs(slug: str):
    from hosted_apps import logs_for_slug

    tail = request.args.get("tail", "400")
    since = request.args.get("since", "1800")
    service = (request.args.get("service") or "").strip() or None
    search = request.args.get("search", "") or ""
    try:
        tail_v = int(tail)
    except ValueError:
        tail_v = 400
    try:
        since_v = int(since)
    except ValueError:
        since_v = 1800
    return jsonify(logs_for_slug(slug, tail=tail_v, since_seconds=since_v, service=service, search=search))


@app.get("/api/hosted-apps/<slug>/logs/stream")
def api_hosted_logs_stream(slug: str):
    from hosted_apps import logs_stream_for_slug

    tail = request.args.get("tail", 200, type=int) or 200
    service = (request.args.get("service") or "").strip() or None

    @stream_with_context
    def gen():
        try:
            for line in logs_stream_for_slug(slug, tail=tail, service=service):
                yield line
        except GeneratorExit:
            raise

    return Response(
        gen(),
        mimetype="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/api/hosted-apps/<slug>/insights")
def api_hosted_insights(slug: str):
    from hosted_apps import insights_for_slug

    return jsonify(insights_for_slug(slug))


@app.get("/api/hosted-apps/<slug>/data-import/discover")
def api_hosted_data_import_discover(slug: str):
    from hosted_data_import import data_import_summary_for_slug
    from leco_control import leco_meta_for_slug

    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return jsonify({"ok": False, "error": "unknown or invalid app slug"}), 404
    summary = data_import_summary_for_slug(
        slug.strip(),
        manifest_path=meta["manifest_path"],
        compose_tail=meta.get("compose_tail"),
    )
    return jsonify({"ok": True, "slug": slug.strip(), **summary})


@app.post("/api/hosted-apps/<slug>/data-import/stream")
def api_hosted_data_import_stream(slug: str):
    from control import check_control_token
    from hosted_data_import import iterate_data_import_stream
    from leco_control import leco_meta_for_slug

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return jsonify({"ok": False, "error": "unknown or invalid app slug"}), 404

    reimport = bool(data.get("reimport", True))
    dry_run = bool(data.get("dry_run", False))
    raw_sel = data.get("selected_ids")
    selected_ids: list[str] | None = None
    if raw_sel is not None:
        if isinstance(raw_sel, list):
            selected_ids = [str(x).strip() for x in raw_sel if str(x).strip()]
        else:
            selected_ids = []
    from hosted_apps import compose_ps_result

    rows, _code = compose_ps_result(meta)

    @stream_with_context
    def ndjson():
        try:
            for ev in iterate_data_import_stream(
                slug.strip(),
                manifest_path=meta["manifest_path"],
                compose_tail=meta.get("compose_tail"),
                compose_root=meta.get("root") or str(Path(meta["manifest_path"]).parent),
                compose_ps=rows,
                reimport=reimport,
                dry_run=dry_run,
                selected_ids=selected_ids,
            ):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except GeneratorExit:
            raise
        except Exception as exc:
            yield json.dumps(
                {"type": "done", "result": {"ok": False, "error": str(exc)}},
                ensure_ascii=False,
            ) + "\n"

    return Response(
        ndjson(),
        mimetype="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/hosted-apps/<slug>/validate-configuration")
def api_hosted_validate_configuration(slug: str):
    """Schema + on-disk path checks for manifest + profile (no control token)."""
    from leco_control import leco_meta_for_slug
    from leco_validate import validate_configuration_on_disk

    meta = leco_meta_for_slug(slug.strip())
    if not meta:
        return jsonify({"ok": False, "error": "unknown or invalid app slug"}), 404
    mp = meta.get("manifest_path")
    if not mp:
        return jsonify({"ok": False, "error": "no manifest path for this app"}), 400
    payload = validate_configuration_on_disk(str(mp))
    if payload.get("ok") is False:
        return jsonify(payload), 400
    return jsonify(payload)


@app.get("/api/traefik/routes")
def api_traefik_routes():
    from hosted_offboard import traefik_routes_with_hosted_hints

    return jsonify(traefik_routes_with_hosted_hints())


@app.get("/api/leco/browse")
def api_leco_browse():
    """List subdirectories under project or workspace-parent (safe path)."""
    from leco_detect import browse_leco_directories

    root_kind = (request.args.get("root") or "project").strip().lower()
    if root_kind not in ("project", "wsp"):
        return jsonify({"ok": False, "error": "root must be project or wsp"}), 400
    sub = (request.args.get("path") or "").strip()
    return jsonify(browse_leco_directories(root_kind, sub))


@app.get("/api/leco/register-samples")
def api_leco_register_samples():
    """Preset manifest + sidecar profile YAML for the registration wizard."""
    from leco_detect import register_yaml_samples

    return jsonify({"ok": True, "samples": register_yaml_samples()})


@app.post("/api/leco/detect")
def api_leco_detect():
    """Scan an allowed app directory (compose / wrangler / archetype hints)."""
    from leco_detect import (
        host_slug_from_app_id,
        main_urls_from_app_id,
        main_url_from_app_id,
        preview_registration_yaml,
        read_existing_registration_yaml,
        registration_scan_root,
        registration_path_field_for_ui,
        require_registration_app_id,
        resolve_registration_path,
        scan_app_directory,
        slugify_app_id,
    )
    from leco_materialize import registration_yaml_status

    data = request.get_json(silent=True) or {}
    p = (data.get("path") or "").strip()
    if not p:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        root = resolve_registration_path(p)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    scan_root = registration_scan_root(root)
    out = dict(scan_app_directory(scan_root))
    out["path_field"] = registration_path_field_for_ui(root)
    out["scan_root_path_field"] = registration_path_field_for_ui(scan_root)
    em, el = read_existing_registration_yaml(root)
    out["existing_manifest_yaml"] = em
    out["existing_localhost_yaml"] = el
    preview_raw = (data.get("app_id") or "").strip()
    if preview_raw and (preview_raw in (".", "..") or set(preview_raw) <= {"."}):
        return jsonify(
            {"ok": False, "error": "app_id cannot be '.' or '..' — use a slug such as my-app or 1note."}
        ), 400
    if preview_raw:
        try:
            preview_id = require_registration_app_id(preview_raw)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
    else:
        preview_id = slugify_app_id(root.name)
    try:
        host_slug = host_slug_from_app_id(preview_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    preview_urls = main_urls_from_app_id(preview_id)
    out["main_url_preview"] = main_url_from_app_id(preview_id)
    out["main_url_preview_https"] = preview_urls["https"]
    out["main_url_preview_http"] = preview_urls["http"]
    out["main_url_host_slug"] = host_slug
    warnings: list[str] = []
    if not out.get("compose_files") and not out.get("has_wrangler"):
        warnings.append(
            "No compose or wrangler signals detected. Main URL preview is derived from app id but may not route until you configure infrastructure."
        )
    out["main_url_warnings"] = warnings
    my, ly = preview_registration_yaml(scan_root, preview_id)
    out["manifest_yaml_preview"] = my
    out["localhost_yaml_preview"] = ly
    out["registration_yaml_status"] = registration_yaml_status(p, preview_id)
    manifest_on_disk = root / "leco.app.yaml"
    if manifest_on_disk.is_file():
        try:
            from hosted_data_import import build_import_plan

            seed = build_import_plan(manifest_on_disk.resolve())
            out["seed_data"] = {
                "present": bool(seed.get("present")),
                "item_count": len(seed.get("items") or []),
                "warnings": list(seed.get("warnings") or [])[:5],
            }
        except Exception:
            pass
    return jsonify({"ok": True, **out})


@app.post("/api/leco/validate-yaml")
def api_leco_validate_yaml():
    """Parse and validate wizard YAML against LEco ApplicationManifest / LocalhostProfile (no token)."""
    from leco_detect import registration_scan_root, resolve_registration_path
    from leco_validate import validate_registration_yaml

    data = request.get_json(silent=True) or {}
    my = data.get("manifest_yaml")
    ly = data.get("localhost_yaml")
    if not isinstance(my, str):
        my = None
    if not isinstance(ly, str):
        ly = None
    scan_root = None
    path_rel = (data.get("path") or "").strip()
    if path_rel:
        try:
            scan_root = registration_scan_root(resolve_registration_path(path_rel))
        except ValueError:
            scan_root = None
    payload = validate_registration_yaml(my, ly, scan_root=scan_root)
    return jsonify({"ok": True, **payload})


@app.post("/api/leco/extract-localhost-urls")
def api_leco_extract_localhost_urls():
    """Parse ``urls`` from localhost profile YAML text (registration wizard)."""
    from leco_localhost_urls import extract_localhost_urls

    data = request.get_json(silent=True) or {}
    ly = data.get("localhost_yaml")
    if not isinstance(ly, str):
        return jsonify({"ok": False, "error": "localhost_yaml string required"}), 400
    urls = extract_localhost_urls(ly)
    return jsonify({"ok": True, "urls": urls})


@app.post("/api/leco/merge-localhost-urls")
def api_leco_merge_localhost_urls():
    """Merge URL rows into localhost profile YAML text (registration wizard)."""
    from leco_localhost_urls import merge_localhost_urls

    data = request.get_json(silent=True) or {}
    ly = data.get("localhost_yaml")
    urls = data.get("urls")
    if not isinstance(ly, str):
        return jsonify({"ok": False, "error": "localhost_yaml string required"}), 400
    if not isinstance(urls, list):
        return jsonify({"ok": False, "error": "urls array required"}), 400
    try:
        merged = merge_localhost_urls(ly, urls)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "localhost_yaml": merged})


@app.post("/api/leco/yaml-status")
def api_leco_yaml_status():
    """Whether ``leco.app.yaml`` + localhost profile exist (no token)."""
    from leco_detect import resolve_registration_path
    from leco_materialize import registration_yaml_status

    data = request.get_json(silent=True) or {}
    p = (data.get("path") or "").strip()
    app_id = (data.get("app_id") or data.get("id") or "").strip()
    if not p:
        return jsonify({"ok": False, "error": "path required"}), 400
    try:
        resolve_registration_path(p)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    st = registration_yaml_status(p, app_id or None)
    return jsonify({"ok": True, **st})


@app.post("/api/leco/generate-yaml")
def api_leco_generate_yaml():
    """Scan app root and write ``leco.app.yaml`` + localhost profile (control token)."""
    from leco_materialize import materialize_registration_yaml

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    path_rel = (data.get("path") or "").strip()
    app_id = (data.get("app_id") or data.get("id") or "").strip()
    if not path_rel or not app_id:
        return jsonify({"ok": False, "error": "path and app_id required"}), 400
    try:
        result = materialize_registration_yaml(path_rel, app_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/leco/save-yaml")
def api_leco_save_yaml():
    """Validate and write manifest + localhost YAML from the editor (control token)."""
    from leco_materialize import save_registration_yaml

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    path_rel = (data.get("path") or "").strip()
    app_id = (data.get("app_id") or data.get("id") or "").strip()
    my = data.get("manifest_yaml")
    ly = data.get("localhost_yaml")
    if not isinstance(my, str) or not isinstance(ly, str):
        return jsonify({"ok": False, "error": "manifest_yaml and localhost_yaml strings required"}), 400
    if not path_rel or not app_id:
        return jsonify({"ok": False, "error": "path and app_id required"}), 400
    try:
        result = save_registration_yaml(path_rel, app_id, my, ly)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **result})


@app.post("/api/leco/register")
def api_leco_register():
    """Run ``leco-devops ecosystem-register`` using YAML already on disk (control token)."""
    from leco_registration import register_app_wizard

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    path_rel = (data.get("path") or "").strip()
    app_id = (data.get("app_id") or data.get("id") or "").strip()
    label = (data.get("label") or "").strip()
    url_overrides = data.get("url_overrides")
    if not path_rel or not app_id:
        return jsonify({"ok": False, "error": "path and app_id required"}), 400
    deploy_stack = bool(data.get("deploy_stack"))
    try:
        result = register_app_wizard(
            path_rel,
            app_id,
            label,
            url_overrides=url_overrides if isinstance(url_overrides, list) else None,
            deploy_stack=deploy_stack,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)


@app.post("/api/leco/register/stream")
def api_leco_register_stream():
    """NDJSON stream: log lines from register + ecosystem-register, then `{type:done,result:{...}}`."""
    from leco_registration import iterate_register_app_wizard

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    path_rel = (data.get("path") or "").strip()
    app_id = (data.get("app_id") or data.get("id") or "").strip()
    label = (data.get("label") or "").strip()
    url_overrides = data.get("url_overrides")
    if not path_rel or not app_id:
        return jsonify({"ok": False, "error": "path and app_id required"}), 400
    deploy_stack = bool(data.get("deploy_stack"))

    @stream_with_context
    def ndjson():
        try:
            for ev in iterate_register_app_wizard(
                path_rel,
                app_id,
                label,
                url_overrides=url_overrides if isinstance(url_overrides, list) else None,
                deploy_stack=deploy_stack,
            ):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except GeneratorExit:
            raise
        except Exception as exc:
            yield json.dumps(
                {"type": "done", "result": {"ok": False, "error": str(exc)}},
                ensure_ascii=False,
            ) + "\n"

    return Response(
        ndjson(),
        mimetype="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/hosted/upload-zip")
def api_hosted_upload_zip():
    """Extract zip into hosting/app-available/<slug>/; delete archive after extract (control token)."""
    from pathlib import Path

    from hosted_zip_upload import host_zip_upload
    from leco_subprocess import PROJECT_ROOT

    form = request.form.to_dict(flat=True)
    if not check_control_token(request, form):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    app_id = (form.get("app_id") or form.get("slug") or "").strip()
    upload = request.files.get("file")
    try:
        result = host_zip_upload(Path(PROJECT_ROOT).resolve(), app_id, upload)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify(result)


@app.post("/api/hosted-apps/<slug>/offboard")
def api_hosted_offboard(slug: str):
    from hosted_offboard import offboard_hosted_app

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    strip_t = data.get("strip_traefik", True)
    clean_cf = data.get("clean_local_cf", True)
    if isinstance(strip_t, str):
        strip_t = strip_t.lower() in ("1", "true", "yes")
    if isinstance(clean_cf, str):
        clean_cf = clean_cf.lower() in ("1", "true", "yes")
    return jsonify(offboard_hosted_app(slug, strip_traefik=bool(strip_t), clean_local_cf=bool(clean_cf)))


@app.post("/api/traefik/merge-fragment")
def api_traefik_merge_fragment():
    from traefik_dynamic_file import merge_http_fragment

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    frag = (data.get("yaml") or data.get("fragment") or "").strip()
    if not frag:
        return jsonify({"ok": False, "error": "yaml / fragment required"}), 400
    ok, msg = merge_http_fragment(frag)
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.post("/api/traefik/fragment-from-manifest")
def api_traefik_fragment_from_manifest():
    """Run LEco DevOps traefik-fragment for a registry app manifest (stdout YAML)."""
    from pathlib import Path

    from leco_control import load_leco_registry_entries, resolve_manifest_path
    from leco_subprocess import run_traefik_fragment

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    slug = (data.get("slug") or "").strip()
    if not slug:
        return jsonify({"ok": False, "error": "slug required"}), 400
    manifest_rel = None
    for entry in load_leco_registry_entries():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id") or "").strip() != slug:
            continue
        manifest_rel = (entry.get("manifest") or "").strip()
        break
    if not manifest_rel:
        return jsonify({"ok": False, "error": f"no registry entry for {slug!r}"}), 404
    abs_m = resolve_manifest_path(manifest_rel)
    if not abs_m or not Path(abs_m).is_file():
        return jsonify({"ok": False, "error": "manifest not found"}), 400
    code, stdout, combined = run_traefik_fragment(Path(abs_m))
    if code != 0:
        return (
            jsonify({"ok": False, "error": combined[-4000:] if combined else f"exit {code}"}),
            400,
        )
    return jsonify({"ok": True, "yaml": stdout, "manifest": manifest_rel})


@app.post("/api/traefik/strip-keys")
def api_traefik_strip_keys():
    from traefik_dynamic_file import strip_router_service_keys

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    routers = data.get("routers") or []
    services = data.get("services") or []
    if not isinstance(routers, list):
        routers = []
    if not isinstance(services, list):
        services = []
    rks = [str(x) for x in routers if x]
    sks = [str(x) for x in services if x]
    nr, ns, err = strip_router_service_keys(rks, sks)
    return jsonify(
        {
            "ok": err is None,
            "routers_removed": nr,
            "services_removed": ns,
            "error": err,
        }
    )


# ---------------------------------------------------------------------------
# AI-assisted onboarding endpoints
# ---------------------------------------------------------------------------


@app.get("/api/ai/settings")
def api_ai_settings():
    """Return AI provider config safe for browser display (keys masked)."""
    from ai_config import config_for_ui

    return jsonify({"ok": True, **config_for_ui()})


@app.post("/api/ai/settings")
def api_ai_settings_update():
    """Update AI provider settings (control token required)."""
    from ai_config import update_from_ui

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        safe = update_from_ui(data)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **safe})


@app.post("/api/ai/test")
def api_ai_test():
    """Test connectivity to the configured AI provider."""
    from ai_config import get_provider_config
    from ai_provider import create_provider

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    provider_name = (data.get("provider") or "").strip()
    cfg = get_provider_config()
    if provider_name:
        cfg["provider"] = provider_name
    provider = create_provider(cfg)
    if provider is None:
        return jsonify({"ok": False, "error": f"Provider '{cfg.get('provider', 'none')}' not configured or missing API key"})
    status = provider.health_check()
    return jsonify({
        "ok": status.ok,
        "provider": status.provider,
        "message": status.message,
        "models": [{"name": m.name, "context_window": m.context_window} for m in status.models],
    })


@app.get("/api/ai/models")
def api_ai_models():
    """List models available on the configured AI provider."""
    from ai_config import get_provider_config
    from ai_provider import create_provider

    cfg = get_provider_config()
    provider = create_provider(cfg)
    if provider is None:
        return jsonify({"ok": False, "models": [], "error": "No provider configured"})
    try:
        models = provider.list_models()
    except Exception as exc:
        return jsonify({"ok": False, "models": [], "error": str(exc)})
    return jsonify({
        "ok": True,
        "provider": cfg.get("provider", "none"),
        "models": [{"name": m.name, "context_window": m.context_window, "description": m.description} for m in models],
    })


@app.post("/api/leco/ai-analyze/stream")
def api_leco_ai_analyze_stream():
    """NDJSON stream: AI-assisted onboarding pipeline (collect → analyze → generate).

    Request body:
        path        — app directory (same as registration wizard)
        slug        — app slug for container/hostname naming
        source_path — relative path within hosting dir (default ".")
        health_path — optional health check endpoint
        provider    — optional provider override
        model       — optional model override
    """
    from ai_orchestrator import stream_onboarding
    from leco_detect import resolve_registration_path

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    path_rel = (data.get("path") or "").strip()
    slug = (data.get("slug") or data.get("app_id") or "").strip()
    source_path = (data.get("source_path") or ".").strip()
    health_path = (data.get("health_path") or "").strip() or None
    provider_override = (data.get("provider") or "").strip() or None
    model_override = (data.get("model") or "").strip() or None

    if not path_rel or not slug:
        return jsonify({"ok": False, "error": "path and slug required"}), 400

    try:
        app_root = str(resolve_registration_path(path_rel))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    @stream_with_context
    def ndjson():
        try:
            for ev in stream_onboarding(
                app_root,
                slug,
                source_path,
                health_path=health_path,
                provider_override=provider_override,
                model_override=model_override,
            ):
                yield json.dumps(ev.to_dict(), ensure_ascii=False) + "\n"
        except GeneratorExit:
            raise
        except Exception as exc:
            yield json.dumps(
                {"type": "error", "text": str(exc)}, ensure_ascii=False,
            ) + "\n"
            yield json.dumps(
                {"type": "done", "data": {"ok": False, "error": str(exc)}},
                ensure_ascii=False,
            ) + "\n"

    return Response(
        ndjson(),
        mimetype="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/api/leco/ai-analyze/write")
def api_leco_ai_analyze_write():
    """Write previously generated AI config files to hosting/app-available/{slug}/.

    Follows the same layout as the registration flow: generated configs
    live in the ecosystem's hosting/app-available/<slug>/ directory, not in
    the (possibly read-only) app source tree.  If the app source dir is
    writable (project-local app), files go there instead.
    """
    from ai_orchestrator import write_generated_files
    from hosting_layout import hosting_staging_dir, is_dir_writable
    from leco_detect import resolve_registration_path

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    path_rel = (data.get("path") or "").strip()
    slug = (data.get("slug") or data.get("app_id") or "").strip()
    files = data.get("files")
    dry_run = bool(data.get("dry_run", False))

    if not path_rel:
        return jsonify({"ok": False, "error": "path required"}), 400
    if not slug:
        return jsonify({"ok": False, "error": "slug/app_id required"}), 400
    if not isinstance(files, dict) or not files:
        return jsonify({"ok": False, "error": "files dict required"}), 400

    try:
        app_root = resolve_registration_path(path_rel)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    # Use the hosting staging directory (same as registration flow).
    # Fall back to app root only if it is writable (project-local app).
    eco_root = Path(os.getenv("DASHBOARD_PROJECT_ROOT", "/project"))
    if is_dir_writable(app_root):
        target = str(app_root)
    else:
        staging = hosting_staging_dir(eco_root, slug)
        staging.mkdir(parents=True, exist_ok=True)
        target = str(staging)

    try:
        written = write_generated_files(files, target, dry_run=dry_run)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    return jsonify({"ok": True, "written": written, "target": target, "dry_run": dry_run})


# ---------------------------------------------------------------------------
# Ollama model management endpoints
# ---------------------------------------------------------------------------


# Ollama API routes
@app.get("/api/ollama/models")
def api_ollama_models():
    return jsonify(build_ollama_models_payload())


@app.post("/api/ollama/models/action")
def api_ollama_models_action():
    data = request.get_json(silent=True) or {}
    body, status = handle_ollama_models_action(request, data)
    return jsonify(body), status


@app.get("/api/ollama/model/inspect")
def api_ollama_model_inspect():
    """Full /api/show payload for one model (modelfile, params, template). Requires control token header if set."""
    body, status = handle_ollama_inspect(request)
    return jsonify(body), status


@app.get("/api/ollama/backups")
def api_ollama_backups_list():
    if not check_control_token(request, None):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify(list_ollama_manifest_backups())


# AirLLM API routes (mirrors Ollama routes)
@app.get("/api/airllm/models")
def api_airllm_models():
    return jsonify(build_airllm_models_payload())


@app.post("/api/airllm/models/action")
def api_airllm_models_action():
    data = request.get_json(silent=True) or {}
    body, status = handle_airllm_models_action(request, data)
    return jsonify(body), status


@app.get("/api/airllm/model/inspect")
def api_airllm_model_inspect():
    """Full /api/show payload for one AirLLM model. Requires control token header if set."""
    body, status = handle_airllm_inspect(request)
    return jsonify(body), status


@app.get("/api/airllm/backups")
def api_airllm_backups_list():
    if not check_control_token(request, None):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify(list_airllm_manifest_backups())


# Curated "Popular models" catalogs (JSON files in ecosystem-stack/config/).
# Public reads: no Control token required — purely informational dropdown data.
@app.get("/api/ollama/popular")
def api_ollama_popular():
    return jsonify(load_ollama_catalog())


@app.get("/api/airllm/popular")
def api_airllm_popular():
    return jsonify(load_airllm_catalog())


@app.get("/api/help/tree")
def api_help_tree():
    return jsonify(get_help_tree())


@app.get("/api/help/content")
def api_help_content():
    node_id = (request.args.get("id") or "").strip()
    payload, err = get_help_content(node_id)
    if err:
        return jsonify({"ok": False, "error": err}), 404
    return jsonify(payload)


@app.get("/api/help/search")
def api_help_search():
    q = (request.args.get("q") or "").strip()
    return jsonify(search_help(q))


@app.get("/api/ecosystem/updates")
def api_ecosystem_updates():
    return jsonify(load_ecosystem_updates())


@app.get("/api/llm-catalog/ollama")
def api_llm_catalog_ollama():
    return jsonify(load_llm_catalog("ollama"))


@app.get("/api/llm-catalog/airllm")
def api_llm_catalog_airllm():
    return jsonify(load_llm_catalog("airllm"))


@app.get("/api/ecosystem/catalog-meta")
def api_catalog_meta():
    return jsonify(load_catalog_meta())


@app.get("/api/update-catalog/panel")
def api_update_catalog_panel():
    return jsonify(collect_update_catalog_panel())


@app.post("/api/update-catalog/schedule")
def api_update_catalog_schedule():
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    mode = str(data.get("mode") or "interval").strip().lower()
    interval = data.get("interval_hours", 6)
    times = data.get("fixed_times_utc") or []
    if not isinstance(times, list):
        times = [x.strip() for x in str(times).split(",") if x.strip()]
    try:
        return jsonify(save_schedule(mode, float(interval), times))
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/update-catalog/mark-read")
def api_update_catalog_mark_read():
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify(mark_all_read())


@app.get("/api/ai-news")
def api_ai_news():
    force = request.args.get("refresh", "").strip().lower() in ("1", "true", "yes")
    category = (request.args.get("category") or "").strip() or None
    tags_raw = (request.args.get("tags") or "").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else None
    q = (request.args.get("q") or "").strip() or None
    payload = fetch_all_news(force=force)
    items = filter_news(payload, category=category, tags=tags, q=q)
    return jsonify({**payload, "filtered_count": len(items), "filtered_items": items})


@app.post("/api/ai-news/refine")
def api_ai_news_refine():
    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or data.get("interest") or "").strip()
    if not query:
        return jsonify({"ok": False, "error": "query is required"}), 400
    return jsonify(refine_query_with_llm(query))


@app.post("/api/control")
def api_control():
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    target_id = (data.get("target_id") or "").strip()
    action = (data.get("action") or "").strip()
    if not target_id or not action:
        return jsonify({"ok": False, "error": "target_id and action are required"}), 400
    return jsonify(run_action(target_id, action))


@app.post("/api/control/stream")
def api_control_stream():
    """NDJSON stream: `{type:log,text}` lines then final `{type:done,result:{...}}`."""
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    target_id = (data.get("target_id") or "").strip()
    action = (data.get("action") or "").strip()
    if not target_id or not action:
        return jsonify({"ok": False, "error": "target_id and action are required"}), 400

    @stream_with_context
    def ndjson():
        try:
            for ev in run_action_streaming(target_id, action):
                yield json.dumps(ev, ensure_ascii=False) + "\n"
        except GeneratorExit:
            raise
        except Exception as exc:
            yield json.dumps(
                {"type": "done", "result": {"ok": False, "error": str(exc)}},
                ensure_ascii=False,
            ) + "\n"

    return Response(
        ndjson(),
        # text/plain often streams through proxies more reliably than application/* NDJSON.
        mimetype="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-transform",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/")
def home():
    return _html_response("index.html", dashboard_boot=_dashboard_boot_dict())


@app.get("/help")
def help_center():
    """Standalone Help & User Manual (tree nav + search + markdown topics)."""
    return _html_response("help.html", dashboard_boot=_dashboard_boot_dict())


@app.get("/hub")
def hub_index():
    return _html_response("hub_index.html", hubs=list_hub_slugs(), dashboard_boot=_dashboard_boot_dict())


@app.get("/hub/<slug>")
def hub_detail(slug: str):
    hub = get_hub_detail(slug)
    if not hub:
        abort(404)
    ui_entry = get_registry_entry(slug)
    return _html_response(
        "service_hub.html",
        hub=hub,
        ui_access=ui_entry,
        dashboard_boot=_dashboard_boot_dict(),
    )


@app.get("/api/ui-credentials/catalog")
def api_ui_credentials_catalog():
    return jsonify(catalog_for_ui())


@app.get("/api/ui-credentials/<slug>")
def api_ui_credentials_get(slug: str):
    entry = get_registry_entry(slug)
    if not entry:
        return jsonify({"ok": False, "error": "unknown service"}), 404
    from ui_credential_reset import _container_running

    container = str(entry.get("container") or "").strip()
    return jsonify(
        {
            "ok": True,
            "slug": slug,
            "label": entry.get("label"),
            "login_url": entry.get("login_url"),
            "auth_type": entry.get("auth_type"),
            "can_auto_login": entry.get("auth_type") in ("form_post", "json_post"),
            "can_reset": (entry.get("reset_handler") or "none") != "none",
            "container": container or None,
            "container_running": _container_running(container) if container else None,
            "credentials": credentials_for_ui(slug),
        }
    )


@app.put("/api/ui-credentials/<slug>")
def api_ui_credentials_put(slug: str):
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not get_registry_entry(slug):
        return jsonify({"ok": False, "error": "unknown service"}), 404
    values = data.get("values") or data.get("credentials") or data
    if not isinstance(values, dict):
        return jsonify({"ok": False, "error": "values object required"}), 400
    try:
        saved = save_credentials(slug, values)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "slug": slug, "credentials": saved})


@app.post("/api/ui-credentials/<slug>/reset")
def api_ui_credentials_reset(slug: str):
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not get_registry_entry(slug):
        return jsonify({"ok": False, "error": "unknown service"}), 404
    result = apply_reset(slug)
    status = 200 if result.get("ok") else 500
    return jsonify({"ok": result.get("ok"), **result}), status


@app.post("/api/ui-credentials/<slug>/launch-token")
def api_ui_credentials_launch_token(slug: str):
    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    try:
        token = make_launch_token(slug)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    entry = get_registry_entry(slug) or {}
    return jsonify(
        {
            "ok": True,
            "slug": slug,
            "token": token,
            "assist_url": build_assist_public_url(slug, token),
            "login_url": entry.get("login_url") or "",
            "expires_in_sec": 60,
        }
    )


@app.get("/assist/login/<slug>")
def assist_login(slug: str):
    token = (request.args.get("token") or "").strip()
    if not verify_launch_token(slug, token):
        abort(403)
    entry = get_registry_entry(slug)
    if not entry:
        abort(404)
    if (entry.get("auth_type") or "") == "json_post":
        result = try_server_side_login(slug)
        if result.ok and result.mode == "cookie" and result.cookies:
            resp = redirect(str(entry.get("login_url") or "/"))
            apply_cookies_to_flask_response(
                resp, result.cookies, login_url=str(entry.get("login_url") or "")
            )
            return resp
        if result.ok and result.mode == "local_storage" and result.token:
            return _html_response(
                "login_assist_token.html",
                assist={
                    "label": entry.get("label") or slug,
                    "login_url": entry.get("login_url") or "/",
                    "token": result.token,
                    "storage_key": result.storage_key,
                },
            )
        detail = result.error or "Login API rejected credentials or service unreachable."
        if "401" in detail or "Wrong username" in detail or "incorrect" in detail.lower():
            detail += (
                " Run Reset & apply on Service hubs → UI access (default password Localdev1), "
                "then Auto-login again."
            )
        if "502" in detail or "Bad Gateway" in detail:
            detail += " Run: ./ecosystem-stack/services/traefik.sh heal && restart dashboard."
        if "network error" in detail.lower():
            detail += " For MinIO: use Reset & apply to recreate the minio container."
        return _html_response(
            "login_assist_error.html",
            assist={
                "label": entry.get("label") or slug,
                "login_url": entry.get("login_url") or "/",
                "summary": "Server-side login did not succeed.",
                "detail": detail,
                "control_url": "/?tab=controlTab",
            },
        )
    ctx = build_assist_context(slug)
    if not ctx:
        abort(404)
    return _html_response("login_assist.html", assist=ctx)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8090")))

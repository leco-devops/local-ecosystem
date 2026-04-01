import json
import os

from flask import Flask, Response, abort, jsonify, render_template, request, stream_with_context

from control import CONTROL_TOKEN, check_control_token, list_targets, run_action, run_action_streaming
from ollama_models import build_models_payload, handle_inspect, handle_models_action, list_manifest_backups
from docs_catalog import get_doc_catalog, get_doc_content
from monitor import (
    collect_cloudflare_local_status,
    collect_overview,
    collect_reference_status,
    collect_service_logs,
    list_managed_services,
)
from service_hub import get_hub_detail, list_hub_slugs

app = Flask(__name__, template_folder="templates", static_folder="static")


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
    return {
        "token_required": bool(CONTROL_TOKEN),
        "prefill_control_token": prefill,
        "workspace_parent_host": wsp_host or None,
        "project_root_host": proj_host or None,
    }


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
    return jsonify(list_targets())


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
        preview_registration_yaml,
        read_existing_registration_yaml,
        registration_path_field_for_ui,
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
    out = dict(scan_app_directory(root))
    out["path_field"] = registration_path_field_for_ui(root)
    em, el = read_existing_registration_yaml(root)
    out["existing_manifest_yaml"] = em
    out["existing_localhost_yaml"] = el
    preview_id = (data.get("app_id") or "").strip() or root.name
    preview_id = slugify_app_id(preview_id)
    my, ly = preview_registration_yaml(root, preview_id)
    out["manifest_yaml_preview"] = my
    out["localhost_yaml_preview"] = ly
    out["registration_yaml_status"] = registration_yaml_status(p, preview_id)
    return jsonify({"ok": True, **out})


@app.post("/api/leco/validate-yaml")
def api_leco_validate_yaml():
    """Parse and validate wizard YAML against LEco ApplicationManifest / LocalhostProfile (no token)."""
    from leco_validate import validate_registration_yaml

    data = request.get_json(silent=True) or {}
    my = data.get("manifest_yaml")
    ly = data.get("localhost_yaml")
    if not isinstance(my, str):
        my = None
    if not isinstance(ly, str):
        ly = None
    payload = validate_registration_yaml(my, ly)
    return jsonify({"ok": True, **payload})


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
    """Run ``leco-app ecosystem-register`` using YAML already on disk (control token)."""
    from leco_registration import register_app_wizard

    data = request.get_json(silent=True) or {}
    if not check_control_token(request, data):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    path_rel = (data.get("path") or "").strip()
    app_id = (data.get("app_id") or data.get("id") or "").strip()
    label = (data.get("label") or "").strip()
    if not path_rel or not app_id:
        return jsonify({"ok": False, "error": "path and app_id required"}), 400
    deploy_stack = bool(data.get("deploy_stack"))
    try:
        result = register_app_wizard(
            path_rel,
            app_id,
            label,
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


@app.get("/api/ollama/models")
def api_ollama_models():
    return jsonify(build_models_payload())


@app.post("/api/ollama/models/action")
def api_ollama_models_action():
    data = request.get_json(silent=True) or {}
    body, status = handle_models_action(request, data)
    return jsonify(body), status


@app.get("/api/ollama/model/inspect")
def api_ollama_model_inspect():
    """Full /api/show payload for one model (modelfile, params, template). Requires control token header if set."""
    body, status = handle_inspect(request)
    return jsonify(body), status


@app.get("/api/ollama/backups")
def api_ollama_backups_list():
    if not check_control_token(request, None):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify(list_manifest_backups())


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
    return render_template("index.html", dashboard_boot=_dashboard_boot_dict())


@app.get("/hub")
def hub_index():
    return render_template("hub_index.html", hubs=list_hub_slugs())


@app.get("/hub/<slug>")
def hub_detail(slug: str):
    hub = get_hub_detail(slug)
    if not hub:
        abort(404)
    return render_template("service_hub.html", hub=hub)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8090")))

import json
import os

from flask import Flask, Response, abort, jsonify, render_template, request, stream_with_context

from control import check_control_token, list_targets, run_action, run_action_streaming
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
    return render_template("index.html")


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

import json
import os

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

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
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/")
def home():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8090")))

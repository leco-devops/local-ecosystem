import os

from flask import Flask, jsonify, render_template, request

from monitor import collect_overview, collect_service_logs, list_managed_services

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


@app.get("/")
def home():
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("DASHBOARD_PORT", "8090")))

import os
import threading
import time
from datetime import datetime, timezone

import docker
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

TARGET_GROUP = os.getenv("AUTOSCALE_TARGET_GROUP", "autoscale-demo")
MIN_REPLICAS = int(os.getenv("AUTOSCALE_MIN_REPLICAS", "1"))
MAX_REPLICAS = int(os.getenv("AUTOSCALE_MAX_REPLICAS", "5"))
UP_CPU_PCT = float(os.getenv("AUTOSCALE_UP_CPU_PCT", "60"))
DOWN_CPU_PCT = float(os.getenv("AUTOSCALE_DOWN_CPU_PCT", "20"))
COOLDOWN_SEC = int(os.getenv("AUTOSCALE_COOLDOWN_SEC", "60"))
CHECK_INTERVAL_SEC = int(os.getenv("AUTOSCALE_CHECK_INTERVAL_SEC", "20"))

docker_client = docker.from_env()
autoscale_events = []
last_scale_ts = 0.0


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def add_event(action, detail):
    autoscale_events.append({"time": utc_now(), "action": action, "detail": detail})
    if len(autoscale_events) > 200:
        del autoscale_events[0]


def group_containers():
    return docker_client.containers.list(
        all=True,
        filters={"label": [f"autoscale.group={TARGET_GROUP}"]},
    )


def running_group_containers():
    return [c for c in group_containers() if c.status == "running"]


def cpu_percent(container):
    try:
        stats = container.stats(stream=False)
    except Exception:
        return 0.0
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})
    cpu_delta = (
        cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
        - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
    )
    system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
    online_cpus = cpu_stats.get("online_cpus") or len(cpu_stats.get("cpu_usage", {}).get("percpu_usage") or [1])
    if system_delta <= 0:
        return 0.0
    return max(0.0, (cpu_delta / system_delta) * online_cpus * 100)


def average_cpu(containers):
    if not containers:
        return 0.0
    values = [cpu_percent(c) for c in containers]
    return sum(values) / len(values)


def next_replica_name(existing):
    nums = []
    for c in existing:
        if c.name.startswith(f"{TARGET_GROUP}-r"):
            try:
                nums.append(int(c.name.split("-r")[-1]))
            except ValueError:
                continue
    next_num = max(nums) + 1 if nums else 1
    return f"{TARGET_GROUP}-r{next_num}"


def scale_up(template, existing):
    target_networks = list((template.attrs.get("NetworkSettings", {}).get("Networks") or {}).keys())
    network_name = target_networks[0] if target_networks else None
    name = next_replica_name(existing)

    env_list = template.attrs.get("Config", {}).get("Env") or []
    env_dict = {}
    for item in env_list:
        if "=" in item:
            key, value = item.split("=", 1)
            env_dict[key] = value

    labels = template.labels or {}
    labels["autoscale.group"] = TARGET_GROUP
    labels["autoscale.target"] = "true"

    docker_client.containers.run(
        image=template.image.tags[0] if template.image.tags else template.image.id,
        command=template.attrs.get("Config", {}).get("Cmd"),
        detach=True,
        name=name,
        environment=env_dict,
        labels=labels,
        network=network_name,
    )
    add_event("scale_up", f"Created replica {name}")


def scale_down(containers):
    replicas = [c for c in containers if c.name.startswith(f"{TARGET_GROUP}-r")]
    if not replicas:
        return
    victim = sorted(replicas, key=lambda c: c.name)[-1]
    victim.remove(force=True)
    add_event("scale_down", f"Removed replica {victim.name}")


def ensure_min_replicas():
    running = running_group_containers()
    if len(running) >= MIN_REPLICAS:
        return
    all_group = group_containers()
    if not all_group:
        return
    template = all_group[0]
    while len(running_group_containers()) < MIN_REPLICAS:
        scale_up(template, all_group)
        all_group = group_containers()


def loop():
    global last_scale_ts
    add_event("startup", f"Autoscaler started for group={TARGET_GROUP}")
    while True:
        try:
            ensure_min_replicas()
            running = running_group_containers()
            if not running:
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            avg = average_cpu(running)
            now = time.time()
            cooling = (now - last_scale_ts) < COOLDOWN_SEC

            if not cooling and avg >= UP_CPU_PCT and len(running) < MAX_REPLICAS:
                scale_up(running[0], group_containers())
                last_scale_ts = now
            elif not cooling and avg <= DOWN_CPU_PCT and len(running) > MIN_REPLICAS:
                scale_down(group_containers())
                last_scale_ts = now
        except Exception as exc:
            add_event("error", str(exc))

        time.sleep(CHECK_INTERVAL_SEC)


AUTOSCALE_UI_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Autoscaler — local</title>
<style>
:root{--bg:#0b1220;--card:#151d2e;--bd:#2d3a52;--txt:#e8edf7;--a:#f472b6}
body{font-family:ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:1.25rem;line-height:1.55;max-width:52rem}
h1{font-size:1.15rem}a{color:var(--a)}.muted{opacity:.85;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:1rem;margin:1rem 0}
pre{background:#0f172a;padding:1rem;border-radius:8px;overflow:auto;font-size:.8rem;max-height:28rem}
</style></head><body>
<h1>Autoscaler (Docker demo)</h1>
<p class="muted">Scales containers labeled <code>autoscale.group={{ tg }}</code>. Dev stack — no API auth.</p>
<div class="card"><strong>JSON</strong> · <a href="/health">/health</a> · <a href="/status">/status</a></div>
<div class="card"><strong>Live status</strong><pre id="st">Loading…</pre></div>
<script>
async function tick(){
  try{
    const r=await fetch('/status'); const j=await r.json();
    document.getElementById('st').textContent=JSON.stringify(j,null,2);
  }catch(e){ document.getElementById('st').textContent=String(e); }
}
tick(); setInterval(tick, 5000);
</script>
</body></html>"""


@app.get("/")
@app.get("/panel")
def management_ui():
    return render_template_string(AUTOSCALE_UI_PAGE, tg=TARGET_GROUP)


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "service": "autoscaler",
            "target_group": TARGET_GROUP,
            "generated_at": utc_now(),
        }
    )


@app.get("/status")
def status():
    running = running_group_containers()
    avg = average_cpu(running) if running else 0.0
    return jsonify(
        {
            "ok": True,
            "target_group": TARGET_GROUP,
            "replicas_running": len(running),
            "min_replicas": MIN_REPLICAS,
            "max_replicas": MAX_REPLICAS,
            "avg_cpu_percent": round(avg, 2),
            "up_threshold": UP_CPU_PCT,
            "down_threshold": DOWN_CPU_PCT,
            "cooldown_sec": COOLDOWN_SEC,
            "events": autoscale_events[-25:],
        }
    )


if __name__ == "__main__":
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    app.run(host="0.0.0.0", port=8084)

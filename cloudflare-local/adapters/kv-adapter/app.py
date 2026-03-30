import os
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request
from redis import Redis
from redis.exceptions import RedisError

app = Flask(__name__)

KV_REDIS_URL = os.getenv("KV_REDIS_URL", "redis://valkey:6379/0")
KV_DELAY_MS = int(os.getenv("KV_SIMULATED_DELAY_MS", "0"))

redis_client = Redis.from_url(KV_REDIS_URL, decode_responses=True)
NAMESPACES_KEY = "cfkv:namespaces"


def api_error(message, code=400):
    return jsonify({"ok": False, "error": message}), code


def namespace_key(namespace, key):
    return f"cfkv:{namespace}:{key}"


KV_UI_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>KV adapter — local</title>
<style>
:root{--bg:#0b1220;--card:#151d2e;--bd:#2d3a52;--txt:#e8edf7;--a:#34d399}
body{font-family:ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:1.25rem;line-height:1.55;max-width:52rem}
h1{font-size:1.15rem}a{color:var(--a)}.muted{opacity:.85;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:1rem;margin:1rem 0}
code{font-size:.85em;background:#1e293b;padding:.15rem .4rem;border-radius:4px}
#ns li{cursor:pointer;color:var(--a);text-decoration:underline}
#keys{font-size:.85rem;max-height:12rem;overflow:auto;background:#0f172a;padding:.5rem;border-radius:6px}
.err{color:#f87171}
input,button{padding:.4rem .6rem;border-radius:6px;border:1px solid #475569;background:#1e293b;color:#e2e8f0}
</style></head><body>
<h1>KV adapter (Valkey / Redis)</h1>
<p class="muted">Namespaces and keys for local Workers-style KV emulation.</p>
<div class="card"><strong>Credentials</strong>
<p>Default compose: <strong>no password</strong> on Valkey. Redis URL (container): <code>{{ redis_url }}</code></p>
</div>
<div class="card"><strong>API</strong>
<ul><li><a href="/health">/health</a></li><li><a href="/namespaces">/namespaces</a></li></ul></div>
<div class="card"><strong>Explorer</strong>
<p><input id="newns" placeholder="new namespace" /> <button type="button" id="btnns">Create (POST)</button></p>
<ul id="ns"></ul>
<pre id="keys"></pre>
<p id="err" class="err"></p>
</div>
<script>
async function loadNs(){
  document.getElementById('err').textContent='';
  const r=await fetch('/namespaces'); const j=await r.json();
  const ul=document.getElementById('ns'); ul.innerHTML='';
  if(!j.ok){ document.getElementById('err').textContent=j.error||'fail'; return; }
  (j.namespaces||[]).forEach(n=>{
    const li=document.createElement('li'); li.textContent=n; li.onclick=()=>loadKeys(n); ul.appendChild(li);
  });
}
async function loadKeys(ns){
  const e=document.getElementById('err'); const pre=document.getElementById('keys'); e.textContent=''; pre.textContent='…';
  const r=await fetch('/namespaces/'+encodeURIComponent(ns)+'/keys?limit=100');
  const j=await r.json();
  if(!j.ok){ pre.textContent=''; e.textContent=j.error||'fail'; return; }
  pre.textContent=(j.keys||[]).join('\\n')||'(no keys)';
}
document.getElementById('btnns').onclick=async()=>{
  const v=document.getElementById('newns').value.trim(); if(!v)return;
  const r=await fetch('/namespaces',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v})});
  const j=await r.json(); if(!j.ok){ document.getElementById('err').textContent=j.error||'fail'; return; }
  document.getElementById('newns').value=''; loadNs();
};
loadNs();
</script>
</body></html>"""


@app.get("/")
@app.get("/panel")
def management_ui():
    return render_template_string(KV_UI_PAGE, redis_url=KV_REDIS_URL)


@app.get("/health")
def health():
    try:
        pong = redis_client.ping()
        namespace_count = redis_client.scard(NAMESPACES_KEY)
        return jsonify(
            {
                "ok": bool(pong),
                "service": "kv-adapter",
                "redis_url": KV_REDIS_URL,
                "namespace_count": namespace_count,
                "simulated_delay_ms": KV_DELAY_MS,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except RedisError as exc:
        return api_error(str(exc), 500)


@app.get("/namespaces")
def list_namespaces():
    try:
        namespaces = sorted(list(redis_client.smembers(NAMESPACES_KEY)))
        return jsonify({"ok": True, "namespaces": namespaces})
    except RedisError as exc:
        return api_error(str(exc), 500)


@app.post("/namespaces")
def create_namespace():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return api_error("Namespace name is required.")

    try:
        redis_client.sadd(NAMESPACES_KEY, name)
        return jsonify({"ok": True, "namespace": name})
    except RedisError as exc:
        return api_error(str(exc), 500)


@app.delete("/namespaces/<namespace>")
def delete_namespace(namespace):
    try:
        cursor = 0
        pattern = namespace_key(namespace, "*")
        while True:
            cursor, keys = redis_client.scan(cursor=cursor, match=pattern, count=200)
            if keys:
                redis_client.delete(*keys)
            if cursor == 0:
                break
        redis_client.srem(NAMESPACES_KEY, namespace)
        return jsonify({"ok": True, "namespace": namespace, "deleted": True})
    except RedisError as exc:
        return api_error(str(exc), 500)


@app.put("/namespaces/<namespace>/values/<path:key>")
def put_value(namespace, key):
    ttl = request.args.get("ttl")
    value = request.get_data(as_text=True)
    if namespace not in redis_client.smembers(NAMESPACES_KEY):
        return api_error("Namespace does not exist.", 404)

    try:
        redis_key = namespace_key(namespace, key)
        if ttl:
            redis_client.setex(redis_key, int(ttl), value)
        else:
            redis_client.set(redis_key, value)
        return jsonify({"ok": True, "namespace": namespace, "key": key, "ttl": int(ttl) if ttl else None})
    except (RedisError, ValueError) as exc:
        return api_error(str(exc), 500)


@app.get("/namespaces/<namespace>/values/<path:key>")
def get_value(namespace, key):
    if namespace not in redis_client.smembers(NAMESPACES_KEY):
        return api_error("Namespace does not exist.", 404)

    try:
        value = redis_client.get(namespace_key(namespace, key))
        if value is None:
            return api_error("Key not found.", 404)
        return jsonify({"ok": True, "namespace": namespace, "key": key, "value": value})
    except RedisError as exc:
        return api_error(str(exc), 500)


@app.delete("/namespaces/<namespace>/values/<path:key>")
def delete_value(namespace, key):
    try:
        removed = redis_client.delete(namespace_key(namespace, key))
        return jsonify({"ok": True, "namespace": namespace, "key": key, "deleted": bool(removed)})
    except RedisError as exc:
        return api_error(str(exc), 500)


@app.get("/namespaces/<namespace>/keys")
def list_keys(namespace):
    prefix = request.args.get("prefix", "")
    limit = int(request.args.get("limit", "200"))
    try:
        cursor = 0
        keys = []
        pattern = namespace_key(namespace, f"{prefix}*")
        while True:
            cursor, found = redis_client.scan(cursor=cursor, match=pattern, count=200)
            for item in found:
                stripped = item.replace(f"cfkv:{namespace}:", "", 1)
                keys.append(stripped)
                if len(keys) >= limit:
                    return jsonify({"ok": True, "namespace": namespace, "keys": keys, "truncated": True})
            if cursor == 0:
                break
        return jsonify({"ok": True, "namespace": namespace, "keys": keys, "truncated": False})
    except (RedisError, ValueError) as exc:
        return api_error(str(exc), 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082)

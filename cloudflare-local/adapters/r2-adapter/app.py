import io
import os
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template_string, request, send_file
from minio import Minio
from minio.error import S3Error

app = Flask(__name__)

R2_S3_ENDPOINT = os.getenv("R2_S3_ENDPOINT", "minio:9000")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "minioadmin")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "minioadmin")
R2_SECURE = os.getenv("R2_SECURE", "false").lower() == "true"
R2_DEFAULT_REGION = os.getenv("R2_DEFAULT_REGION", "auto")

client = Minio(
    R2_S3_ENDPOINT,
    access_key=R2_ACCESS_KEY,
    secret_key=R2_SECRET_KEY,
    secure=R2_SECURE,
    region=R2_DEFAULT_REGION,
)

# Simple local multipart emulation for development parity.
multipart_sessions = {}


def api_error(message, code=400):
    return jsonify({"ok": False, "error": message}), code


R2_UI_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>R2 adapter — local</title>
<style>
:root{--bg:#0b1220;--card:#151d2e;--bd:#2d3a52;--txt:#e8edf7;--a:#38bdf8;--ok:#4ade80}
*{box-sizing:border-box}body{font-family:ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:1.25rem;line-height:1.55;max-width:52rem}
h1{font-size:1.15rem;margin:0 0 .25rem}a{color:var(--a)}
.muted{opacity:.85;font-size:.9rem}.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:1rem;margin:1rem 0}
.row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;margin:.35rem 0}
code{font-size:.85em;background:#1e293b;padding:.15rem .4rem;border-radius:4px}
#buckets li{cursor:pointer;color:var(--a);text-decoration:underline}
#objects{font-size:.85rem;max-height:14rem;overflow:auto;background:#0f172a;padding:.5rem;border-radius:6px}
.err{color:#f87171}
</style></head><body>
<h1>R2 adapter (S3-compatible)</h1>
<p class="muted">Local dev stack — not Cloudflare production. Use this page or the JSON API.</p>

<div class="card"><strong>Credentials (MinIO / S3 backend)</strong>
<p>Access key: <code>{{ access_key }}</code> · Secret: <code>{{ secret_key }}</code></p>
<p class="muted">Same values are used by this adapter to talk to MinIO. Web UI: <a href="http://minio-console.lh" target="_blank" rel="noopener">minio-console.lh</a> (user <code>{{ access_key }}</code> / password <code>{{ secret_key }}</code>).</p>
</div>

<div class="card"><strong>Quick links</strong>
<ul>
<li><a href="/health">GET /health</a> — JSON status</li>
<li><a href="/buckets">GET /buckets</a> — list buckets (JSON)</li>
</ul></div>

<div class="card"><strong>Bucket explorer</strong>
<p class="muted">Click a bucket to list objects (first 80).</p>
<ul id="buckets"></ul>
<pre id="objects"></pre>
<p id="err" class="err"></p>
</div>
<script>
async function loadBuckets(){
  const e=document.getElementById('err'); e.textContent='';
  try{
    const r=await fetch('/buckets'); const j=await r.json();
    const ul=document.getElementById('buckets'); ul.innerHTML='';
    if(!j.ok){ e.textContent=j.error||'failed'; return; }
    (j.buckets||[]).forEach(b=>{
      const li=document.createElement('li'); li.textContent=b.name; li.onclick=()=>loadObjects(b.name); ul.appendChild(li);
    });
  }catch(x){ e.textContent=String(x); }
}
async function loadObjects(bucket){
  const e=document.getElementById('err'); const pre=document.getElementById('objects'); e.textContent=''; pre.textContent='Loading…';
  try{
    const r=await fetch('/objects/'+encodeURIComponent(bucket)+'?limit=80');
    const j=await r.json();
    if(!j.ok){ pre.textContent=''; e.textContent=j.error||'failed'; return; }
    pre.textContent=JSON.stringify(j.objects||[],null,2);
  }catch(x){ pre.textContent=''; e.textContent=String(x); }
}
loadBuckets();
</script>
</body></html>"""


@app.get("/")
@app.get("/panel")
def management_ui():
    return render_template_string(
        R2_UI_PAGE,
        access_key=R2_ACCESS_KEY,
        secret_key=R2_SECRET_KEY,
    )


@app.get("/health")
def health():
    try:
        buckets = client.list_buckets()
        return jsonify(
            {
                "ok": True,
                "service": "r2-adapter",
                "s3_endpoint": R2_S3_ENDPOINT,
                "bucket_count": len(buckets),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/buckets")
def list_buckets():
    try:
        buckets = client.list_buckets()
        return jsonify(
            {
                "ok": True,
                "buckets": [
                    {"name": bucket.name, "created_at": bucket.creation_date.isoformat()}
                    for bucket in buckets
                ],
            }
        )
    except Exception as exc:
        return api_error(str(exc), 500)


@app.post("/buckets")
def create_bucket():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return api_error("Bucket name is required.")
    try:
        if not client.bucket_exists(name):
            client.make_bucket(name)
        return jsonify({"ok": True, "bucket": name})
    except S3Error as exc:
        return api_error(exc.message, 500)


@app.delete("/buckets/<bucket>")
def delete_bucket(bucket):
    try:
        for obj in client.list_objects(bucket, recursive=True):
            client.remove_object(bucket, obj.object_name)
        client.remove_bucket(bucket)
        return jsonify({"ok": True, "bucket": bucket, "deleted": True})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.get("/objects/<bucket>")
def list_objects(bucket):
    prefix = request.args.get("prefix", "")
    limit = int(request.args.get("limit", "100"))
    try:
        entries = []
        for obj in client.list_objects(bucket, prefix=prefix, recursive=True):
            entries.append(
                {
                    "key": obj.object_name,
                    "size": obj.size,
                    "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
                    "etag": obj.etag,
                }
            )
            if len(entries) >= limit:
                break
        return jsonify({"ok": True, "bucket": bucket, "objects": entries})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.put("/objects/<bucket>/<path:key>")
def put_object(bucket, key):
    body = request.get_data()
    content_type = request.headers.get("Content-Type", "application/octet-stream")
    try:
        result = client.put_object(
            bucket,
            key,
            io.BytesIO(body),
            length=len(body),
            content_type=content_type,
        )
        return jsonify({"ok": True, "bucket": bucket, "key": key, "etag": result.etag})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.get("/objects/<bucket>/<path:key>")
def get_object(bucket, key):
    try:
        response = client.get_object(bucket, key)
        data = response.read()
        response.close()
        response.release_conn()
        return send_file(
            io.BytesIO(data),
            mimetype="application/octet-stream",
            as_attachment=False,
            download_name=key.split("/")[-1] or key,
        )
    except Exception as exc:
        return api_error(str(exc), 404)


@app.delete("/objects/<bucket>/<path:key>")
def delete_object(bucket, key):
    try:
        client.remove_object(bucket, key)
        return jsonify({"ok": True, "bucket": bucket, "key": key, "deleted": True})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.post("/multipart/start")
def multipart_start():
    payload = request.get_json(silent=True) or {}
    bucket = (payload.get("bucket") or "").strip()
    key = (payload.get("key") or "").strip()
    if not bucket or not key:
        return api_error("Both bucket and key are required.")

    upload_id = str(uuid.uuid4())
    multipart_sessions[upload_id] = {"bucket": bucket, "key": key, "parts": {}}
    return jsonify({"ok": True, "upload_id": upload_id, "bucket": bucket, "key": key})


@app.put("/multipart/upload-part")
def multipart_upload_part():
    upload_id = request.args.get("upload_id", "")
    part_number = int(request.args.get("part_number", "0"))
    if not upload_id or part_number <= 0:
        return api_error("upload_id and positive part_number are required.")
    if upload_id not in multipart_sessions:
        return api_error("Unknown upload_id.", 404)

    multipart_sessions[upload_id]["parts"][part_number] = request.get_data()
    return jsonify({"ok": True, "upload_id": upload_id, "part_number": part_number})


@app.post("/multipart/complete")
def multipart_complete():
    payload = request.get_json(silent=True) or {}
    upload_id = (payload.get("upload_id") or "").strip()
    if upload_id not in multipart_sessions:
        return api_error("Unknown upload_id.", 404)

    session = multipart_sessions[upload_id]
    ordered_parts = [session["parts"][k] for k in sorted(session["parts"].keys())]
    content = b"".join(ordered_parts)

    try:
        result = client.put_object(
            session["bucket"],
            session["key"],
            io.BytesIO(content),
            len(content),
            content_type="application/octet-stream",
        )
    except Exception as exc:
        return api_error(str(exc), 500)
    finally:
        multipart_sessions.pop(upload_id, None)

    return jsonify(
        {
            "ok": True,
            "bucket": session["bucket"],
            "key": session["key"],
            "etag": result.etag,
            "size": len(content),
        }
    )


@app.post("/presign")
def presign_url():
    payload = request.get_json(silent=True) or {}
    bucket = (payload.get("bucket") or "").strip()
    key = (payload.get("key") or "").strip()
    method = (payload.get("method") or "GET").upper()
    if not bucket or not key:
        return api_error("bucket and key are required.")

    try:
        if method == "GET":
            url = client.presigned_get_object(bucket, key)
        elif method == "PUT":
            url = client.presigned_put_object(bucket, key)
        else:
            return api_error("method must be GET or PUT.")
        return jsonify({"ok": True, "url": url, "method": method})
    except Exception as exc:
        return api_error(str(exc), 500)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081)

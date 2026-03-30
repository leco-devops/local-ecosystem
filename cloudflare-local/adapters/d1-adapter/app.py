import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

DATA_DIR = Path(os.getenv("D1_DATA_DIR", "/data"))
BACKUP_DIR = Path(os.getenv("D1_BACKUP_DIR", "/backups"))
MIGRATIONS_DIR = Path(os.getenv("D1_MIGRATIONS_DIR", "/migrations"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def api_error(message, code=400):
    return jsonify({"ok": False, "error": message}), code


D1_UI_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>D1 adapter — local</title>
<style>
:root{--bg:#0b1220;--card:#151d2e;--bd:#2d3a52;--txt:#e8edf7;--a:#a78bfa}
body{font-family:ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--txt);margin:0;padding:1.25rem;line-height:1.55;max-width:52rem}
h1{font-size:1.15rem}a{color:var(--a)}.muted{opacity:.85;font-size:.9rem}
.card{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:1rem;margin:1rem 0}
code{font-size:.85em;background:#1e293b;padding:.15rem .4rem;border-radius:4px}
textarea,input,button{width:100%;padding:.5rem;border-radius:6px;border:1px solid #475569;background:#1e293b;color:#e2e8f0;font-family:ui-monospace,monospace;font-size:.85rem}
button{width:auto;cursor:pointer;margin-top:.5rem}
#out{font-size:.8rem;max-height:16rem;overflow:auto;background:#0f172a;padding:.75rem;border-radius:6px;white-space:pre-wrap}
.err{color:#f87171}
</style></head><body>
<h1>D1 adapter (SQLite)</h1>
<p class="muted">Local SQL databases under <code>{{ data_dir }}</code> — dev only, no auth.</p>
<div class="card"><strong>Credentials</strong><p>No API authentication in this local stack.</p></div>
<div class="card"><strong>API</strong><ul><li><a href="/health">/health</a></li><li><a href="/databases">/databases</a></li></ul></div>
<div class="card"><strong>Database explorer</strong>
<p><input id="newdb" placeholder="new database name" /> <button type="button" id="btndb">Create DB</button></p>
<ul id="dblist"></ul>
<p class="muted">Read-only-ish: run <code>SELECT …</code> below.</p>
<label>SQL</label><textarea id="sql" rows="4">SELECT name FROM sqlite_master WHERE type='table';</textarea>
<button type="button" id="runq">Run query</button>
<pre id="out"></pre>
<p id="err" class="err"></p>
</div>
<script>
let currentDb=null;
async function loadDbs(){
  document.getElementById('err').textContent='';
  const r=await fetch('/databases'); const j=await r.json();
  const ul=document.getElementById('dblist'); ul.innerHTML='';
  if(!j.ok){ document.getElementById('err').textContent=j.error||'fail'; return; }
  (j.databases||[]).forEach(d=>{
    const li=document.createElement('li'); li.style.cursor='pointer'; li.style.color='#a78bfa'; li.style.textDecoration='underline';
    li.textContent=d; li.onclick=()=>{currentDb=d; document.getElementById('out').textContent='Selected DB: '+d;}; ul.appendChild(li);
  });
}
document.getElementById('btndb').onclick=async()=>{
  const n=document.getElementById('newdb').value.trim(); if(!n)return;
  const r=await fetch('/databases',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:n})});
  const j=await r.json(); if(!j.ok){ document.getElementById('err').textContent=j.error||'fail'; return; }
  document.getElementById('newdb').value=''; loadDbs();
};
document.getElementById('runq').onclick=async()=>{
  const e=document.getElementById('err'); const o=document.getElementById('out'); e.textContent='';
  if(!currentDb){ e.textContent='Click a database name first.'; return; }
  const sql=document.getElementById('sql').value;
  const r=await fetch('/databases/'+encodeURIComponent(currentDb)+'/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql,params:[]})});
  const j=await r.json();
  if(!j.ok){ o.textContent=''; e.textContent=j.error||'fail'; return; }
  o.textContent=JSON.stringify(j.rows||[],null,2);
};
loadDbs();
</script>
</body></html>"""


@app.get("/")
@app.get("/panel")
def management_ui():
    return render_template_string(D1_UI_PAGE, data_dir=str(DATA_DIR))


def db_path(name):
    return DATA_DIR / f"{name}.sqlite"


def connect_db(name):
    return sqlite3.connect(db_path(name))


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "service": "d1-adapter",
            "data_dir": str(DATA_DIR),
            "database_count": len(list(DATA_DIR.glob("*.sqlite"))),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.get("/databases")
def list_databases():
    names = sorted([f.stem for f in DATA_DIR.glob("*.sqlite")])
    return jsonify({"ok": True, "databases": names})


@app.post("/databases")
def create_database():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return api_error("Database name is required.")
    path = db_path(name)
    if not path.exists():
        conn = sqlite3.connect(path)
        conn.close()
    return jsonify({"ok": True, "database": name})


@app.delete("/databases/<name>")
def delete_database(name):
    path = db_path(name)
    if not path.exists():
        return api_error("Database not found.", 404)
    path.unlink()
    return jsonify({"ok": True, "database": name, "deleted": True})


@app.post("/databases/<name>/execute")
def execute_sql(name):
    payload = request.get_json(silent=True) or {}
    sql = payload.get("sql")
    params = payload.get("params", [])
    if not sql:
        return api_error("sql is required.")
    if not db_path(name).exists():
        return api_error("Database not found.", 404)

    try:
        conn = connect_db(name)
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        affected = cur.rowcount
        conn.close()
        return jsonify({"ok": True, "database": name, "rows_affected": affected})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.post("/databases/<name>/query")
def query_sql(name):
    payload = request.get_json(silent=True) or {}
    sql = payload.get("sql")
    params = payload.get("params", [])
    if not sql:
        return api_error("sql is required.")
    if not db_path(name).exists():
        return api_error("Database not found.", 404)

    try:
        conn = connect_db(name)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return jsonify({"ok": True, "database": name, "rows": rows, "count": len(rows)})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.post("/databases/<name>/migrate")
def migrate(name):
    payload = request.get_json(silent=True) or {}
    migration_subdir = payload.get("dir", name)
    migration_dir = MIGRATIONS_DIR / migration_subdir
    if not migration_dir.exists():
        return api_error(f"Migration directory not found: {migration_dir}", 404)

    path = db_path(name)
    if not path.exists():
        conn = sqlite3.connect(path)
        conn.close()

    files = sorted(migration_dir.glob("*.sql"))
    try:
        conn = connect_db(name)
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE IF NOT EXISTS _d1_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {r[0] for r in cur.execute("SELECT name FROM _d1_migrations").fetchall()}
        newly_applied = []
        for file in files:
            if file.name in applied:
                continue
            sql = file.read_text()
            cur.executescript(sql)
            cur.execute(
                "INSERT INTO _d1_migrations (name, applied_at) VALUES (?, ?)",
                (file.name, datetime.now(timezone.utc).isoformat()),
            )
            newly_applied.append(file.name)
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "database": name, "applied": newly_applied, "total_migrations": len(files)})
    except Exception as exc:
        return api_error(str(exc), 500)


@app.post("/databases/<name>/backup")
def backup(name):
    source = db_path(name)
    if not source.exists():
        return api_error("Database not found.", 404)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = BACKUP_DIR / f"{name}-{stamp}.sqlite"
    shutil.copyfile(source, target)
    return jsonify({"ok": True, "database": name, "backup_file": str(target)})


@app.post("/databases/<name>/restore")
def restore(name):
    payload = request.get_json(silent=True) or {}
    backup_file = (payload.get("backup_file") or "").strip()
    if not backup_file:
        return api_error("backup_file is required.")
    source = Path(backup_file)
    if not source.exists():
        return api_error("backup_file not found.", 404)

    target = db_path(name)
    shutil.copyfile(source, target)
    return jsonify({"ok": True, "database": name, "restored_from": backup_file})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8083)

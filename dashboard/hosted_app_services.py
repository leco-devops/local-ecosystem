"""Per-hosted-app attached services: compose, runtimes, Cloudflare, ecosystem hubs."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse, urlunparse


def _yaml_load_file(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))

_COMPOSE_SERVICE_KIND_RE = [
    (re.compile(r"mysql|mariadb", re.I), "mysql"),
    (re.compile(r"postgres", re.I), "postgres"),
    (re.compile(r"redis|valkey", re.I), "redis"),
    (re.compile(r"mongo", re.I), "mongodb"),
    (re.compile(r"minio", re.I), "minio"),
    (re.compile(r"nginx|caddy|traefik|haproxy", re.I), "proxy"),
    (re.compile(r"adminer", re.I), "database_gui"),
    (re.compile(r"mailpit|mailhog", re.I), "mail"),
]

_ECO_SERVICE_ALIASES: dict[str, str] = {
    "mysql": "mysql",
    "mariadb": "mysql",
    "db": "mysql",
    "database": "mysql",
    "n8n_postgres": "postgres",
    "postgres": "postgres",
    "postgresql": "postgres",
    "redis": "redis",
    "valkey": "redis",
    "minio": "minio",
}


def _pick(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _hub_map_by_slug() -> dict[str, dict[str, Any]]:
    try:
        from monitor import SERVICE_MAP
    except ImportError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in SERVICE_MAP:
        if not isinstance(row, dict):
            continue
        slug = str(row.get("hub_slug") or "").strip()
        if slug:
            out[slug] = row
    return out


def _ecosystem_hub_item(hub_slug: str, *, name: str, notes: str = "") -> dict[str, Any]:
    hubs = _hub_map_by_slug()
    hub = hubs.get(hub_slug) or {}
    creds: dict[str, str] = {}
    try:
        from ui_credentials import _merged_credentials

        creds = dict(_merged_credentials(hub_slug))
    except Exception:
        pass
    conn = list(hub.get("connection_strings") or [])
    mgmt: list[dict[str, str]] = []
    for g in hub.get("database_guis") or []:
        if isinstance(g, dict) and g.get("url"):
            mgmt.append({"label": str(g.get("label") or "UI"), "url": str(g["url"])})
    for g in hub.get("management_links") or []:
        if isinstance(g, dict) and g.get("url"):
            mgmt.append({"label": str(g.get("label") or "Link"), "url": str(g["url"])})
    hub_url = ""
    urls = hub.get("urls")
    if isinstance(urls, list) and urls:
        hub_url = str(urls[0])
    reg: dict[str, Any] = {}
    try:
        from ui_credentials import get_registry_entry

        reg = get_registry_entry(hub_slug) or {}
    except Exception:
        pass
    login_url = str(reg.get("login_url") or "")
    can_auto_login = (reg.get("auth_type") or "none") in ("form_post", "json_post")
    endpoints = _flat_strings_to_endpoints(conn)
    return {
        "id": f"ecosystem-{hub_slug}",
        "name": name or str(hub.get("service") or hub_slug),
        "kind": hub_slug,
        "source": "ecosystem",
        "status": "shared",
        "credentials": creds,
        "connection_endpoints": endpoints,
        "connection_strings": _endpoints_to_flat_strings(endpoints) or conn,
        "management_uis": mgmt,
        "hub_url": hub_url,
        "hub_slug": hub_slug,
        "login_url": login_url,
        "can_auto_login": can_auto_login,
        "notes": notes or str(hub.get("notes") or ""),
    }


def compose_paths_from_tail(compose_tail: list[str] | None) -> list[Path]:
    """Parse ``-f`` paths from ``leco_control`` compose_tail (same files ``docker compose`` uses)."""
    out: list[Path] = []
    tail = compose_tail or []
    i = 0
    while i < len(tail):
        if tail[i] == "-f" and i + 1 < len(tail):
            raw = str(tail[i + 1]).strip()
            if raw:
                try:
                    p = Path(raw).resolve()
                    if p.is_file() and p not in out:
                        out.append(p)
                except OSError:
                    pass
            i += 2
        else:
            i += 1
    return out


def list_compose_file_paths(
    manifest_path: Path,
    *,
    compose_tail: list[str] | None = None,
) -> list[Path]:
    """Resolve compose ``-f`` paths in deploy order (same rules as compose_runner)."""
    mp = manifest_path.resolve()
    tail_paths = compose_paths_from_tail(compose_tail)
    try:
        from leco_app.schema import load_effective_manifest
    except ImportError:
        return tail_paths
    try:
        m = load_effective_manifest(mp)
    except Exception:
        return tail_paths
    if not m.docker_compose:
        return tail_paths
    dc = m.docker_compose
    root = m.resolved_root(mp)
    manifest_parent = mp.parent.resolve()
    paths: list[Path] = []
    cfm = (dc.compose_file_from_manifest or "").strip()
    if cfm:
        er = Path(cfm)
        paths.append(er.resolve() if er.is_absolute() else (manifest_parent / er).resolve())
    elif (dc.compose_file or "").strip():
        rel = Path(dc.compose_file.strip())
        paths.append(rel.resolve() if rel.is_absolute() else (root / rel).resolve())
    for extra in dc.additional_compose_files or []:
        es = str(extra).strip()
        if not es:
            continue
        er = Path(es)
        cand = er.resolve() if er.is_absolute() else (root / er).resolve()
        if cand.is_file() and cand not in paths:
            paths.append(cand)
    for extra in dc.additional_compose_files_from_manifest or []:
        es = str(extra).strip()
        if not es:
            continue
        er = Path(es)
        cand = er.resolve() if er.is_absolute() else (manifest_parent / er).resolve()
        if cand.is_file() and cand not in paths:
            paths.append(cand)
    for p in compose_paths_from_tail(compose_tail):
        if p.is_file() and p not in paths:
            paths.append(p)
    return [p for p in paths if p.is_file()]


def _load_dotenv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            if "=" in s:
                k, _, v = s.partition("=")
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def load_merged_compose_services(
    manifest_path: Path,
    *,
    compose_tail: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Shallow-merge ``services:`` from all compose files for this manifest."""
    merged: dict[str, dict[str, Any]] = {}
    for cf in list_compose_file_paths(manifest_path, compose_tail=compose_tail):
        try:
            raw = _yaml_load_file(cf) or {}
        except (OSError, UnicodeDecodeError):
            continue
        except Exception:
            continue
        services = raw.get("services")
        if not isinstance(services, dict):
            continue
        for name, spec in services.items():
            if isinstance(spec, dict):
                entry = dict(spec)
                entry["_compose_file"] = str(cf)
                merged[str(name)] = entry
    return merged


def _env_dict_from_spec(spec: dict[str, Any], compose_dir: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    env = spec.get("environment")
    if isinstance(env, dict):
        for k, v in env.items():
            if v is not None:
                out[str(k)] = str(v)
    elif isinstance(env, list):
        for item in env:
            if isinstance(item, str) and "=" in item:
                k, _, v = item.partition("=")
                out[k.strip()] = v.strip()
    env_file = spec.get("env_file")
    files: list[Path] = []
    if isinstance(env_file, str) and env_file.strip():
        files.append(compose_dir / env_file.strip())
    elif isinstance(env_file, list):
        for ef in env_file:
            if isinstance(ef, str) and ef.strip():
                files.append(compose_dir / ef.strip())
    for dot in (compose_dir / ".env", compose_dir.parent / ".env"):
        if dot.is_file():
            files.append(dot)
    for ef in files:
        if not ef.is_file():
            continue
        try:
            for line in ef.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" in s:
                    k, _, v = s.partition("=")
                    out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except OSError:
            pass
    return out


def classify_compose_service(name: str, spec: dict[str, Any]) -> str:
    image = str(spec.get("image") or "")
    if name.startswith("leco-rt-") or "leco/runtime" in image:
        return "edge-runtime"
    for pat, kind in _COMPOSE_SERVICE_KIND_RE:
        if pat.search(image) or pat.search(name):
            return kind
    return "application"


def _published_ports(spec: dict[str, Any]) -> list[str]:
    ports: list[str] = []
    raw = spec.get("ports")
    if isinstance(raw, list):
        for p in raw:
            ports.append(str(p))
    elif raw:
        ports.append(str(raw))
    return ports


def _extract_credentials(kind: str, env: dict[str, str], service_name: str) -> dict[str, str]:
    creds: dict[str, str] = {}
    sn = service_name.lower()
    if kind == "mysql":
        creds["database"] = env.get("MYSQL_DATABASE", env.get("MARIADB_DATABASE", ""))
        creds["user"] = env.get("MYSQL_USER", env.get("MARIADB_USER", "root"))
        creds["password"] = env.get(
            "MYSQL_PASSWORD",
            env.get("MYSQL_ROOT_PASSWORD", env.get("MARIADB_ROOT_PASSWORD", "")),
        )
        if not creds.get("password"):
            creds["password"] = env.get("MYSQL_ROOT_PASSWORD", "")
        db = creds.get("database") or "localdev"
        user = creds.get("user") or "root"
        pw = creds.get("password") or "localdev"
        host = "mysql.lh" if sn in ("mysql", "db", "database") else service_name
        creds["connection_string"] = f"mysql://{user}:{pw}@{host}:3306/{db}"
    elif kind == "postgres":
        creds["database"] = env.get("POSTGRES_DB", env.get("POSTGRES_DATABASE", "n8n"))
        creds["user"] = env.get("POSTGRES_USER", "postgres")
        creds["password"] = env.get("POSTGRES_PASSWORD", "password")
        host = "postgres.lh" if "postgres" in sn else service_name
        creds["connection_string"] = (
            f"postgresql://{creds['user']}:{creds['password']}@{host}:5432/{creds['database']}"
        )
    elif kind == "redis":
        creds["host"] = env.get("REDIS_HOST", service_name)
        creds["port"] = env.get("REDIS_PORT", "6379")
        creds["password"] = env.get("REDIS_PASSWORD", "")
        if creds["password"]:
            creds["connection_string"] = f"redis://:{creds['password']}@{creds['host']}:{creds['port']}"
        else:
            creds["connection_string"] = f"redis://{creds['host']}:{creds['port']}"
    elif kind == "mongodb":
        creds["database"] = env.get("MONGO_INITDB_DATABASE", env.get("MONGO_DATABASE", ""))
        creds["user"] = env.get("MONGO_INITDB_ROOT_USERNAME", env.get("MONGO_USERNAME", ""))
        creds["password"] = env.get("MONGO_INITDB_ROOT_PASSWORD", env.get("MONGO_PASSWORD", ""))
        for key in (
            "MONGO_URI",
            "MONGODB_URI",
            "MONGODB_URL",
            "DATABASE_URL",
            "MONGO_URL",
        ):
            if env.get(key):
                creds["connection_string"] = env[key]
                break
        if not creds.get("connection_string"):
            user = creds.get("user") or ""
            pw = creds.get("password") or ""
            db = creds.get("database") or "admin"
            auth = f"{user}:{pw}@" if user and pw else ""
            host = "mongo" if sn in ("mongo", "mongodb") else service_name
            creds["connection_string"] = f"mongodb://{auth}{host}:27017/{db}?authSource=admin"
            creds["connection_string_host"] = (
                f"mongodb://{auth}127.0.0.1:27017/{db}?authSource=admin"
                if auth
                else f"mongodb://127.0.0.1:27017/{db}"
            )
    elif kind == "minio":
        creds["user"] = env.get("MINIO_ROOT_USER", env.get("MINIO_ACCESS_KEY", "minioadmin"))
        creds["password"] = env.get("MINIO_ROOT_PASSWORD", env.get("MINIO_SECRET_KEY", "minioadmin"))
        creds["connection_string"] = "http://s3.lh"
    return {k: v for k, v in creds.items() if v}


def _management_uis_for_data_store(
    kind: str,
    service_name: str,
    spec: dict[str, Any],
    creds: dict[str, str],
) -> list[dict[str, str]]:
    sn = service_name.lower()
    hub_slug = _ECO_SERVICE_ALIASES.get(sn)
    if hub_slug:
        item = _ecosystem_hub_item(hub_slug, name=service_name)
        return list(item.get("management_uis") or [])
    if kind == "mysql" or kind == "postgres":
        return [{"label": "Adminer", "url": "http://adminer.lh"}]
    if kind == "redis":
        return [{"label": "Redis Commander", "url": "http://redis-ui.lh"}]
    if kind == "minio":
        return [{"label": "MinIO Console", "url": "http://minio-console.lh"}]
    if kind == "database_gui":
        return [{"label": "Adminer", "url": "http://adminer.lh"}]
    if kind == "mail":
        return [{"label": "Mailpit", "url": "http://mail.lh"}]
    if kind == "mongodb":
        port = _host_port_from_publish(spec) or "27017"
        return [
            {
                "label": "MongoDB Compass (host)",
                "url": _build_host_mongodb_uri(creds, port),
            },
        ]
    return []


def _host_port_from_publish(spec: dict[str, Any]) -> str | None:
    for raw in _published_ports(spec):
        s = str(raw).strip().strip('"')
        if not s:
            continue
        parts = s.split(":")
        if len(parts) == 1 and parts[0].isdigit():
            return parts[0]
        if len(parts) >= 2:
            candidate = parts[1] if len(parts) == 3 else parts[0]
            if candidate.isdigit():
                return candidate
    return None


def _build_host_mongodb_uri(creds: dict[str, str], port: str) -> str:
    user = (creds.get("user") or "").strip()
    password = (creds.get("password") or "").strip()
    database = (creds.get("database") or "").strip()
    auth = ""
    if user:
        auth = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
    path = f"/{database}" if database else "/"
    qs = "?authSource=admin" if user else ""
    return f"mongodb://{auth}127.0.0.1:{port}{path}{qs}"


def _data_uri_for_host(uri: str, host_port: str) -> str:
    """Rewrite Docker service hostnames to loopback for host-side clients (Compass, redis-cli)."""
    parsed = urlparse(uri)
    if not parsed.scheme:
        return uri
    netloc = parsed.netloc
    if "@" in netloc:
        auth, _host = netloc.rsplit("@", 1)
        new_netloc = f"{auth}@127.0.0.1:{host_port}"
    else:
        new_netloc = f"127.0.0.1:{host_port}"
    return urlunparse(
        (parsed.scheme, new_netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
    )


def _is_docker_internal_data_uri(uri: str) -> bool:
    parsed = urlparse(uri)
    if not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in ("127.0.0.1", "localhost") or host.endswith(".lh"):
        return False
    return True


def _connection_scope_label(scope: str) -> str:
    return {
        "host": "From your Mac (host)",
        "docker": "From app containers (Docker DNS)",
        "host_lh": "From your Mac (*.lh DNS)",
    }.get(scope, scope)


def _add_connection_endpoint(
    endpoints: list[dict[str, str]],
    scope: str,
    uri: str,
    *,
    label: str | None = None,
) -> None:
    uri = (uri or "").strip()
    if not uri:
        return
    for entry in endpoints:
        if entry.get("scope") == scope and entry.get("uri") == uri:
            return
    endpoints.append(
        {
            "scope": scope,
            "label": label or _connection_scope_label(scope),
            "uri": uri,
        }
    )


def _endpoints_to_flat_strings(endpoints: list[dict[str, str]]) -> list[str]:
    return [str(e.get("uri") or "") for e in endpoints if e.get("uri")]


def _flat_strings_to_endpoints(strings: list[str]) -> list[dict[str, str]]:
    endpoints: list[dict[str, str]] = []
    for raw in strings:
        uri = str(raw).strip()
        if not uri:
            continue
        if _is_docker_internal_data_uri(uri):
            _add_connection_endpoint(endpoints, "docker", uri)
        elif "://" in uri:
            parsed = urlparse(uri)
            host = (parsed.hostname or "").lower()
            if host.endswith(".lh"):
                _add_connection_endpoint(endpoints, "host_lh", uri)
            elif host in ("127.0.0.1", "localhost"):
                _add_connection_endpoint(endpoints, "host", uri)
            else:
                _add_connection_endpoint(endpoints, "host", uri)
        else:
            _add_connection_endpoint(endpoints, "host", uri)
    return endpoints


def _build_connection_endpoints(
    kind: str,
    service_name: str,
    spec: dict[str, Any],
    creds: dict[str, str],
    hints: list[str],
    existing: list[str],
) -> list[dict[str, str]]:
    endpoints: list[dict[str, str]] = []
    host_port = _host_port_from_publish(spec)
    default_ports = {
        "mongodb": "27017",
        "redis": "6379",
        "mysql": "3306",
        "postgres": "5432",
        "minio": "9000",
    }
    effective_port = host_port or default_ports.get(kind, "")
    sn = service_name.lower()
    docker_host = service_name

    if kind == "mongodb":
        user = (creds.get("user") or "").strip()
        password = (creds.get("password") or "").strip()
        database = (creds.get("database") or "").strip() or "admin"
        dp = effective_port or "27017"
        if user and password:
            docker_uri = f"mongodb://{user}:{password}@{docker_host}:27017/{database}?authSource=admin"
        elif user:
            docker_uri = f"mongodb://{user}@{docker_host}:27017/{database}?authSource=admin"
        else:
            docker_uri = f"mongodb://{docker_host}:27017/{database}"
        _add_connection_endpoint(endpoints, "docker", docker_uri)
        _add_connection_endpoint(endpoints, "host", _build_host_mongodb_uri(creds, dp))
    elif kind == "mysql":
        user = creds.get("user") or "root"
        pw = creds.get("password") or ""
        db = creds.get("database") or ""
        u, p = quote_plus(user), quote_plus(pw)
        _add_connection_endpoint(endpoints, "docker", f"mysql://{u}:{p}@{docker_host}:3306/{db}")
        if effective_port:
            _add_connection_endpoint(
                endpoints, "host", f"mysql://{u}:{p}@127.0.0.1:{effective_port}/{db}"
            )
        if sn in ("mysql", "db", "database", "mariadb"):
            _add_connection_endpoint(endpoints, "host_lh", f"mysql://{u}:{p}@mysql.lh:3306/{db}")
    elif kind == "postgres":
        user = creds.get("user") or "postgres"
        pw = creds.get("password") or ""
        db = creds.get("database") or ""
        u, p = quote_plus(user), quote_plus(pw)
        _add_connection_endpoint(
            endpoints, "docker", f"postgresql://{u}:{p}@{docker_host}:5432/{db}"
        )
        if effective_port:
            _add_connection_endpoint(
                endpoints, "host", f"postgresql://{u}:{p}@127.0.0.1:{effective_port}/{db}"
            )
        if "postgres" in sn:
            _add_connection_endpoint(
                endpoints, "host_lh", f"postgresql://{u}:{p}@postgres.lh:5432/{db}"
            )
    elif kind == "redis":
        pw = creds.get("password") or ""
        port = creds.get("port") or "6379"
        if pw:
            docker_uri = f"redis://:{quote_plus(pw)}@{docker_host}:{port}"
            host_uri = f"redis://:{quote_plus(pw)}@127.0.0.1:{effective_port or port}"
            lh_uri = f"redis://:{quote_plus(pw)}@redis.lh:{port}"
        else:
            docker_uri = f"redis://{docker_host}:{port}"
            host_uri = f"redis://127.0.0.1:{effective_port or port}"
            lh_uri = f"redis://redis.lh:{port}"
        _add_connection_endpoint(endpoints, "docker", docker_uri)
        if effective_port or host_port:
            _add_connection_endpoint(endpoints, "host", host_uri)
        if sn in ("redis", "valkey"):
            _add_connection_endpoint(endpoints, "host_lh", lh_uri)
    elif kind == "minio":
        _add_connection_endpoint(endpoints, "docker", f"http://{docker_host}:9000")
        if effective_port:
            _add_connection_endpoint(endpoints, "host", f"http://127.0.0.1:{effective_port}")
        _add_connection_endpoint(endpoints, "host_lh", "http://s3.lh")
        _add_connection_endpoint(endpoints, "host_lh", "http://minio-console.lh", label="MinIO console (*.lh)")

    for uri in list(hints) + list(existing):
        if not uri or not isinstance(uri, str):
            continue
        if _is_docker_internal_data_uri(uri):
            _add_connection_endpoint(endpoints, "docker", uri)
            if effective_port and kind in default_ports:
                _add_connection_endpoint(
                    endpoints, "host", _data_uri_for_host(uri, effective_port)
                )
        elif uri.startswith("http://") or uri.startswith("https://"):
            parsed = urlparse(uri)
            host = (parsed.hostname or "").lower()
            if host.endswith(".lh"):
                _add_connection_endpoint(endpoints, "host_lh", uri)
            elif host in ("127.0.0.1", "localhost"):
                _add_connection_endpoint(endpoints, "host", uri)
            elif _is_docker_internal_data_uri(uri):
                _add_connection_endpoint(endpoints, "docker", uri)
            else:
                _add_connection_endpoint(endpoints, "host", uri)
        else:
            _add_connection_endpoint(endpoints, "host", uri)

    scope_order = {"host": 0, "host_lh": 1, "docker": 2}
    endpoints.sort(key=lambda e: (scope_order.get(str(e.get("scope") or ""), 9), e.get("label") or ""))
    return endpoints


def _collect_connection_hints_from_compose(
    services: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """URIs in app service env (server, worker, …) that reference data stores."""
    hints: dict[str, list[str]] = {
        "mysql": [],
        "postgres": [],
        "redis": [],
        "mongodb": [],
    }
    uri_keys = {
        "mongodb": ("MONGO_URI", "MONGODB_URI", "MONGODB_URL", "MONGO_URL"),
        "redis": ("REDIS_URL", "REDIS_URI", "REDIS_DSN"),
        "mysql": ("MYSQL_URL", "DATABASE_URL", "MYSQL_DSN"),
        "postgres": ("POSTGRES_URL", "DATABASE_URL", "POSTGRES_URI", "PG_URL"),
    }
    for _sname, spec in services.items():
        cf = Path(str(spec.get("_compose_file") or "."))
        env = _env_dict_from_spec(spec, cf.parent)
        for kind, keys in uri_keys.items():
            for key in keys:
                val = env.get(key)
                if val and val not in hints[kind]:
                    hints[kind].append(val)
    return hints


def _enrich_data_store_items(
    items: list[dict[str, Any]],
    services: dict[str, dict[str, Any]],
    hints: dict[str, list[str]],
) -> None:
    by_name = {str(i.get("name") or "").lower(): i for i in items}
    for name, spec in services.items():
        kind = classify_compose_service(name, spec)
        if kind not in ("mysql", "postgres", "redis", "mongodb", "minio"):
            continue
        item = by_name.get(name.lower())
        if not item:
            continue
        host_port = _host_port_from_publish(spec)
        creds = dict(item.get("credentials") or {})
        conns = list(item.get("connection_strings") or [])
        if creds.get("connection_string_host"):
            hp = creds.pop("connection_string_host")
            if hp not in conns:
                conns.insert(0, hp)

        endpoints = _build_connection_endpoints(
            kind,
            name,
            spec,
            creds,
            hints.get(kind, []),
            conns,
        )
        effective_port = host_port or {
            "mongodb": "27017",
            "redis": "6379",
            "mysql": "3306",
            "postgres": "5432",
            "minio": "9000",
        }.get(kind, "")

        mgmt = list(item.get("management_uis") or [])
        if kind == "mongodb" and effective_port:
            compass = {
                "label": "MongoDB Compass (host)",
                "url": _build_host_mongodb_uri(creds, effective_port),
            }
            mgmt = [compass] + [
                m
                for m in mgmt
                if "Docker DNS" not in (m.get("label") or "")
                and "Compass" not in (m.get("label") or "")
            ]
            for other_name, other_spec in services.items():
                if "mongo-express" in other_name.lower() or "mongo_express" in other_name.lower():
                    me_port = _host_port_from_publish(other_spec)
                    if me_port:
                        url = f"http://127.0.0.1:{me_port}"
                        if not any(m.get("url") == url for m in mgmt):
                            mgmt.append({"label": "mongo-express", "url": url})

        item["credentials"] = creds
        item["connection_endpoints"] = endpoints
        item["connection_strings"] = _endpoints_to_flat_strings(endpoints)
        item["management_uis"] = mgmt


def _ps_status_map(compose_rows: list[dict[str, Any]] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in compose_rows or []:
        svc = str(row.get("service") or "").strip()
        if not svc:
            continue
        out[svc] = str(row.get("state") or "unknown").lower()
    return out


def _make_item(
    *,
    id: str,
    name: str,
    kind: str,
    source: str,
    status: str = "unknown",
    credentials: dict[str, str] | None = None,
    connection_strings: list[str] | None = None,
    management_uis: list[dict[str, str]] | None = None,
    hub_url: str = "",
    hub_slug: str = "",
    login_url: str = "",
    can_auto_login: bool = False,
    notes: str = "",
    container: str = "",
) -> dict[str, Any]:
    creds = dict(credentials or {})
    conns = list(connection_strings or [])
    if creds.get("connection_string") and creds["connection_string"] not in conns:
        conns.insert(0, creds["connection_string"])
    return {
        "id": id,
        "name": name,
        "kind": kind,
        "source": source,
        "status": status,
        "credentials": creds,
        "connection_endpoints": [],
        "connection_strings": conns,
        "management_uis": management_uis or [],
        "hub_url": hub_url,
        "hub_slug": hub_slug,
        "login_url": login_url,
        "can_auto_login": can_auto_login,
        "notes": notes,
        "container": container,
    }


def _compose_items(
    manifest_path: Path,
    compose_ps: list[dict[str, Any]] | None,
    *,
    compose_tail: list[str] | None = None,
) -> list[dict[str, Any]]:
    services = load_merged_compose_services(manifest_path, compose_tail=compose_tail)
    ps_map = _ps_status_map(compose_ps)
    if not services and compose_ps:
        for row in compose_ps:
            sname = str(row.get("service") or "").strip()
            if not sname:
                continue
            kind = classify_compose_service(sname, {"image": sname})
            if kind in ("mysql", "postgres", "redis", "mongodb", "minio", "application", "proxy"):
                services[sname] = {"image": sname, "_compose_file": str(manifest_path.parent)}
    if not services:
        return []
    hints = _collect_connection_hints_from_compose(services)
    items: list[dict[str, Any]] = []
    for name, spec in services.items():
        kind = classify_compose_service(name, spec)
        cf = Path(str(spec.get("_compose_file") or manifest_path.parent))
        env = _env_dict_from_spec(spec, cf.parent)
        creds = _extract_credentials(kind, env, name)
        mgmt = _management_uis_for_data_store(kind, name, spec, creds)
        cs = creds.pop("connection_string", "")
        conns = [cs] if cs else []
        if kind in ("mysql", "postgres", "redis", "mongodb", "minio") and not conns:
            hub_slug = _ECO_SERVICE_ALIASES.get(name.lower(), "")
            if hub_slug:
                hub_item = _ecosystem_hub_item(hub_slug, name=name)
                conns = list(hub_item.get("connection_strings") or [])
                if not creds:
                    creds = dict(hub_item.get("credentials") or {})
                mgmt = mgmt or list(hub_item.get("management_uis") or [])
        ports = _published_ports(spec)
        notes_parts = []
        if ports:
            notes_parts.append(f"ports: {', '.join(ports[:3])}")
        image = str(spec.get("image") or "")[:80]
        if image:
            notes_parts.append(f"image: {image}")
        container = ""
        for row in compose_ps or []:
            if str(row.get("service") or "") == name:
                container = str(row.get("container") or "")
                break
        items.append(
            _make_item(
                id=f"compose-{name}",
                name=name,
                kind=kind,
                source="compose",
                status=ps_map.get(name, "not_running"),
                credentials=creds,
                connection_strings=conns,
                management_uis=mgmt,
                notes=" · ".join(notes_parts),
                container=container,
            )
        )
    data_items = [i for i in items if i.get("kind") in ("mysql", "postgres", "redis", "mongodb", "minio")]
    _enrich_data_store_items(data_items, services, hints)
    return items


def _runtime_items(manifest_path: Path, compose_ps: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    try:
        from leco_app.schema import load_effective_manifest
    except ImportError:
        return []
    mp = manifest_path.resolve()
    try:
        m = load_effective_manifest(mp)
    except Exception:
        return []
    infra = {}
    lhp = (m.local_host_profile or "leco.yaml").strip()
    lp = mp.parent / lhp
    if lp.is_file():
        try:
            prof = _yaml_load_file(lp)
            if isinstance(prof, dict):
                infra = prof.get("infrastructure") if isinstance(prof.get("infrastructure"), dict) else {}
        except (OSError, Exception):
            pass
    runtimes = infra.get("runtimes") or []
    if not isinstance(runtimes, list):
        return []
    slug = str(m.name or mp.parent.name).strip().lower()
    prefix_map: dict[str, str] = {}
    rt_entries = infra.get("routing", {})
    if isinstance(rt_entries, dict):
        for ent in rt_entries.get("entries") or []:
            if not isinstance(ent, dict):
                continue
            host = str(ent.get("hostname") or f"{slug}.lh")
            for up in ent.get("upstream") or []:
                if not isinstance(up, dict):
                    continue
                rid = str(up.get("runtime") or "").strip()
                pfx = str(up.get("prefix") or "/")
                if rid:
                    prefix_map[rid] = f"https://{host.rstrip('/')}{pfx}"
    ps_by_svc = {str(r.get("service") or ""): r for r in compose_ps or []}
    items: list[dict[str, Any]] = []
    for rt in runtimes:
        if not isinstance(rt, dict):
            continue
        rid = str(rt.get("id") or "").strip()
        if not rid:
            continue
        rtype = str(rt.get("type") or "runtime")
        cfg = str(rt.get("config") or "")
        port = rt.get("port")
        svc_name = f"leco-rt-{slug}-{rid}"
        row = ps_by_svc.get(svc_name) or {}
        url = prefix_map.get(rid, f"https://{slug}.lh")
        notes = f"config: {cfg}" if cfg else ""
        if port:
            notes = (notes + f" · port {port}").strip(" · ")
        items.append(
            _make_item(
                id=f"runtime-{rid}",
                name=rid,
                kind=rtype,
                source="runtime",
                status=str(row.get("state") or "not_running").lower() if row else "expected",
                connection_strings=[url],
                management_uis=[{"label": "App URL", "url": url}],
                notes=notes,
                container=str(row.get("container") or svc_name),
            )
        )
    return items


def _cf_items(manifest_ui: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not manifest_ui:
        return []
    items: list[dict[str, Any]] = []
    w = manifest_ui.get("wrangler_expected") or {}
    lc = manifest_ui.get("local_cf") or {}
    hosts = manifest_ui.get("local_cf_adapter_hosts") or {}
    kv_base = str((hosts or {}).get("kv") or "https://kv.lh/panel")
    r2_base = str((hosts or {}).get("r2") or "https://r2.lh/panel")
    d1_base = str((hosts or {}).get("d1") or "https://d1.lh/panel")

    def _add_binding(
        binding: str,
        kind: str,
        local_name: str,
        *,
        provisioned: bool,
    ) -> None:
        panel = {"kv": kv_base, "r2": r2_base, "d1": d1_base}.get(kind, "")
        items.append(
            _make_item(
                id=f"cf-{kind}-{binding}",
                name=f"{binding} ({kind.upper()})",
                kind=kind,
                source="wrangler",
                status="provisioned" if provisioned else "expected",
                connection_strings=[panel] if panel else [],
                management_uis=[{"label": f"{kind.upper()} panel", "url": panel}] if panel else [],
                notes=f"local: {local_name}" if local_name else "",
                hub_slug=kind,
                hub_url=f"http://localhost.lh/hub/{kind}",
            )
        )

    if lc.get("present"):
        for row in lc.get("kv") or []:
            if isinstance(row, dict):
                _add_binding(
                    str(row.get("binding") or ""),
                    "kv",
                    str(row.get("local_namespace") or ""),
                    provisioned=True,
                )
        for row in lc.get("r2") or []:
            if isinstance(row, dict):
                _add_binding(
                    str(row.get("binding") or ""),
                    "r2",
                    str(row.get("bucket") or ""),
                    provisioned=True,
                )
        for row in lc.get("d1") or []:
            if isinstance(row, dict):
                _add_binding(
                    str(row.get("binding") or ""),
                    "d1",
                    str(row.get("database") or ""),
                    provisioned=True,
                )
    elif w.get("wrangler_configured"):
        for row in w.get("expected_kv") or []:
            if isinstance(row, dict):
                _add_binding(str(row.get("binding") or ""), "kv", str(row.get("cf_id") or ""), provisioned=False)
        for row in w.get("expected_r2") or []:
            if isinstance(row, dict):
                _add_binding(
                    str(row.get("binding") or ""),
                    "r2",
                    str(row.get("bucket_name") or ""),
                    provisioned=False,
                )
        for row in w.get("expected_d1") or []:
            if isinstance(row, dict):
                _add_binding(
                    str(row.get("binding") or ""),
                    "d1",
                    str(row.get("database_name") or ""),
                    provisioned=False,
                )
    wp = w.get("wrangler_path")
    seen_eco: set[str] = {str(i.get("id") or "") for i in items}
    if wp and Path(str(wp)).is_file():
        try:
            import tomllib

            td = tomllib.loads(Path(str(wp)).read_text(encoding="utf-8"))
            for hd in td.get("hyperdrive") or []:
                if not isinstance(hd, dict):
                    continue
                binding = str(hd.get("binding") or "HYPERDRIVE")
                eid = f"ecosystem-hyperdrive-{binding}"
                if eid in seen_eco:
                    continue
                seen_eco.add(eid)
                row = _ecosystem_hub_item(
                    "postgres",
                    name=f"Hyperdrive → {binding}",
                    notes="Wrangler hyperdrive binding; use postgres.lh / Adminer locally.",
                )
                row["id"] = eid
                items.append(row)
        except Exception:
            pass
    return items


def build_attached_services(
    manifest_path: str,
    *,
    compose_ps: list[dict[str, Any]] | None = None,
    manifest_ui: dict[str, Any] | None = None,
    compose_tail: list[str] | None = None,
) -> dict[str, Any]:
    """Build grouped attached-services payload for hosted app snapshot."""
    mp = Path(manifest_path).resolve()
    compose_all = _compose_items(mp, compose_ps, compose_tail=compose_tail)
    data_stores = [i for i in compose_all if i.get("kind") in ("mysql", "postgres", "redis", "mongodb", "minio")]
    compose_apps = [
        i
        for i in compose_all
        if i.get("kind") not in ("mysql", "postgres", "redis", "mongodb", "minio", "edge-runtime")
    ]
    edge = _runtime_items(mp, compose_ps)
    edge_compose = [i for i in compose_all if i.get("kind") == "edge-runtime"]
    edge_ids = {i.get("id") for i in edge}
    for e in edge_compose:
        if e.get("id") not in edge_ids:
            edge.append(e)
    cf = _cf_items(manifest_ui)
    groups: list[dict[str, Any]] = []
    if data_stores:
        groups.append({"id": "data_stores", "label": "Data stores", "items": data_stores})
    if edge:
        groups.append({"id": "edge_runtimes", "label": "Edge runtimes", "items": edge})
    if cf:
        groups.append({"id": "cloudflare", "label": "Cloudflare local", "items": cf})
    if compose_apps:
        groups.append({"id": "compose", "label": "Compose services", "items": compose_apps})
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_dev_only": True,
        "groups": groups,
    }


def load_compose_services_for_detect(
    localhost: dict[str, Any],
    root: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
    """Like leco_detect loader but honors ``composeFileFromManifest`` (shared with attached services)."""
    _ = manifest
    infra = localhost.get("infrastructure")
    if not isinstance(infra, dict):
        return None
    dc = infra.get("dockerCompose")
    if not isinstance(dc, dict):
        return None
    mp = root / "leco.app.yaml"
    if not mp.is_file():
        return None
    services = load_merged_compose_services(mp.resolve())
    if not services:
        return None
    clean = {k: {kk: vv for kk, vv in v.items() if not str(kk).startswith("_")} for k, v in services.items()}
    return clean, dc, infra

"""URLs, credentials, networking diagram, and admin helpers for dev stacks."""

from __future__ import annotations

import subprocess
from typing import Any

from dev_stack_compose import STACKS_ROOT, _slugify
from dev_stack_routes import http_container_name, stack_hostname

# Ecosystem GUIs on lh-network (not wired into isolated stack internal DB by default).
_PLATFORM_GUI_LINKS: tuple[dict[str, str], ...] = (
    {
        "id": "adminer",
        "label": "Adminer (SQL)",
        "url": "http://adminer.lh",
        "kind": "database_gui",
        "hint": "Manages ecosystem MySQL/Postgres on lh-network. Stack databases are on an isolated Docker network — use CLI below or publish a host port in Advanced.",
    },
    {
        "id": "redis-ui",
        "label": "Redis Commander",
        "url": "http://redis-ui.lh",
        "kind": "redis_gui",
        "hint": "Inspects ecosystem infra Redis (redis.lh:6379). Stack Redis (e.g. Magento cache) is internal unless published.",
    },
)

def _framework_templates() -> frozenset[str]:
    from dev_stack_frameworks import FRAMEWORK_TEMPLATE_IDS

    return FRAMEWORK_TEMPLATE_IDS


_ADMIN_RESET_DEFAULTS: dict[str, dict[str, str]] = {
    "wordpress": {"username": "admin", "password": "admin"},
    "woocommerce": {"username": "admin", "password": "admin"},
    "magento-min": {"username": "admin", "password": "Admin123!"},
    "magento-full": {"username": "admin", "password": "Admin123!"},
}


def template_http_backend(template: str, stack_id: str) -> tuple[str, int] | None:
    tpl = (template or "").strip().lower()
    if tpl == "magento-min":
        return http_container_name(stack_id, "app"), 8080
    if tpl in ("magento-full", "wordpress", "woocommerce", "joomla", "drupal", "ghost"):
        return http_container_name(stack_id, "app"), 80
    if tpl == "elasticsearch":
        return http_container_name(stack_id, "app"), 9200
    from dev_stack_frameworks import FRAMEWORK_TEMPLATE_IDS

    if tpl in FRAMEWORK_TEMPLATE_IDS:
        ports = {
            "django": 8000,
            "fastapi": 8000,
            "flask": 5000,
            "rails": 3000,
            "nestjs": 3000,
            "express": 3000,
        }
        return http_container_name(stack_id, "app"), ports.get(tpl, 80)
    return None


def _dual_url(host: str, path: str = "/") -> dict[str, str]:
    p = path if path.startswith("/") else f"/{path}" if path else "/"
    http_u = f"http://{host}{p}"
    https_u = f"https://{host}{p}"
    return {"http": http_u, "https": https_u}


def _url_entry(label: str, role: str, host: str, path: str) -> dict[str, str]:
    schemes = _dual_url(host, path)
    return {
        "label": label,
        "role": role,
        "url": schemes["http"],
        "url_http": schemes["http"],
        "url_https": schemes["https"],
    }


def _quick_link(
    link_id: str,
    label: str,
    *,
    url_http: str = "",
    url_https: str = "",
    kind: str = "app",
    hint: str = "",
    primary: bool = False,
) -> dict[str, Any]:
    return {
        "id": link_id,
        "label": label,
        "url": url_http or url_https,
        "url_http": url_http,
        "url_https": url_https,
        "kind": kind,
        "hint": hint,
        "primary": primary,
    }


def _networking_diagram(template: str, host: str, stack_id: str) -> dict[str, Any]:
    """Structured nodes/edges for the Platform card networking illustration."""
    tpl = (template or "").strip().lower()
    nodes: list[dict[str, str]] = [
        {"id": "traefik", "label": "Traefik", "detail": host, "tier": "edge"},
    ]
    edges: list[list[str]] = []

    if tpl == "magento-full":
        nodes.extend(
            [
                {"id": "edge", "label": "Nginx edge", "detail": "lh-network", "tier": "edge"},
                {"id": "varnish", "label": "Varnish", "detail": "cache", "tier": "cache"},
                {"id": "magento", "label": "Magento", "detail": "app :8080", "tier": "app"},
                {"id": "mariadb", "label": "MariaDB", "detail": "10.6", "tier": "data"},
                {"id": "redis", "label": "Redis", "detail": "cache", "tier": "data"},
                {"id": "elasticsearch", "label": "Elasticsearch", "detail": ":9200", "tier": "data"},
            ]
        )
        edges = [
            ["traefik", "edge"],
            ["edge", "varnish"],
            ["varnish", "magento"],
            ["magento", "mariadb"],
            ["magento", "redis"],
            ["magento", "elasticsearch"],
        ]
        return {
            "hostname": host,
            "route_file": "hosting/traefik/20-dev-stacks.yml",
            "nodes": nodes,
            "edges": edges,
            "layers": [
                ["traefik", "edge", "varnish", "magento"],
                ["mariadb", "redis", "elasticsearch"],
            ],
        }
    elif tpl == "magento-min":
        nodes.extend(
            [
                {"id": "magento", "label": "Magento", "detail": "lh-network :8080", "tier": "app"},
                {"id": "mariadb", "label": "MariaDB", "detail": "10.6", "tier": "data"},
            ]
        )
        edges = [["traefik", "magento"], ["magento", "mariadb"]]
    elif tpl in ("wordpress", "woocommerce"):
        nodes.extend(
            [
                {"id": "wordpress", "label": "WordPress", "detail": "lh-network", "tier": "app"},
                {"id": "mysql", "label": "MySQL", "detail": "db", "tier": "data"},
            ]
        )
        edges = [["traefik", "wordpress"], ["wordpress", "mysql"]]
        if tpl == "woocommerce":
            nodes.append({"id": "wc", "label": "WooCommerce", "detail": "plugin", "tier": "app"})
            edges.append(["wordpress", "wc"])
    elif tpl == "joomla":
        nodes.extend(
            [
                {"id": "joomla", "label": "Joomla", "detail": "lh-network", "tier": "app"},
                {"id": "mariadb", "label": "MariaDB", "detail": "db", "tier": "data"},
            ]
        )
        edges = [["traefik", "joomla"], ["joomla", "mariadb"]]
    elif tpl == "drupal":
        nodes.extend(
            [
                {"id": "drupal", "label": "Drupal", "detail": "lh-network", "tier": "app"},
                {"id": "postgres", "label": "PostgreSQL", "detail": "db", "tier": "data"},
            ]
        )
        edges = [["traefik", "drupal"], ["drupal", "postgres"]]
    elif tpl == "ghost":
        nodes.append({"id": "ghost", "label": "Ghost", "detail": "lh-network", "tier": "app"})
        edges = [["traefik", "ghost"]]
    elif tpl == "elasticsearch":
        nodes.append({"id": "elasticsearch", "label": "Elasticsearch", "detail": "lh-network :9200", "tier": "data"})
        edges = [["traefik", "elasticsearch"]]
    elif tpl in _framework_templates():
        label = tpl.replace("-", " ").title()
        nodes.append({"id": "app", "label": label, "detail": "lh-network", "tier": "app"})
        edges = [["traefik", "app"]]
        if tpl in ("django", "rails"):
            nodes.append({"id": "postgres", "label": "PostgreSQL", "detail": "db", "tier": "data"})
            edges.append(["app", "postgres"])
        elif tpl in ("yii2", "symfony", "cakephp", "laravel"):
            nodes.append({"id": "mysql", "label": "MySQL", "detail": "db", "tier": "data"})
            edges.append(["app", "mysql"])
    else:
        from dev_stacks import _compose_services_on_lh_network, _parse_compose_services

        sid = _slugify(stack_id)
        on_lh = _compose_services_on_lh_network(sid)
        svcs = _parse_compose_services(sid)
        app_ids = [s for s in svcs if s in on_lh]
        if not app_ids and svcs:
            app_ids = [next(iter(svcs))]
        for svc in sorted(svcs):
            tier = "app" if svc in on_lh else "data"
            nodes.append({"id": svc, "label": svc, "detail": "lh-network" if svc in on_lh else "internal", "tier": tier})
        if app_ids:
            edges.append(["traefik", app_ids[0]])
            for svc in sorted(svcs):
                if svc not in on_lh and svc != app_ids[0]:
                    edges.append([app_ids[0], svc])

    return {
        "hostname": host,
        "route_file": "hosting/traefik/20-dev-stacks.yml",
        "nodes": nodes,
        "edges": edges,
    }


def _data_stores_for_stack(stack_id: str) -> list[dict[str, Any]]:
    from dev_stacks import _parse_compose_services
    from hosted_app_services import (
        _build_connection_endpoints,
        _extract_credentials,
        _management_uis_for_data_store,
        classify_compose_service,
    )

    sid = _slugify(stack_id)
    items: list[dict[str, Any]] = []
    for sname, spec in _parse_compose_services(sid).items():
        if not isinstance(spec, dict):
            continue
        spec = dict(spec)
        kind = classify_compose_service(sname, spec)
        if kind not in ("mysql", "postgres", "redis", "mongodb", "minio"):
            continue
        env = {k: str(v) for k, v in (spec.get("environment") or {}).items()}
        creds = _extract_credentials(kind, env, sname.split("-")[0])
        if kind == "mysql" and env.get("MARIADB_USER"):
            creds.setdefault("username", env.get("MARIADB_USER", ""))
            creds.setdefault("database", env.get("MARIADB_DATABASE", ""))
            if env.get("ALLOW_EMPTY_PASSWORD", "").lower() in ("yes", "true", "1"):
                creds.setdefault("password", "")
        endpoints = _build_connection_endpoints(kind, sname, spec, creds, [], [])
        gui_links = _management_uis_for_data_store(kind, sname, spec, creds)
        cli = ""
        if kind in ("mysql", "postgres"):
            user = creds.get("username") or creds.get("user") or "root"
            db = creds.get("database") or creds.get("dbname") or ""
            pw = creds.get("password") or ""
            pw_flag = f"-p{pw} " if pw else ""
            if kind == "mysql":
                cli = f"docker compose exec {sname} mysql -u{user} {pw_flag}{db}".strip()
            else:
                cli = f"docker compose exec {sname} psql -U {user} {db}".strip()
        elif kind == "redis":
            cli = f"docker compose exec {sname} redis-cli"
        items.append(
            {
                "name": sname,
                "kind": kind,
                "credentials": creds,
                "connection_endpoints": endpoints,
                "gui_links": gui_links,
                "cli_hint": cli,
                "docker_host": sname,
                "isolated": True,
            }
        )
    return items


def _build_quick_links(info: dict[str, Any], data_stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for u in info.get("urls") or []:
        role = str(u.get("role") or "")
        label = str(u.get("label") or "Open")
        http_u = str(u.get("url_http") or u.get("url") or "")
        https_u = str(u.get("url_https") or http_u)
        kind = "admin" if role == "admin" else "frontend" if role == "frontend" else "app"
        links.append(
            _quick_link(
                f"url-{role}",
                label,
                url_http=http_u,
                url_https=https_u,
                kind=kind,
                primary=role in ("admin", "frontend"),
            )
        )

    for plat in _PLATFORM_GUI_LINKS:
        links.append(
            _quick_link(
                plat["id"],
                plat["label"],
                url_http=plat["url"],
                url_https=plat["url"].replace("http://", "https://", 1),
                kind=plat["kind"],
                hint=plat.get("hint", ""),
            )
        )

    host = str(info.get("hostname") or "")
    template = str(info.get("template") or "")
    if template == "elasticsearch" and host:
        base = _dual_url(host, "/_cluster/health")
        links.append(
            _quick_link(
                "es-health",
                "Elasticsearch API",
                url_http=base["http"],
                url_https=base["https"],
                kind="api",
            )
        )

    seen: set[str] = {str(l.get("id")) for l in links}
    for store in data_stores:
        for gui in store.get("gui_links") or []:
            gid = f"store-{store.get('name')}-{gui.get('label')}"
            if gid in seen:
                continue
            seen.add(gid)
            url = str(gui.get("url") or "")
            links.append(
                _quick_link(
                    gid,
                    str(gui.get("label") or "Database GUI"),
                    url_http=url,
                    url_https=url.replace("http://", "https://", 1) if url.startswith("http://") else url,
                    kind="database_gui",
                    hint=str(store.get("cli_hint") or ""),
                )
            )
    return links


def stack_access_info(stack_id: str) -> dict[str, Any]:
    from dev_stack_routes import load_stack_meta

    sid = _slugify(stack_id)
    meta = load_stack_meta(sid)
    template = str(meta.get("template") or "").strip().lower()
    sample = bool(meta.get("sample_data"))
    host = stack_hostname(sid)
    base_urls = _dual_url(host, "/")

    info: dict[str, Any] = {
        "stack_id": sid,
        "hostname": host,
        "base_url": base_urls["http"],
        "base_urls": base_urls,
        "template": template or None,
        "sample_data": sample,
        "urls": [],
        "credentials": [],
        "notes": [],
    }

    def _cred(label: str, username: str, password: str, path: str, *, role: str = "admin") -> dict[str, str]:
        login = _dual_url(host, path)
        return {
            "label": label,
            "role": role,
            "username": username,
            "password": password,
            "login_url": login["https"],
            "login_url_http": login["http"],
            "login_url_https": login["https"],
        }

    if template in ("wordpress", "woocommerce"):
        info["urls"] = [
            _url_entry("Public site", "frontend", host, "/"),
            _url_entry("WP Admin", "admin", host, "/wp-admin/"),
            _url_entry("Install wizard", "install", host, "/wp-admin/install.php"),
        ]
        if template == "woocommerce":
            info["urls"].append(_url_entry("Shop", "shop", host, "/shop/"))
            info["notes"].append(
                "WooCommerce is installed on first start (wc-setup). Use the stack hostname above, not localhost."
            )
        if sample:
            info["credentials"] = [_cred("WordPress admin", "admin", "admin", "/wp-admin/")]
        else:
            info["notes"].append(
                "No sample data: open the site once, complete the install wizard, or enable sample content when creating the stack."
            )
            info["credentials"] = [
                _cred(
                    "WordPress admin (after install)",
                    "(you choose during install)",
                    "(you choose during install)",
                    "/wp-admin/",
                )
            ]
    elif template == "joomla":
        info["urls"] = [
            _url_entry("Public site", "frontend", host, "/"),
            _url_entry("Administrator", "admin", host, "/administrator/"),
        ]
        info["notes"].append(
            f"Open {info['base_url']} (not localhost). With sample data, Joomla auto-installs on first start."
        )
        if sample:
            info["credentials"] = [
                _cred("Joomla admin", "admin", "localdevpass12", "/administrator/")
            ]
    elif template in ("magento-min", "magento-full"):
        info["urls"] = [
            _url_entry("Storefront", "frontend", host, "/"),
            _url_entry("Admin", "admin", host, "/admin/"),
        ]
        info["credentials"] = [_cred("Magento admin", "admin", "Admin123!", "/admin/")]
        info["notes"].append(f"Store base URL is set to {info['base_url']} (MAGENTO_HOST on first boot).")
        info["notes"].append(
            "Uses bitnamilegacy/magento-archived and bitnamilegacy/mariadb:10.6 (Bitnami removed public catalog images in 2025)."
        )
        if template == "magento-full":
            info["notes"].append(
                "Full stack: Elasticsearch, Redis, Varnish, and Nginx edge in front of Magento. First start can take 15–30 minutes with sample data."
            )
            info["notes"].append(
                "If Magento was created with MariaDB 11, use Reinstall (volumes) so the database is recreated on MariaDB 10.6."
            )
        else:
            info["notes"].append("Minimum stack: MariaDB + Magento only (no Elasticsearch / Varnish).")
        if sample:
            info["notes"].append("Sample catalog data loads when MAGENTO_LOAD_SAMPLE_DATA=yes (first start may take several minutes).")
    elif template == "elasticsearch":
        info["urls"] = [
            _url_entry("Cluster health", "api", host, "/_cluster/health"),
            _url_entry("Root API", "api", host, "/"),
        ]
        info["notes"].append("Single-node Elasticsearch 8.x with security disabled for local dev.")
    elif template == "drupal":
        info["urls"] = [
            _url_entry("Site", "frontend", host, "/"),
            _url_entry("User login", "admin", host, "/user/login"),
        ]
        info["notes"].append(
            f"Complete the installer in the browser at {info['base_url']} (not localhost) so CSS/assets load correctly."
        )
        if sample:
            info["notes"].append("Sample mode sets the site name only; you still complete the Drupal installer once.")
    elif template == "ghost":
        info["urls"] = [
            _url_entry("Ghost site", "frontend", host, "/"),
            _url_entry("Admin", "admin", host, "/ghost/"),
        ]
        info["notes"].append(
            f"Create the owner account in the browser at {info['base_url']} on first visit (Ghost url env is set to this host)."
        )
    elif template in _framework_templates():
        fw = str(meta.get("framework") or template.replace("-", " ").title())
        info["urls"] = [
            _url_entry(f"{fw} app", "frontend", host, "/"),
        ]
        if template == "django":
            info["urls"].append(_url_entry("Django admin", "admin", host, "/admin/"))
            info["notes"].append("Create a superuser: docker compose exec app python manage.py createsuperuser")
        elif template == "laravel":
            info["notes"].append("After boot: docker compose exec app php artisan key:generate if needed.")
        elif template in ("yii2", "symfony", "cakephp"):
            info["notes"].append("PHP framework — document root is the public/ or webroot/ folder inside the app volume.")
        info["notes"].append(
            f"{fw}: first Start runs composer/npm/pip bootstrap in the app container (can take several minutes)."
        )
        info["notes"].append("Watch progress: docker compose logs -f app")
        info["notes"].append(f"Open {info['base_url']} (not localhost) via Traefik.")

    info["networking"] = _networking_diagram(template, host, sid)
    info["data_stores"] = _data_stores_for_stack(sid)
    info["quick_links"] = _build_quick_links(info, info["data_stores"])
    reset_defaults = _ADMIN_RESET_DEFAULTS.get(template)
    info["admin_reset"] = {
        "supported": template in _ADMIN_RESET_DEFAULTS,
        "template": template or None,
        "username": (reset_defaults or {}).get("username"),
        "password": (reset_defaults or {}).get("password"),
    }
    return info


def reset_template_admin(stack_id: str) -> dict[str, Any]:
    """Reset admin credentials for supported ready stacks (requires running containers)."""
    from dev_stack_app_urls import _magento_cli
    from dev_stack_routes import load_stack_meta

    sid = _slugify(stack_id)
    meta = load_stack_meta(sid)
    template = str(meta.get("template") or "").strip().lower()
    compose_dir = STACKS_ROOT / sid
    if not (compose_dir / "docker-compose.yml").is_file():
        return {"ok": False, "error": f"Stack {sid} not found"}

    defaults = _ADMIN_RESET_DEFAULTS.get(template)
    if not defaults:
        return {"ok": False, "error": f"Admin reset not supported for template: {template or 'custom'}"}

    if template in ("wordpress", "woocommerce"):
        cmd = [
            "docker",
            "compose",
            "exec",
            "-T",
            "wordpress",
            "wp",
            "user",
            "update",
            "admin",
            "--user_pass=admin",
            "--allow-root",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(compose_dir))
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return {"ok": False, "error": "wp-cli failed — is the stack running and WordPress installed?", "output": out}
        return {
            "ok": True,
            "message": "WordPress admin password reset to admin / admin",
            "credentials": {"username": "admin", "password": "admin"},
            "output": out.strip(),
        }

    if template in ("magento-min", "magento-full"):
        user = defaults["username"]
        password = defaults["password"]
        logs: list[str] = []
        for cmd in (
            ["admin:user:unlock", user],
            ["admin:user:change-password", user, password],
        ):
            code, out = _magento_cli(sid, *cmd, timeout=180)
            if out:
                logs.append(out)
            if code != 0:
                return {
                    "ok": False,
                    "error": "Magento CLI failed — wait for install to finish, then try again.",
                    "output": "\n".join(logs)[-4000:],
                }
        return {
            "ok": True,
            "message": f"Magento admin password reset to {user} / {password}",
            "credentials": {"username": user, "password": password},
            "output": "\n".join(logs)[-4000:],
        }

    return {"ok": False, "error": f"Admin reset not supported for template: {template or 'custom'}"}

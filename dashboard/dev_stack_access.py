"""URLs, credentials, and admin helpers for ready dev-stack templates."""

from __future__ import annotations

import subprocess
from typing import Any

from dev_stack_compose import STACKS_ROOT, _slugify
from dev_stack_routes import http_container_name, stack_hostname


def template_http_backend(template: str, stack_id: str) -> tuple[str, int] | None:
    tpl = (template or "").strip().lower()
    if tpl == "magento-min":
        return http_container_name(stack_id, "app"), 8080
    if tpl in ("magento-full", "wordpress", "woocommerce", "joomla", "drupal", "ghost"):
        return http_container_name(stack_id, "app"), 80
    if tpl == "elasticsearch":
        return http_container_name(stack_id, "app"), 9200
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

    def _cred(label: str, username: str, password: str, path: str) -> dict[str, str]:
        login = _dual_url(host, path)
        return {
            "label": label,
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
            "Uses bitnamilegacy/magento-archived and bitnamilegacy/mariadb (Bitnami removed public catalog images in 2025)."
        )
        if template == "magento-full":
            info["notes"].append(
                "Full stack: Elasticsearch, Redis, Varnish, and Nginx edge in front of Magento. First start can take 10+ minutes."
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

    return info


def reset_template_admin(stack_id: str) -> dict[str, Any]:
    """Reset admin credentials for supported ready stacks (requires running containers)."""
    from dev_stack_routes import load_stack_meta

    sid = _slugify(stack_id)
    meta = load_stack_meta(sid)
    template = str(meta.get("template") or "").strip().lower()
    compose_dir = STACKS_ROOT / sid
    if not (compose_dir / "docker-compose.yml").is_file():
        return {"ok": False, "error": f"Stack {sid} not found"}

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
    return {"ok": False, "error": f"Admin reset not supported for template: {template or 'custom'}"}

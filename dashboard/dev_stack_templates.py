"""Ready dev-stack templates and preset catalog loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from dev_stack_compose import NETWORK_EXTERNAL, STACKS_ROOT, _slugify, register_stack_in_platform
from dev_stack_app_urls import stack_public_host, stack_public_url
from dev_stack_routes import http_container_name
from platform_config import _PROJECT_ROOT

_PRESETS_FILE = _PROJECT_ROOT / "ecosystem-stack" / "config" / "dev-stack-presets.yaml"

from dev_stack_images import MAGENTO_APP_IMAGE, MAGENTO_DB_IMAGE


def load_dev_stack_presets() -> dict[str, Any]:
    if not _PRESETS_FILE.is_file():
        return {"groups": [], "presets": {}}
    raw = yaml.safe_load(_PRESETS_FILE.read_text(encoding="utf-8")) or {}
    presets = raw.get("presets") if isinstance(raw.get("presets"), dict) else {}
    groups = raw.get("groups") if isinstance(raw.get("groups"), list) else []
    return {"groups": groups, "presets": presets}


def get_preset(preset_key: str) -> dict[str, Any] | None:
    presets = load_dev_stack_presets().get("presets") or {}
    row = presets.get(preset_key)
    return dict(row) if isinstance(row, dict) else None


def preset_catalog_for_api() -> dict[str, Any]:
    data = load_dev_stack_presets()
    groups = data.get("groups") or []
    presets = data.get("presets") or {}
    out_presets: dict[str, Any] = {}
    for key, row in presets.items():
        if not isinstance(row, dict):
            continue
        out_presets[key] = {
            "key": key,
            "group": row.get("group") or "infrastructure",
            "label": row.get("label") or key,
            "id": row.get("id") or key,
            "name": row.get("name") or row.get("label") or key,
            "template": row.get("template"),
            "supports_sample_data": bool(row.get("supports_sample_data")),
            "components": row.get("components") or [],
        }
    return {"groups": groups, "presets": out_presets}


def _write_stack_files(sid: str, files: dict[str, str]) -> None:
    stack_dir = STACKS_ROOT / _slugify(sid)
    for rel, content in files.items():
        path = stack_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _write_stack(stack_id: str, name: str, compose: dict[str, Any], meta: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    from dev_stack_images import normalize_compose_images, verify_compose_images

    sid = _slugify(stack_id)
    stack_dir = STACKS_ROOT / sid
    stack_dir.mkdir(parents=True, exist_ok=True)
    compose, _ = normalize_compose_images(compose)
    image_errors = verify_compose_images(compose, skip_registry=True)
    if image_errors:
        raise ValueError("Unavailable container images:\n" + "\n".join(f"  • {e}" for e in image_errors))
    compose_path = stack_dir / "docker-compose.yml"
    compose_path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    meta = dict(meta)
    meta["id"] = sid
    meta["name"] = name or sid
    meta["project"] = f"leco-devstack-{sid}"
    meta["internal_network"] = f"leco-devstack-{sid}-internal"
    (stack_dir / "stack.yaml").write_text(yaml.safe_dump(meta, sort_keys=False), encoding="utf-8")
    return compose_path, meta


def _base_compose(sid: str, services: dict[str, Any], volumes: dict[str, Any] | None = None) -> dict[str, Any]:
    internal_net = f"leco-devstack-{sid}-internal"
    return {
        "name": f"leco-devstack-{sid}",
        "services": services,
        "networks": {
            internal_net: {"driver": "bridge"},
            NETWORK_EXTERNAL: {"external": True},
        },
        "volumes": volumes or {},
    }


def _mysql_db(
    internal_net: str,
    *,
    db_name: str = "appdb",
    user: str = "app",
    password: str = "localdev",
    volume: str = "db_data",
) -> dict[str, Any]:
    return {
        "image": "mysql:8.0",
        "restart": "unless-stopped",
        "environment": {
            "MYSQL_ROOT_PASSWORD": password,
            "MYSQL_DATABASE": db_name,
            "MYSQL_USER": user,
            "MYSQL_PASSWORD": password,
        },
        "volumes": [f"{volume}:/var/lib/mysql"],
        "networks": [internal_net],
        "healthcheck": {
            "test": ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "-uroot", f"-p{password}"],
            "interval": "5s",
            "timeout": "5s",
            "retries": 12,
        },
    }


def _mariadb_db(internal_net: str, *, db_name: str = "appdb", password: str = "localdev") -> dict[str, Any]:
    return {
        "image": "mariadb:11",
        "restart": "unless-stopped",
        "environment": {
            "MARIADB_ROOT_PASSWORD": password,
            "MARIADB_DATABASE": db_name,
            "MARIADB_USER": "app",
            "MARIADB_PASSWORD": password,
        },
        "volumes": ["db_data:/var/lib/mysql"],
        "networks": [internal_net],
    }


def _postgres_db(internal_net: str, *, db_name: str = "appdb", password: str = "localdev") -> dict[str, Any]:
    return {
        "image": "postgres:16-alpine",
        "restart": "unless-stopped",
        "environment": {
            "POSTGRES_USER": "postgres",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_DB": db_name,
        },
        "volumes": ["db_data:/var/lib/postgresql/data"],
        "networks": [internal_net],
    }


def _wordpress_wp_cli_service(internal_net: str, *, db_name: str, password: str) -> dict[str, Any]:
    return {
        "image": "wordpress:cli",
        "profiles": ["cli"],
        "depends_on": ["db", "wordpress"],
        "environment": {
            "WORDPRESS_DB_HOST": "db",
            "WORDPRESS_DB_USER": "app",
            "WORDPRESS_DB_PASSWORD": password,
            "WORDPRESS_DB_NAME": db_name,
        },
        "volumes": ["wp_data:/var/www/html"],
        "networks": [internal_net],
        "entrypoint": ["wp"],
        "command": ["--info"],
    }


def _template_wordpress(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    db_name = "wordpress"
    password = "localdev"
    wp_url = stack_public_url(sid)
    wp_config_extra = f"define('WP_HOME', '{wp_url}'); define('WP_SITEURL', '{wp_url}');"
    services: dict[str, Any] = {
        "db": _mysql_db(internal_net, db_name=db_name, password=password),
        "wordpress": {
            "image": "wordpress:6.7-apache",
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "depends_on": {"db": {"condition": "service_healthy"}},
            "environment": {
                "WORDPRESS_DB_HOST": "db",
                "WORDPRESS_DB_USER": "app",
                "WORDPRESS_DB_PASSWORD": password,
                "WORDPRESS_DB_NAME": db_name,
                "WORDPRESS_CONFIG_EXTRA": wp_config_extra,
            },
            "volumes": ["wp_data:/var/www/html"],
            "networks": [internal_net, NETWORK_EXTERNAL],
        },
        "wp-cli": _wordpress_wp_cli_service(internal_net, db_name=db_name, password=password),
    }
    if sample_data:
        wp_init = (
            "set -e; "
            "for i in $(seq 1 60); do wp db check 2>/dev/null && break; sleep 2; done; "
            f"wp core is-installed || wp core install --url={wp_url} "
            "--title='LEco WordPress Demo' --admin_user=admin --admin_password=admin "
            "--admin_email=demo@local.test --skip-email; "
            f"wp option update siteurl '{wp_url}'; "
            f"wp option update home '{wp_url}'; "
            "wp rewrite flush; "
            "COUNT=$(wp post list --post_type=post --format=count 2>/dev/null || echo 0); "
            '[ "$COUNT" = "0" ] && wp post generate --count=10 --post_status=publish || true'
        )
        services["wp-sample-init"] = {
            "image": "wordpress:cli",
            "depends_on": ["db", "wordpress"],
            "environment": {
                "WORDPRESS_DB_HOST": "db",
                "WORDPRESS_DB_USER": "app",
                "WORDPRESS_DB_PASSWORD": password,
                "WORDPRESS_DB_NAME": db_name,
            },
            "volumes": ["wp_data:/var/www/html"],
            "networks": [internal_net],
            "restart": "no",
            "entrypoint": ["/bin/sh", "-c"],
            "command": [wp_init],
        }
    compose = _base_compose(sid, services, {"db_data": {}, "wp_data": {}})
    meta = {
        "template": "wordpress",
        "sample_data": sample_data,
        "components": [
            {"id": "mysql", "version": "8.0"},
            {"id": "wordpress", "version": "6.7"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_woocommerce(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    path, meta = _template_wordpress(sid, name, sample_data=sample_data)
    internal_net = f"leco-devstack-{sid}-internal"
    password = "localdev"
    db_name = "wordpress"
    compose = yaml.safe_load((STACKS_ROOT / _slugify(sid) / "docker-compose.yml").read_text(encoding="utf-8"))
    wc_setup = (
        "set -e; "
        "for i in $(seq 1 90); do wp core is-installed 2>/dev/null && break; sleep 3; done; "
        "wp core is-installed; "
        "wp plugin is-installed woocommerce 2>/dev/null || wp plugin install woocommerce --activate; "
        "wp plugin is-active woocommerce || wp plugin activate woocommerce; "
    )
    if sample_data:
        wc_setup += "wp wc tool run install_pages 2>/dev/null || true; "
    compose["services"]["wc-setup"] = {
        "image": "wordpress:cli",
        "depends_on": ["db", "wordpress"],
        "environment": {
            "WORDPRESS_DB_HOST": "db",
            "WORDPRESS_DB_USER": "app",
            "WORDPRESS_DB_PASSWORD": password,
            "WORDPRESS_DB_NAME": db_name,
        },
        "volumes": ["wp_data:/var/www/html"],
        "networks": [internal_net],
        "restart": "no",
        "entrypoint": ["/bin/sh", "-c"],
        "command": [wc_setup],
    }
    (STACKS_ROOT / _slugify(sid) / "docker-compose.yml").write_text(
        yaml.safe_dump(compose, sort_keys=False), encoding="utf-8"
    )
    meta["template"] = "woocommerce"
    meta["components"].append({"id": "woocommerce", "version": "latest"})
    return path, meta


def _template_joomla(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    db_name = "joomla"
    password = "localdev"
    services: dict[str, Any] = {
        "db": _mariadb_db(internal_net, db_name=db_name, password=password),
        "joomla": {
            "image": "joomla:5.2-apache",
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "depends_on": ["db"],
            "environment": {
                "JOOMLA_DB_HOST": "db",
                "JOOMLA_DB_USER": "app",
                "JOOMLA_DB_PASSWORD": password,
                "JOOMLA_DB_NAME": db_name,
            },
            "volumes": ["joomla_data:/var/www/html"],
            "networks": [internal_net, NETWORK_EXTERNAL],
        },
    }
    if sample_data:
        services["joomla"]["environment"].update(
            {
                "JOOMLA_SITE_NAME": "LEco Joomla Demo",
                "JOOMLA_ADMIN_USER": "Admin User",
                "JOOMLA_ADMIN_USERNAME": "admin",
                "JOOMLA_ADMIN_PASSWORD": "localdevpass12",
                "JOOMLA_ADMIN_EMAIL": "admin@local.test",
            }
        )
    compose = _base_compose(sid, services, {"db_data": {}, "joomla_data": {}})
    meta = {
        "template": "joomla",
        "sample_data": sample_data,
        "components": [
            {"id": "mariadb", "version": "11"},
            {"id": "joomla", "version": "5.2"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _magento_app_env(sid: str, *, sample_data: bool, with_search_cache: bool) -> dict[str, str]:
    env = {
        "MAGENTO_HOST": stack_public_host(sid),
        "MAGENTO_ENABLE_HTTPS": "no",
        "MAGENTO_MODE": "developer",
        "MAGENTO_USERNAME": "admin",
        "MAGENTO_PASSWORD": "Admin123!",
        "MAGENTO_EMAIL": "admin@local.test",
        "MAGENTO_FIRST_NAME": "Admin",
        "MAGENTO_LAST_NAME": "User",
        "MAGENTO_LOAD_SAMPLE_DATA": "yes" if sample_data else "no",
        "ALLOW_EMPTY_PASSWORD": "yes",
        "MAGENTO_DATABASE_HOST": "mariadb",
        "MAGENTO_DATABASE_PORT_NUMBER": "3306",
        "MARIADB_HOST": "mariadb",
        "MARIADB_PORT_NUMBER": "3306",
        # Surfaces install failures in container logs during dev-stack Start.
        "BITNAMI_DEBUG": "true",
    }
    if with_search_cache:
        env.update(
            {
                "MAGENTO_ELASTICSEARCH_HOST": "elasticsearch",
                "MAGENTO_ELASTICSEARCH_PORT_NUMBER": "9200",
                "MAGENTO_REDIS_HOST": "redis",
                "MAGENTO_REDIS_PORT_NUMBER": "6379",
                "MAGENTO_USE_REDIS_REMOTE_CACHE": "yes",
            }
        )
    return env


MAGENTO_FULL_VARNISH_VCL = """vcl 4.1;
backend magento_app {
  .host = "magento";
  .port = "8080";
}
sub vcl_recv {
  return (hash);
}
"""

# $$ escapes Compose env interpolation; nginx receives $host, $remote_addr, etc.
MAGENTO_FULL_NGINX_EDGE_CONF = """server {
  listen 80;
  server_name _;
  location / {
    proxy_pass http://varnish:80;
    proxy_set_header Host $$host;
    proxy_set_header X-Real-IP $$remote_addr;
    proxy_set_header X-Forwarded-For $$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $$scheme;
  }
}
"""


def magento_full_edge_configs() -> dict[str, Any]:
    """Inline Compose configs — avoids host bind mounts (Docker Desktop file sharing)."""
    return {
        "varnish_vcl": {"content": MAGENTO_FULL_VARNISH_VCL.rstrip() + "\n"},
        "nginx_edge_conf": {"content": MAGENTO_FULL_NGINX_EDGE_CONF.rstrip() + "\n"},
    }


def _template_magento_min(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    """Minimum: MariaDB + Bitnami Magento (no Elasticsearch / Varnish / Nginx edge)."""
    internal_net = f"leco-devstack-{sid}-internal"
    services: dict[str, Any] = {
        "mariadb": {
            "image": MAGENTO_DB_IMAGE,
            "restart": "unless-stopped",
            "environment": {
                "ALLOW_EMPTY_PASSWORD": "yes",
                "MARIADB_USER": "bn_magento",
                "MARIADB_DATABASE": "bitnami_magento",
            },
            "volumes": ["magento_db:/bitnami/mariadb"],
            "networks": [internal_net],
        },
        "magento": {
            "image": MAGENTO_APP_IMAGE,
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "depends_on": ["mariadb"],
            "environment": _magento_app_env(sid, sample_data=sample_data, with_search_cache=False),
            "volumes": ["magento_data:/bitnami/magento"],
            "networks": [internal_net, NETWORK_EXTERNAL],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    "test -x /bitnami/magento/bin/magento && /bitnami/magento/bin/magento setup:db:status >/dev/null 2>&1",
                ],
                "interval": "30s",
                "timeout": "10s",
                "retries": 40,
                "start_period": "1200s",
            },
        },
    }
    compose = _base_compose(sid, services, {"magento_db": {}, "magento_data": {}})
    meta = {
        "template": "magento-min",
        "sample_data": sample_data,
        "variant": "minimum",
        "components": [
            {"id": "mariadb", "version": "11"},
            {"id": "magento", "version": "2"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_magento_full(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    """Full: MariaDB, Redis, Elasticsearch, Magento, Varnish, Nginx edge (production-like)."""
    internal_net = f"leco-devstack-{sid}-internal"
    _write_stack_files(
        sid,
        {
            "varnish/default.vcl": MAGENTO_FULL_VARNISH_VCL,
            "nginx/default.conf": MAGENTO_FULL_NGINX_EDGE_CONF,
        },
    )
    services: dict[str, Any] = {
        "elasticsearch": {
            "image": "docker.elastic.co/elasticsearch/elasticsearch:8.15.0",
            "restart": "unless-stopped",
            "environment": {
                "discovery.type": "single-node",
                "xpack.security.enabled": "false",
                "ES_JAVA_OPTS": "-Xms512m -Xmx512m",
            },
            "volumes": ["es_data:/usr/share/elasticsearch/data"],
            "networks": [internal_net],
            "healthcheck": {
                "test": ["CMD-SHELL", "curl -sf http://127.0.0.1:9200/_cluster/health || exit 1"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 12,
            },
        },
        "redis": {
            "image": "redis:7-alpine",
            "restart": "unless-stopped",
            "command": ["redis-server", "--appendonly", "yes"],
            "volumes": ["redis_data:/data"],
            "networks": [internal_net],
        },
        "mariadb": {
            "image": MAGENTO_DB_IMAGE,
            "restart": "unless-stopped",
            "environment": {
                "ALLOW_EMPTY_PASSWORD": "yes",
                "MARIADB_USER": "bn_magento",
                "MARIADB_DATABASE": "bitnami_magento",
            },
            "volumes": ["magento_db:/bitnami/mariadb"],
            "networks": [internal_net],
        },
        "magento": {
            "image": MAGENTO_APP_IMAGE,
            "restart": "unless-stopped",
            "depends_on": {
                "mariadb": {"condition": "service_started"},
                "redis": {"condition": "service_started"},
                "elasticsearch": {"condition": "service_healthy"},
            },
            "environment": _magento_app_env(sid, sample_data=sample_data, with_search_cache=True),
            "volumes": ["magento_data:/bitnami/magento"],
            "networks": [internal_net],
            "healthcheck": {
                "test": [
                    "CMD-SHELL",
                    "test -x /bitnami/magento/bin/magento && /bitnami/magento/bin/magento setup:db:status >/dev/null 2>&1",
                ],
                "interval": "30s",
                "timeout": "10s",
                "retries": 40,
                "start_period": "1200s",
            },
        },
        "varnish": {
            "image": "varnish:7.4",
            "restart": "unless-stopped",
            "depends_on": {"magento": {"condition": "service_started"}},
            "configs": [{"source": "varnish_vcl", "target": "/etc/varnish/default.vcl"}],
            "networks": [internal_net],
        },
        "edge": {
            "image": "nginx:alpine",
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "depends_on": {"varnish": {"condition": "service_started"}},
            "configs": [{"source": "nginx_edge_conf", "target": "/etc/nginx/conf.d/default.conf"}],
            "networks": [internal_net, NETWORK_EXTERNAL],
        },
    }
    compose = _base_compose(
        sid,
        services,
        {"es_data": {}, "redis_data": {}, "magento_db": {}, "magento_data": {}},
    )
    compose["configs"] = magento_full_edge_configs()
    meta = {
        "template": "magento-full",
        "sample_data": sample_data,
        "variant": "full",
        "components": [
            {"id": "elasticsearch", "version": "8.15"},
            {"id": "redis", "version": "7"},
            {"id": "mariadb", "version": "11"},
            {"id": "magento", "version": "2"},
            {"id": "varnish", "version": "7.4"},
            {"id": "nginx", "version": "alpine"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_elasticsearch(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    """Standalone Elasticsearch node (HTTP API on port 9200)."""
    internal_net = f"leco-devstack-{sid}-internal"
    services: dict[str, Any] = {
        "elasticsearch": {
            "image": "docker.elastic.co/elasticsearch/elasticsearch:8.15.0",
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "environment": {
                "discovery.type": "single-node",
                "xpack.security.enabled": "false",
                "ES_JAVA_OPTS": "-Xms512m -Xmx512m",
            },
            "volumes": ["es_data:/usr/share/elasticsearch/data"],
            "networks": [internal_net, NETWORK_EXTERNAL],
            "healthcheck": {
                "test": ["CMD-SHELL", "curl -sf http://127.0.0.1:9200/_cluster/health || exit 1"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 12,
            },
        },
    }
    compose = _base_compose(sid, services, {"es_data": {}})
    meta = {
        "template": "elasticsearch",
        "sample_data": False,
        "components": [{"id": "elasticsearch", "version": "8.15"}],
    }
    return _write_stack(sid, name, compose, meta)


def _template_drupal(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    db_name = "drupal"
    password = "localdev"
    services: dict[str, Any] = {
        "db": _postgres_db(internal_net, db_name=db_name, password=password),
        "drupal": {
            "image": "drupal:10-apache",
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "depends_on": ["db"],
            "environment": {
                "DRUPAL_DB_HOST": "db",
                "DRUPAL_DB_USER": "postgres",
                "DRUPAL_DB_PASSWORD": password,
                "DRUPAL_DB_NAME": db_name,
                **(
                    {"DRUPAL_SITE_NAME": "LEco Drupal Demo"}
                    if sample_data
                    else {}
                ),
            },
            "volumes": ["drupal_data:/var/www/html"],
            "networks": [internal_net, NETWORK_EXTERNAL],
        },
    }
    compose = _base_compose(sid, services, {"db_data": {}, "drupal_data": {}})
    meta = {
        "template": "drupal",
        "sample_data": sample_data,
        "components": [
            {"id": "postgres", "version": "16"},
            {"id": "drupal", "version": "10"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


def _template_ghost(sid: str, name: str, *, sample_data: bool) -> tuple[Path, dict[str, Any]]:
    internal_net = f"leco-devstack-{sid}-internal"
    db_name = "ghost"
    password = "localdev"
    services: dict[str, Any] = {
        "db": _mysql_db(internal_net, db_name=db_name, password=password),
        "ghost": {
            "image": "ghost:5-alpine",
            "container_name": http_container_name(sid, "app"),
            "restart": "unless-stopped",
            "depends_on": ["db"],
            "environment": {
                "url": stack_public_url(sid),
                "database__client": "mysql",
                "database__connection__host": "db",
                "database__connection__user": "app",
                "database__connection__password": password,
                "database__connection__database": db_name,
            },
            "volumes": ["ghost_data:/var/lib/ghost/content"],
            "networks": [internal_net, NETWORK_EXTERNAL],
        },
    }
    if sample_data:
        services["ghost"]["environment"]["NODE_ENV"] = "production"
    compose = _base_compose(sid, services, {"db_data": {}, "ghost_data": {}})
    meta = {
        "template": "ghost",
        "sample_data": sample_data,
        "components": [
            {"id": "mysql", "version": "8.0"},
            {"id": "ghost", "version": "5"},
        ],
    }
    return _write_stack(sid, name, compose, meta)


_TEMPLATES: dict[str, Any] = {
    "wordpress": _template_wordpress,
    "woocommerce": _template_woocommerce,
    "joomla": _template_joomla,
    "magento-min": _template_magento_min,
    "magento-full": _template_magento_full,
    "elasticsearch": _template_elasticsearch,
    "drupal": _template_drupal,
    "ghost": _template_ghost,
}


def generate_from_template(
    stack_id: str,
    name: str,
    template_id: str,
    *,
    sample_data: bool = False,
) -> tuple[Path, dict[str, Any]]:
    fn = _TEMPLATES.get(template_id)
    if not fn:
        from dev_stack_frameworks import FRAMEWORK_TEMPLATES

        fn = FRAMEWORK_TEMPLATES.get(template_id)
    if not fn:
        raise ValueError(f"Unknown dev stack template: {template_id}")
    return fn(_slugify(stack_id), name, sample_data=sample_data)


def create_from_preset(
    preset_key: str,
    stack_id: str | None = None,
    name: str | None = None,
    *,
    sample_data: bool = False,
) -> tuple[Path, dict[str, Any]]:
    preset = get_preset(preset_key)
    if not preset:
        raise ValueError(f"Unknown preset: {preset_key}")
    sid = _slugify(stack_id or preset.get("id") or preset_key)
    stack_name = name or preset.get("name") or sid
    template = preset.get("template")
    if template:
        return generate_from_template(sid, stack_name, str(template), sample_data=sample_data)
    from dev_stack_compose import generate_compose

    components = preset.get("components") or []
    return generate_compose(sid, stack_name, components)

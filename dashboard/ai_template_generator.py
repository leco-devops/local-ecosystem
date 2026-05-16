"""
Deterministic config file generators from AI analysis JSON.

Takes the structured analysis from the AI and produces valid LEco config
files using Python templates.  The AI does understanding; this module
does file generation.  No raw AI text hits disk.
"""

from __future__ import annotations

import json
from typing import Any

import yaml


def generate_from_analysis(
    analysis: dict[str, Any],
    slug: str,
    source_path: str,
    *,
    health_path: str | None = None,
) -> dict[str, str]:
    """Generate all LEco config files from an AI analysis dict.

    Returns a dict mapping filename → file content (all strings).
    """
    files: dict[str, str] = {}

    hp = health_path or analysis.get("health_endpoint") or "/health"
    port = analysis.get("listening_port", 3000)
    cache = analysis.get("cache_layer")
    data_stores = analysis.get("data_stores", [])
    services = analysis.get("services", [])
    config_file = analysis.get("config_file")
    patches = analysis.get("config_keys_to_patch", {})
    entry_scripts = analysis.get("entry_scripts", [])
    uses_chromium = analysis.get("uses_chromium", False)
    node_version = analysis.get("node_version", "20")
    env_vars = analysis.get("environment_vars", [])

    # Determine backend for Traefik routing
    if cache == "varnish":
        backend_host = f"{slug}-varnish"
        backend_port = 80
    elif cache == "nginx":
        backend_host = f"{slug}-nginx"
        backend_port = 80
    else:
        backend_host = f"{slug}-server"
        backend_port = port

    files["leco.yaml"] = _gen_leco_yaml(slug, backend_host, backend_port, port, hp)
    files["leco.app.yaml"] = _gen_leco_app_yaml(slug, config_file, source_path, analysis)
    files["docker-compose.yml"] = _gen_docker_compose(slug, source_path, data_stores, services, cache, port, hp, node_version)
    files["docker-compose.leco-hosting.yml"] = _gen_hosting_overlay(
        slug, source_path, services, patches, env_vars, uses_chromium, config_file,
        cache=cache, health_path=hp, port=port,
    )

    if config_file and patches:
        files["leco-docker-preload.js"] = _gen_preloader(slug, config_file, patches)

    if cache == "varnish":
        files["conf/varnish/default.vcl"] = _gen_varnish_vcl(slug, port, hp)

    return files


# ---------------------------------------------------------------------------
# leco.yaml (localhost profile)
# ---------------------------------------------------------------------------

def _gen_leco_yaml(slug: str, backend_host: str, backend_port: int, app_port: int, health_path: str) -> str:
    profile = {
        "schemaVersion": 2,
        "archetype": "node",
        "infrastructure": {
            "dockerCompose": {
                "composeFile": "docker-compose.yml",
                "projectName": slug,
                "additionalComposeFilesFromManifest": ["docker-compose.leco-hosting.yml"],
            },
            "routing": {
                "entries": [
                    {
                        "hostname": f"{slug}.lh",
                        "backendHost": backend_host,
                        "backendPort": backend_port,
                    }
                ]
            },
            "healthcheckUrls": [f"http://{slug}-server:{app_port}{health_path}"],
        },
        "urls": [
            {"role": "api", "label": "Main server (HTTPS)", "publicUrl": f"https://{slug}.lh{health_path}"},
            {"role": "api", "label": "Main server (HTTP)", "publicUrl": f"http://{slug}.lh{health_path}"},
        ],
        "lifecycle": {"prepare": [], "build": [], "preStart": []},
        "notes": f"AI-generated profile for {slug}. Review and adjust before deploying.",
    }
    return yaml.dump(profile, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# leco.app.yaml (bridge manifest)
# ---------------------------------------------------------------------------

def _gen_leco_app_yaml(slug: str, config_file: str | None, source_path: str, analysis: dict) -> str:
    manifest: dict[str, Any] = {
        "lecoAppVersion": "3",
        "name": slug,
        "root": ".",
        "localHostProfile": "leco.yaml",
    }
    refs: dict[str, str] = {
        "dockerComposeFile": "docker-compose.yml",
    }
    if config_file:
        refs["configFile"] = f"{source_path}/{config_file}" if source_path != "." else config_file
    refs["packageJson"] = f"{source_path}/package.json" if source_path != "." else "package.json"
    manifest["configRefs"] = refs
    manifest["localhost"] = {
        "notes": f"[AI-generated bridge for {slug}]\n\n"
                 f"  Description: {analysis.get('description', 'N/A')}\n"
                 f"  Services: {', '.join(s.get('name', '?') for s in analysis.get('services', []))}\n"
                 f"  Data stores: {', '.join(analysis.get('data_stores', []))}\n"
                 f"  Cache: {analysis.get('cache_layer', 'none')}\n"
    }
    return yaml.dump(manifest, default_flow_style=False, sort_keys=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# docker-compose.yml
# ---------------------------------------------------------------------------

def _gen_docker_compose(
    slug: str,
    source_path: str,
    data_stores: list[str],
    services: list[dict],
    cache: str | None,
    port: int,
    health_path: str,
    node_version: str,
) -> str:
    lines = [
        "# AI-generated Docker Compose for LEco DevOps hosting.",
        "# Review: source paths, images, healthchecks, volume names.",
        "",
        "services:",
    ]

    network = f"{slug}-network"
    volumes_needed: list[str] = []

    # Data stores
    if "mongodb" in data_stores:
        volumes_needed.append(f"{slug}_mongo_data")
        lines += [
            "",
            "  mongo:",
            "    image: mongo:7.0",
            f"    container_name: {slug}-mongo",
            "    restart: unless-stopped",
            "    volumes:",
            f"      - {slug}_mongo_data:/data/db",
            "    networks:",
            f"      - {network}",
            "    healthcheck:",
            "      test: echo 'db.runCommand(\"ping\").ok' | mongosh localhost:27017 --quiet",
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 5",
        ]

    if "redis" in data_stores:
        volumes_needed.append(f"{slug}_redis_data")
        lines += [
            "",
            "  redis:",
            "    image: redis:7-alpine",
            f"    container_name: {slug}-redis",
            "    restart: unless-stopped",
            "    volumes:",
            f"      - {slug}_redis_data:/data",
            "    networks:",
            f"      - {network}",
            "    healthcheck:",
            "      test: redis-cli ping | grep -q PONG",
            "      interval: 10s",
            "      timeout: 3s",
            "      retries: 5",
        ]

    if "postgresql" in data_stores:
        volumes_needed.append(f"{slug}_pg_data")
        lines += [
            "",
            "  postgres:",
            "    image: postgres:16-alpine",
            f"    container_name: {slug}-postgres",
            "    restart: unless-stopped",
            "    environment:",
            f"      POSTGRES_DB: {slug}",
            "      POSTGRES_USER: postgres",
            "      POSTGRES_PASSWORD: postgres",
            "    volumes:",
            f"      - {slug}_pg_data:/var/lib/postgresql/data",
            "    networks:",
            f"      - {network}",
            "    healthcheck:",
            "      test: pg_isready -U postgres",
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 5",
        ]

    if "mysql" in data_stores:
        volumes_needed.append(f"{slug}_mysql_data")
        lines += [
            "",
            "  mysql:",
            "    image: mysql:8.0",
            f"    container_name: {slug}-mysql",
            "    restart: unless-stopped",
            "    environment:",
            f"      MYSQL_DATABASE: {slug}",
            "      MYSQL_ROOT_PASSWORD: root",
            "    volumes:",
            f"      - {slug}_mysql_data:/var/lib/mysql",
            "    networks:",
            f"      - {network}",
            "    healthcheck:",
            "      test: mysqladmin ping -h localhost",
            "      interval: 10s",
            "      timeout: 5s",
            "      retries: 5",
        ]

    # Varnish cache
    if cache == "varnish":
        lines += [
            "",
            "  varnish:",
            "    image: varnish:7.6-alpine",
            f"    container_name: {slug}-varnish",
            "    restart: unless-stopped",
            "    volumes:",
            "      - ./conf/varnish/default.vcl:/etc/varnish/default.vcl:ro",
            "    environment:",
            "      VARNISH_SIZE: 256m",
            "    depends_on:",
            "      server:",
            "        condition: service_healthy",
            "    networks:",
            f"      - {network}",
            "    healthcheck:",
            f'      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:80{health_path} || exit 1"]',
            "      interval: 15s",
            "      timeout: 5s",
            "      retries: 5",
            "      start_period: 60s",
        ]

    # Node.js services
    volumes_needed.append(f"{slug}_node_modules")
    dep_services = []
    if "mongodb" in data_stores:
        dep_services.append("mongo")
    if "redis" in data_stores:
        dep_services.append("redis")
    if "postgresql" in data_stores:
        dep_services.append("postgres")
    if "mysql" in data_stores:
        dep_services.append("mysql")

    for svc in services:
        svc_name = svc.get("name", "server")
        is_primary = svc_name == "server" or svc.get("type") == "http"
        lines += [
            "",
            f"  {svc_name}:",
            f"    image: node:{node_version}-bookworm",
            f"    container_name: {slug}-{svc_name}",
            "    restart: unless-stopped",
            "    working_dir: /app",
            "    volumes:",
            f"      - {source_path}:/app",
            f"      - {slug}_node_modules:/app/node_modules",
            "    environment:",
            "      NODE_ENV: development",
        ]
        if dep_services:
            lines.append("    depends_on:")
            for dep in dep_services:
                lines += [
                    f"      {dep}:",
                    "        condition: service_healthy",
                ]
        lines += [
            "    networks:",
            f"      - {network}",
        ]
        if is_primary and svc.get("port"):
            lines += [
                "    healthcheck:",
                f'      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:{port}{health_path} || exit 1"]',
                "      interval: 10s",
                "      timeout: 5s",
                "      retries: 12",
                "      start_period: 180s",
            ]
        lines.append(f"    # Command overridden in docker-compose.leco-hosting.yml")

    # Volumes
    lines += ["", "volumes:"]
    for v in volumes_needed:
        lines += [f"  {v}:", "    driver: local"]

    # Networks
    lines += ["", "networks:", f"  {network}:", "    driver: bridge"]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# docker-compose.leco-hosting.yml (hosting overlay)
# ---------------------------------------------------------------------------

def _gen_hosting_overlay(
    slug: str,
    source_path: str,
    services: list[dict],
    patches: dict[str, Any],
    env_vars: list[str],
    uses_chromium: bool,
    config_file: str | None,
    *,
    cache: str | None = None,
    health_path: str = "/health",
    port: int = 3000,
) -> str:
    lines = [
        "# AI-generated LEco hosting overlay.",
        "# Adds lh-network, preloader mount, LECO_* env vars, and start commands.",
        "",
        "services:",
    ]

    network = f"{slug}-network"

    for svc in services:
        svc_name = svc.get("name", "server")
        entry = svc.get("entry_script", "server.js")
        is_primary = svc_name == "server" or svc.get("type") == "http"

        lines += [
            "",
            f"  {svc_name}:",
            "    volumes:",
            "      - ./leco-docker-preload.js:/opt/leco/leco-docker-preload.js:ro",
            "    environment:",
        ]

        # LECO_* env vars from config patches
        for key, patch in patches.items():
            docker_val = patch.get("docker_value", "") if isinstance(patch, dict) else str(patch)
            lines.append(f"      LECO_{key}: \"{docker_val}\"")

        if uses_chromium:
            lines.append("      PUPPETEER_EXECUTABLE_PATH: /usr/bin/chromium")
            lines.append("      PUPPETEER_SKIP_CHROMIUM_DOWNLOAD: \"true\"")

        lines.append(f"      LECO_OWN_DOMAINS: \"{slug}.lh\"")

        if cache == "varnish" and is_primary:
            lines.append("      LECO_VARNISH_HOST: varnish")
            lines.append("      LECO_DISABLE_VARNISH_NCSA: \"true\"")

        # Command — skip apt-get/npm on restart when already present
        if is_primary and uses_chromium:
            start_cmd = (
                "if ! command -v chromium >/dev/null 2>&1; then echo 'Installing Chromium + deps' && "
                "apt-get update -qq && apt-get install -y -qq --no-install-recommends chromium "
                "fonts-liberation libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 libxdamage1 "
                "libxrandr2 libgbm1 libasound2 libpangocairo-1.0-0 libgtk-3-0 libxshmfence1 ffmpeg webp "
                "&& rm -rf /var/lib/apt/lists/*; else echo 'Chromium already installed — skipping apt-get'; fi "
                "&& mkdir -p /mnt/tmpfs-user-data && "
                "if [ ! -d /app/node_modules/express ]; then npm install --prefer-offline 2>&1 | tail -5; "
                "else echo 'node_modules present — skipping npm install'; fi "
                f"&& echo 'starting {entry}' && node -r /opt/leco/leco-docker-preload.js {entry}"
            )
        elif is_primary:
            start_cmd = (
                "if [ ! -d /app/node_modules/express ]; then npm install --prefer-offline 2>&1 | tail -5; "
                "else echo 'node_modules present — skipping npm install'; fi "
                f"&& echo 'starting {entry}' && node -r /opt/leco/leco-docker-preload.js {entry}"
            )
        else:
            start_cmd = (
                "while [ ! -d /app/node_modules/express ]; do sleep 2; done "
                f"&& echo 'node_modules ready — starting {entry}' "
                f"&& node -r /opt/leco/leco-docker-preload.js {entry}"
            )
        lines += [
            "    command:",
            f"      {json.dumps(['bash', '-c', start_cmd])}",
        ]

        lines += [
            "    networks:",
            f"      - {network}",
            "      - lh-network",
        ]

    if cache == "varnish":
        lines += [
            "",
            "  varnish:",
            "    networks:",
            "      - lh-network",
        ]

    lines += [
        "",
        "networks:",
        f"  {network}:",
        "    external: false",
        "  lh-network:",
        "    external: true",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# leco-docker-preload.js
# ---------------------------------------------------------------------------

def _gen_preloader(slug: str, config_file: str, patches: dict[str, Any]) -> str:
    lines = [
        "/**",
        f" * LEco runtime config patcher for {slug}.",
        f" * Intercepts require('./{config_file.replace('.js','').replace('.ts','')}') and patches exported values",
        " * using LECO_* environment variables.  Zero upstream code changes.",
        " *",
        " * AI-generated — review and adjust key names to match your config exports.",
        " *",
        " * Usage: node -r /opt/leco/leco-docker-preload.js server.js",
        " */",
        "",
        "'use strict';",
        "",
        "const Module = require('module');",
        "const path = require('path');",
        "",
        "const originalLoad = Module._load;",
        "",
        f"const CONFIG_BASENAME = '{config_file.replace('.js','').replace('.ts','').split('/')[-1]}';",
        "",
        "Module._load = function patchedLoad(request, parent, isMain) {",
        "  const result = originalLoad.apply(this, arguments);",
        "",
        "  // Only patch when the config module is loaded from the app directory",
        "  if (",
        f"    (request === './{config_file.replace('.js','').replace('.ts','')}' || request === './' + CONFIG_BASENAME) &&",
        "    parent && parent.filename && parent.filename.startsWith('/app/')",
        "  ) {",
        "    const env = process.env;",
        "",
    ]

    for key, patch in patches.items():
        if isinstance(patch, dict):
            desc = patch.get("description", key)
            docker_val = patch.get("docker_value", "")
        else:
            desc = key
            docker_val = str(patch)

        # Try to figure out the JS export key name (often same as env var but camelCase)
        js_key = key
        # Common patterns: MONGODB_URI -> MONGODB_URI, REDIS_HOST -> REDIS_HOST
        lines += [
            f"    // {desc}",
            f"    if (env.LECO_{key} && result.{js_key} !== undefined) {{",
            f"      console.log('[leco-preload] {key} →', env.LECO_{key});",
            f"      result.{js_key} = env.LECO_{key};",
            "    }",
            "",
        ]

    lines += [
        "    // Add more patches as needed",
        "  }",
        "",
        "  return result;",
        "};",
        "",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# conf/varnish/default.vcl
# ---------------------------------------------------------------------------

def _gen_varnish_vcl(slug: str, backend_port: int, health_path: str) -> str:
    return f"""\
vcl 4.1;
# AI-generated Varnish VCL for {slug}.
# Review: backend host/port, ACL, cache rules.
# Varnish 7.6 requires vcl 4.1 for resp.body and bereq.backend assignment.

backend default {{
    .host = "{slug}-server";
    .port = "{backend_port}";
    .connect_timeout = 5s;
    .first_byte_timeout = 90s;
    .between_bytes_timeout = 10s;
    .probe = {{
        .url = "{health_path}";
        .interval = 15s;
        .timeout = 5s;
        .window = 5;
        .threshold = 3;
    }}
}}

acl purge_allowed {{
    "localhost";
    "127.0.0.1";
    "172.16.0.0"/12;    # Docker bridge networks
    "10.0.0.0"/8;
}}

sub vcl_recv {{
    # Health check — bypass cache
    if (req.url == "{health_path}") {{
        return (pass);
    }}

    # PURGE support
    if (req.method == "PURGE") {{
        if (!client.ip ~ purge_allowed) {{
            return (synth(405, "PURGE not allowed from " + client.ip));
        }}
        return (purge);
    }}

    # Strip tracking parameters
    if (req.url ~ "(\\?|&)(utm_|fbclid|gclid|mc_)") {{
        set req.url = regsuball(req.url, "(\\?|&)(utm_[a-z_]+|fbclid|gclid|mc_[a-z_]+)=[^&]*", "");
        set req.url = regsub(req.url, "\\?&", "?");
        set req.url = regsub(req.url, "\\?$", "");
    }}

    # Pass non-cacheable methods
    if (req.method != "GET" && req.method != "HEAD") {{
        return (pass);
    }}

    return (hash);
}}

sub vcl_backend_response {{
    # Default TTL if backend doesn't set Cache-Control
    if (beresp.ttl <= 0s && beresp.http.Cache-Control !~ "max-age") {{
        set beresp.ttl = 120s;
        set beresp.uncacheable = false;
    }}

    # Grace period for stale responses
    set beresp.grace = 6h;
}}

sub vcl_deliver {{
    # Debug header
    if (obj.hits > 0) {{
        set resp.http.X-Cache = "HIT (" + obj.hits + ")";
    }} else {{
        set resp.http.X-Cache = "MISS";
    }}
}}

sub vcl_synth {{
    if (resp.status == 405) {{
        set resp.http.Content-Type = "text/plain; charset=utf-8";
        set resp.body = resp.reason;
        return (deliver);
    }}
}}
"""

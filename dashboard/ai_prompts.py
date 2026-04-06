"""
Prompt engineering for AI-assisted onboarding.

Contains the system prompt, analysis JSON schema, and a few-shot example
derived from the botfeed (UtilityServer) onboarding.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# JSON schema the AI must produce
# ---------------------------------------------------------------------------

ANALYSIS_JSON_SCHEMA = """\
{
  "app_name": "string — short slug for the application",
  "description": "string — one-sentence description of what the app does",
  "services": [
    {
      "name": "string — service role (server, worker, cron, queue, etc.)",
      "entry_script": "string — JS/Python/etc file that starts this process",
      "port": "number|null — HTTP port if this service listens (null for background workers)",
      "type": "http|worker|cron|queue — process type",
      "needs_npm_install": "boolean — whether this service needs npm install before start"
    }
  ],
  "data_stores": ["mongodb", "redis", "postgresql", "mysql", "elasticsearch"],
  "cache_layer": "varnish|nginx|null — HTTP cache/proxy in front of the app",
  "health_endpoint": "string|null — health check HTTP path (e.g. /health, /alb-health-check)",
  "listening_port": "number — main HTTP port the primary service binds to",
  "config_file": "string|null — relative path to the config module (e.g. config.js)",
  "config_keys_to_patch": {
    "KEY_NAME": {
      "current_value": "string — the hardcoded localhost/127.0.0.1 value",
      "docker_value": "string — what it should be in Docker (e.g. mongodb://mongo:27017/dbname)",
      "description": "string — what this config key controls"
    }
  },
  "environment_vars": ["string — env vars the app reads via process.env"],
  "entry_scripts": ["string — all JS/Python entry files found"],
  "node_version": "string|null — preferred Node.js version from package.json engines or Dockerfile",
  "uses_chromium": "boolean — whether the app uses Puppeteer, Playwright, or headless Chrome",
  "notes": "string — anything else notable about the app architecture"
}
"""

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert DevOps engineer analyzing a software application to generate \
Docker hosting configuration for the LEco DevOps platform.

## LEco DevOps Architecture

LEco DevOps is a local development platform that runs applications via Docker Compose \
with Traefik reverse proxy on *.lh hostnames. Key patterns:

1. **Docker service naming**: All containers are prefixed with the app slug: \
   `<slug>-server`, `<slug>-mongo`, `<slug>-varnish`, etc.

2. **Traefik routing**: `<slug>.lh` hostname routes to the app's frontend service \
   (Varnish cache port 80, or Express directly on port 3000).

3. **Runtime config preloader** (`leco-docker-preload.js`): Instead of modifying \
   the upstream app, a Node.js preloader intercepts `require('./config')` using \
   `Module._load` and patches exported values. This replaces hardcoded \
   `localhost`/`127.0.0.1` URIs with Docker service names at runtime.

4. **conf/ directory**: Production configs for Varnish, NGINX, Redis, etc. are \
   placed in `conf/<service>/<file>` and bind-mounted `:ro` into containers.

5. **Hosting overlay**: `docker-compose.leco-hosting.yml` adds the `lh-network` \
   (for Traefik), mounts the preloader, sets `LECO_*` env vars, and overrides \
   commands to install dependencies before starting.

6. **Multi-process apps**: Multiple containers run the same codebase with different \
   entry scripts. They share a `node_modules` volume so `npm install` runs once.

## Your Task

Analyze the provided source files and extract structured facts about the application. \
Focus on:

- **Entry scripts**: Which files start HTTP servers vs. background workers vs. cron jobs
- **Ports**: What port the HTTP server listens on
- **Data stores**: MongoDB, Redis, PostgreSQL, etc. (look at config files and dependencies)
- **Cache layer**: Varnish, NGINX, or none
- **Config file**: The module that exports connection URIs, hostnames, ports
- **Config keys to patch**: Keys in the config that reference `localhost`, `127.0.0.1`, \
  or hardcoded hostnames that need to become Docker service names
- **Health endpoint**: HTTP path for health checks (look at route definitions)
- **Environment variables**: Env vars the app reads via `process.env`
- **Chromium usage**: Whether the app uses Puppeteer/Playwright (needs apt-get chromium)

## Output Format

Return ONLY valid JSON matching this schema (no markdown, no commentary):

""" + ANALYSIS_JSON_SCHEMA

# ---------------------------------------------------------------------------
# Few-shot example (derived from botfeed/UtilityServer)
# ---------------------------------------------------------------------------

EXAMPLE_ANALYSIS = {
    "app_name": "botfeed",
    "description": "Node.js utility server for web scraping with Puppeteer, HTTP caching via Varnish, and background job processing",
    "services": [
        {"name": "server", "entry_script": "server.js", "port": 3000, "type": "http", "needs_npm_install": True},
        {"name": "request-queue", "entry_script": "requestQueue.js", "port": None, "type": "queue", "needs_npm_install": False},
        {"name": "worker", "entry_script": "worker.js", "port": None, "type": "worker", "needs_npm_install": False},
        {"name": "cron", "entry_script": "cron.js", "port": None, "type": "cron", "needs_npm_install": False},
    ],
    "data_stores": ["mongodb", "redis"],
    "cache_layer": "varnish",
    "health_endpoint": "/alb-health-check",
    "listening_port": 3000,
    "config_file": "config.js",
    "config_keys_to_patch": {
        "MONGODB_URI": {
            "current_value": "mongodb://localhost:27017/botfeed",
            "docker_value": "mongodb://botfeed-mongo:27017/botfeed",
            "description": "MongoDB connection string",
        },
        "REDIS_HOST": {
            "current_value": "127.0.0.1",
            "docker_value": "botfeed-redis",
            "description": "Redis hostname",
        },
        "VARNISH_HOST": {
            "current_value": "127.0.0.1",
            "docker_value": "botfeed-varnish",
            "description": "Varnish cache hostname",
        },
    },
    "environment_vars": ["NODE_ENV", "PORT", "MONGODB_URI", "REDIS_HOST"],
    "entry_scripts": ["server.js", "requestQueue.js", "worker.js", "cron.js"],
    "node_version": "20",
    "uses_chromium": True,
    "notes": "Express app with Puppeteer-based web scraping. 4 separate processes share MongoDB and Redis. Varnish caches HTTP responses with ESI support. Uses custom tracking parameter stripping in VCL.",
}


def build_analysis_prompt(files: list[dict]) -> str:
    """Build the user prompt from collected files.

    ``files`` is a list of dicts with keys: name, content, lines, truncated.
    """
    import json

    parts = [
        "Analyze the following application source files and return a JSON analysis.\n",
        f"Here is an example of correct output for a Node.js app called 'botfeed':\n```json\n{json.dumps(EXAMPLE_ANALYSIS, indent=2)}\n```\n",
        f"Now analyze these {len(files)} files:\n",
    ]
    for f in files:
        trunc_note = " [truncated]" if f.get("truncated") else ""
        parts.append(f"--- FILE: {f['name']} ({f['lines']} lines{trunc_note}) ---\n")
        parts.append(f["content"])
        parts.append("\n")

    parts.append(
        "\nReturn ONLY valid JSON matching the schema. "
        "Do not include markdown fences or commentary outside the JSON."
    )
    return "\n".join(parts)

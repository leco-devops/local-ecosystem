# syntax=docker/dockerfile:1
# LEco DevOps — generic Cloudflare Workers local runtime image.
#
# Strategy:
#   - node:22-bookworm-slim baseline (glibc). Cloudflare's `workerd` binary
#     published on npm (used by every modern wrangler release) is built for
#     glibc; on Alpine/musl it fails to spawn with ENOENT even though npm
#     installed the package, which crash-loops every Worker. Debian-slim keeps
#     the image small while staying ABI-compatible with workerd.
#   - wrangler is installed globally as a fallback, but the entrypoint prefers
#     the upstream app's locally-installed wrangler so each app pins its own
#     version (and its own matching workerd binary).
#   - The upstream app tree is bind-mounted at /app. The entrypoint runs
#     `npm ci`/`npm install` only when /app/package.json exists *and*
#     /app/node_modules is empty (LEco-owned named volume masks any upstream
#     node_modules so we never write into the real repo), then execs
#     `wrangler dev` so the foreground process is the worker.
#
# Image name (referenced from dashboard/leco_runtimes/cloudflare_workers.py):
#   leco/runtime-cloudflare-workers:latest

FROM node:22-bookworm-slim

ENV LECO_PORT=8787 \
    LECO_WRANGLER_CONFIG=/app/wrangler.toml \
    NODE_ENV=development \
    NPM_CONFIG_LOGLEVEL=warn \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

# tini → clean signal handling for wrangler dev (graceful SIGTERM on compose stop).
# wget/curl → optional health probes from compose / Traefik diagnostics.
# git → some npm packages still want git during install.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini git curl wget ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --silent --global wrangler@^3 pnpm@9 \
    && npm cache clean --force

WORKDIR /app

COPY entrypoint.sh /usr/local/bin/leco-runtime-entrypoint
RUN chmod +x /usr/local/bin/leco-runtime-entrypoint

EXPOSE 8787
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/leco-runtime-entrypoint"]

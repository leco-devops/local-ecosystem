# syntax=docker/dockerfile:1
# LEco DevOps — Cloudflare Pages local runtime (wrangler pages dev).
# Image: leco/runtime-cloudflare-pages:latest

FROM node:22-bookworm-slim

ENV LECO_PORT=8791 \
    LECO_WRANGLER_CONFIG=/app/infra/wrangler.pages.toml \
    NODE_ENV=development \
    NPM_CONFIG_LOGLEVEL=warn \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini git curl wget ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --silent --global wrangler@^3 pnpm@9 \
    && npm cache clean --force

WORKDIR /app

COPY entrypoint.sh /usr/local/bin/leco-runtime-entrypoint
RUN chmod +x /usr/local/bin/leco-runtime-entrypoint

EXPOSE 8791
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/leco-runtime-entrypoint"]

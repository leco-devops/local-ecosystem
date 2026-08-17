# LEco DevOps MCP server — streamable HTTP transport for remote/shared agent access.
#
# Build context is the repo root so the image can be built the same way as the dashboard:
#   docker build -t local/leco-mcp:latest -f tools/mcp-server/Dockerfile .
#
# Local (stdio) usage does not need this image — install the package with pipx/uv instead.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LECO_MCP_HTTP_HOST=0.0.0.0 \
    LECO_MCP_HTTP_PORT=8099 \
    LECO_MCP_DASHBOARD_URL=http://service-dashboard:8090

WORKDIR /app

COPY tools/mcp-server/pyproject.toml tools/mcp-server/README.md ./
COPY tools/mcp-server/leco_mcp ./leco_mcp

RUN pip install --no-cache-dir . uvicorn

# Non-root: this container only makes HTTP calls to the dashboard, it needs no host access
# and must never be able to touch the Docker socket.
RUN useradd --create-home --uid 10001 leco
USER leco

EXPOSE 8099

# A bare GET on /mcp is answered with 406 (the transport requires MCP headers) — that is a
# healthy server, so the check treats any non-5xx response as up.
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD ["python", "-m", "leco_mcp.healthcheck"]

ENTRYPOINT ["leco-mcp"]
CMD ["http"]

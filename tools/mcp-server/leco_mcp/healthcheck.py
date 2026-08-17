"""Container healthcheck for the streamable HTTP transport.

A bare GET on the MCP path is answered with 406 Not Acceptable, because the transport
requires MCP negotiation headers. That is a *healthy* server — only a refused connection or
a 5xx means the process is broken.

Uses the standard library only so the check works in any image.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    host = os.getenv("LECO_MCP_HTTP_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::"):  # noqa: S104 - bind address is not a connect address
        host = "127.0.0.1"
    port = os.getenv("LECO_MCP_HTTP_PORT", "8099")
    path = os.getenv("LECO_MCP_HTTP_PATH", "/mcp")
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - fixed localhost URL
            return 0 if resp.status < 500 else 1
    except urllib.error.HTTPError as exc:
        return 0 if exc.code < 500 else 1
    except Exception as exc:  # noqa: BLE001
        print(f"unhealthy: {url}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

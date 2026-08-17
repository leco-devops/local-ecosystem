"""Async HTTP client for the LEco DevOps dashboard API.

Every MCP tool goes through here so base-URL discovery, control-token injection, timeout
policy, and NDJSON stream handling stay in one place.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

try:  # httpx2 ships with the MCP SDK; the surface used here is identical.
    import httpx
except ModuleNotFoundError:  # pragma: no cover - exercised only on httpx2-only installs
    import httpx2 as httpx  # type: ignore[no-redef]

from .config import Settings


class LecoApiError(RuntimeError):
    """A dashboard API call failed (transport error, HTTP error, or ``ok: false``)."""

    def __init__(self, message: str, *, status: int | None = None, path: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.path = path


class DashboardUnreachable(LecoApiError):
    """No candidate base URL answered — the stack is probably down."""


def _excerpt(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


class LecoClient:
    """Thin async wrapper around the dashboard REST + NDJSON endpoints."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._base_url: str | None = None
        self._lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None

    # ---------------------------------------------------------------- lifecycle

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                verify=self.settings.verify_tls,
                follow_redirects=True,
                timeout=httpx.Timeout(self.settings.read_timeout, connect=10.0),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---------------------------------------------------------- base URL probe

    async def base_url(self) -> str:
        """First candidate that answers ``GET /api/version``; cached for the process."""
        if self._base_url:
            return self._base_url
        async with self._lock:
            if self._base_url:
                return self._base_url
            errors: list[str] = []
            for candidate in self.settings.base_urls:
                url = candidate.rstrip("/")
                try:
                    resp = await self._http().get(f"{url}/api/version", timeout=6.0)
                    if resp.status_code < 400:
                        self._base_url = url
                        return url
                    errors.append(f"{url} → HTTP {resp.status_code}")
                except Exception as exc:  # noqa: BLE001 - report every candidate failure
                    errors.append(f"{url} → {type(exc).__name__}: {exc}")
            raise DashboardUnreachable(
                "LEco DevOps dashboard is not reachable. Tried:\n  "
                + "\n  ".join(errors)
                + "\nStart it with `./ecosystem-stack/ecosystem-stack.sh start dashboard`, "
                "or set LECO_MCP_DASHBOARD_URL to the correct address."
            )

    # ------------------------------------------------------------- token plumbing

    def _auth_headers(self) -> dict[str, str]:
        if not self.settings.has_token:
            return {}
        return {"X-Control-Token": self.settings.control_token}

    def _with_token(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        body = dict(payload or {})
        if self.settings.has_token:
            # The dashboard accepts the token in the header or the body; send both so the
            # call works regardless of which endpoint variant is hit.
            body.setdefault("token", self.settings.control_token)
        return body

    # ------------------------------------------------------------------ requests

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        authed: bool = False,
    ) -> Any:
        base = await self.base_url()
        url = f"{base}{path}"
        headers = self._auth_headers() if authed else {}
        body = self._with_token(json_body) if authed else json_body
        try:
            resp = await self._http().request(
                method,
                url,
                params=params,
                json=body,
                headers=headers,
                timeout=timeout or self.settings.read_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise LecoApiError(
                f"{method} {path} failed: {type(exc).__name__}: {exc}", path=path
            ) from exc

        if resp.status_code == 401:
            raise LecoApiError(
                f"{method} {path} → 401 unauthorized. The dashboard enforces "
                "DASHBOARD_CONTROL_TOKEN; set the same value in LECO_MCP_CONTROL_TOKEN "
                "(or DASHBOARD_CONTROL_TOKEN) for this MCP server.",
                status=401,
                path=path,
            )
        if resp.status_code >= 400:
            raise LecoApiError(
                f"{method} {path} → HTTP {resp.status_code}: {_excerpt(resp.text)}",
                status=resp.status_code,
                path=path,
            )
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            raise LecoApiError(
                f"{method} {path} returned non-JSON body: {_excerpt(resp.text)}",
                status=resp.status_code,
                path=path,
            ) from exc

    async def get(
        self, path: str, *, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        return await self._request("GET", path, params=params, timeout=timeout)

    async def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        authed: bool = True,
    ) -> Any:
        return await self._request(
            "POST", path, json_body=json_body, timeout=timeout, authed=authed
        )

    async def put(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
        authed: bool = True,
    ) -> Any:
        return await self._request(
            "PUT", path, json_body=json_body, timeout=timeout, authed=authed
        )

    # ------------------------------------------------------------------ streaming

    async def stream_ndjson(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded events from a dashboard NDJSON endpoint.

        Streaming endpoints emit ``{"type": "log", "text": ...}`` lines followed by a final
        ``{"type": "done", "result": {...}}``. Malformed lines are surfaced as log events
        rather than aborting a long-running deploy.
        """
        base = await self.base_url()
        url = f"{base}{path}"
        body = self._with_token(json_body)
        try:
            async with self._http().stream(
                "POST",
                url,
                json=body,
                headers=self._auth_headers(),
                timeout=httpx.Timeout(timeout or self.settings.action_timeout, connect=10.0),
            ) as resp:
                if resp.status_code >= 400:
                    text = await resp.aread()
                    raise LecoApiError(
                        f"POST {path} → HTTP {resp.status_code}: "
                        f"{_excerpt(text.decode('utf-8', 'replace'))}",
                        status=resp.status_code,
                        path=path,
                    )
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        yield {"type": "log", "text": line}
        except LecoApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LecoApiError(
                f"POST {path} stream failed: {type(exc).__name__}: {exc}", path=path
            ) from exc

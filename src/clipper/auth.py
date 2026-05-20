"""Shared-token auth for the FastAPI server.

Token is read from the `CLIPPER_TOKEN` env var (or `CLIPPER_TOKENS`,
comma-separated). If unset, the middleware is a no-op — useful for
local dev. Clients can present the token via:

- `X-API-Token` header — preferred for fetch/XHR.
- `?token=<value>` query string — needed for browser-embedded URLs
  (`<video src=...>`, `<a download>`) that can't carry custom headers.

Public paths (always allowed): `/health`, `/`, `/index.html`, anything
under `/assets/`, `/docs`, `/redoc`, `/openapi.json`, `/favicon.svg`.
"""

from __future__ import annotations

import os
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_PUBLIC_PREFIXES = (
    "/health",
    "/assets/",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.svg",
)
_PUBLIC_EXACT = {"/", "/index.html"}


def _configured_tokens() -> set[str]:
    raw = os.environ.get("CLIPPER_TOKEN", "") or os.environ.get("CLIPPER_TOKENS", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    return any(path.startswith(p) for p in _PUBLIC_PREFIXES)


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Token gate for /api and /media. Inert when no token is configured."""

    def __init__(self, app):  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.tokens = _configured_tokens()
        self.enabled = bool(self.tokens)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or _is_public(request.url.path):
            return await call_next(request)
        token = request.headers.get("x-api-token") or request.query_params.get("token")
        if not token or token not in self.tokens:
            return JSONResponse(
                {"detail": "unauthorized — provide a valid X-API-Token header or ?token= param"},
                status_code=401,
            )
        return await call_next(request)

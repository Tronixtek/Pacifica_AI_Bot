from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


# Endpoints that change money or bot behaviour. Everything under here is
# refused in read-only mode.
MUTATING_PREFIXES = ("/api/operator",)

# Methods that can only be reads. Listing methods rather than paths means a
# route added later is covered without anyone remembering to update this.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Narrow exceptions: mutating routes that remain available in read-only mode.
# Pausing a bot only ever REDUCES what it can do - it stops new positions and
# cannot open one - so refusing it would mean the dashboard could show a bot
# losing money without offering the one control that helps. Order submission
# stays blocked.
ALWAYS_ALLOWED_SUFFIXES = ("/pause", "/resume")
ALWAYS_ALLOWED_PREFIX = "/api/bots/"


class ReadOnlyApiMiddleware(BaseHTTPMiddleware):
    """Refuse anything that could trade when the API is exposed for viewing.

    The dashboard is read-only by nature, but the same service exposes
    operator endpoints that submit orders, pause the engine and reset the
    paper book - and none of them require authentication. Reaching the API
    from a phone means widening what can reach it, so the trading surface is
    closed off rather than trusted to stay unreachable.

    Enforced as middleware rather than per-route: a new operator endpoint is
    then covered by default instead of by remembering.
    """

    def __init__(self, app, enabled: bool) -> None:
        super().__init__(app)
        self.enabled = enabled

    async def dispatch(self, request: Request, call_next):
        if not self.enabled:
            return await call_next(request)

        path = request.url.path

        if path.startswith(ALWAYS_ALLOWED_PREFIX) and path.endswith(ALWAYS_ALLOWED_SUFFIXES):
            return await call_next(request)

        mutating_path = any(path.startswith(p) for p in MUTATING_PREFIXES)
        unsafe_method = request.method.upper() not in SAFE_METHODS

        if mutating_path or unsafe_method:
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "This service is running in read-only mode. Trading and "
                        "operator actions are disabled. Set API_READ_ONLY=false "
                        "and restart to re-enable them."
                    ),
                    "path": path,
                    "method": request.method,
                },
            )
        return await call_next(request)

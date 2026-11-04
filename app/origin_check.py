"""Same-origin check for state-changing requests.

The session cookie's SameSite=Lax does not stop a form post from a sibling
subdomain: every app on the estate is a subdomain of the same registrable
domain, so a compromised sibling counts as "same-site". This middleware refuses
such writes. It follows the estate's settled rule (the auth gateway uses the
same one):

* Only POST, PUT, PATCH and DELETE are checked.
* ``Origin: null`` is treated as absent. Mobile Safari and Chrome send it on
  same-origin form posts when the referrer policy is strict.
* If ``Sec-Fetch-Site`` is present, ``same-origin`` and ``none`` are allowed and
  anything else (``same-site``, ``cross-site``) is refused.
* If ``Sec-Fetch-Site`` is absent and ``Origin`` is present, ``Origin`` must
  equal the app's own origin.
* If both are absent, the request is allowed (non-browser clients).

The app's own origin is ``NARRATOR_PUBLIC_URL`` when that is set, otherwise
scheme://host from the request, where the scheme comes from
``X-Forwarded-Proto`` (set by Caddy) and falls back to the connection scheme.

Written as plain ASGI middleware rather than ``BaseHTTPMiddleware`` so that it
never buffers request or response bodies (audio uploads, streamed exports).
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

CHECKED_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})

# Machine endpoints with their own authentication, and health checks. Narrator
# has no webhook or token-authenticated API; /healthz is the only entry and is
# GET-only, so the exemption is belt and braces for the container healthcheck.
EXEMPT_PATHS = frozenset({"/healthz"})


def _normalise_origin(value: str) -> str | None:
    """Lower-cased scheme://host[:port] with default ports dropped, or None."""
    parts = urlsplit(value.strip())
    if not parts.scheme or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    if ":" in host:  # IPv6 literal
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        return None
    if port is None or (scheme, port) in {("http", 80), ("https", 443)}:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _own_origin(scope: Scope, headers: Headers, public_url: str | None) -> str | None:
    if public_url:
        return _normalise_origin(public_url)
    host = headers.get("host")
    if not host:
        return None
    proto = headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if proto not in {"http", "https"}:
        proto = scope.get("scheme", "http")
    return _normalise_origin(f"{proto}://{host}")


def origin_allowed(scope: Scope, headers: Headers, public_url: str | None) -> bool:
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site.strip().lower() in ALLOWED_FETCH_SITES

    origin = headers.get("origin")
    if origin is None or origin.strip().lower() == "null":
        return True

    claimed = _normalise_origin(origin)
    own = _own_origin(scope, headers, public_url)
    return claimed is not None and own is not None and claimed == own


class SameOriginMiddleware:
    def __init__(self, app: ASGIApp, public_url: str | None = None) -> None:
        self.app = app
        if public_url is None:
            public_url = os.environ.get("NARRATOR_PUBLIC_URL") or None
        self.public_url = public_url

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"].upper() in CHECKED_METHODS
            and scope["path"] not in EXEMPT_PATHS
            and not origin_allowed(scope, Headers(scope=scope), self.public_url)
        ):
            response = PlainTextResponse(
                "Cross-origin request refused.", status_code=403
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)

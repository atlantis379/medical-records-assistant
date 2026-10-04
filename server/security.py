"""Who may talk to the local service.

The service listens on 127.0.0.1 only, but any web page the doctor has open can still send requests
there. A browser's cross-origin rules stop a page from *reading* answers, not from *sending* requests,
and they do not apply to WebSocket at all. DNS rebinding can even make a hostile site look same-origin.
So every request is checked here, before it reaches any endpoint:

  1. the client is on this computer;
  2. the Host header is a loopback name (stops DNS rebinding: the hostile site's own name is refused);
  3. the Origin, if present, is this browser extension or the service's own status page;
  4. requests that change anything (POST, PUT, DELETE ...) and WebSocket connections must carry such an
     Origin. Browsers always attach one to those, and a web page cannot forge it. Plain reads (GET)
     without an Origin are allowed: opening /health in the address bar, the launcher and scripts do that.
     (A browser extension's own GET carries no Origin either, so pinning extension IDs can only restrict
     requests that change data and WebSocket, not reads.)
  5. a read without an Origin is still refused when the browser says it came from another site
     (Sec-Fetch-Site: cross-site / same-site, e.g. an <img> or a link on a hostile page). The extension,
     the address bar and the launcher are "none"; the service's own page is "same-origin".

The extension is pinned: only the IDs in server/data/allowed_extensions.json may change data or use dictation,
so no other installed extension can. manifest.json carries a `key` that gives the extension that same ID on
every computer (scripts/extension_id.py). BINGLI_ALLOWED_EXTENSIONS=<id>[,<id>...] overrides the file for one
run, and BINGLI_ALLOWED_EXTENSIONS=* (or an empty `ids` list in the file) accepts any extension.
"""
import json
import logging
import os
import re
from pathlib import Path

LOGGER = logging.getLogger("uvicorn.error")
ALLOWED_FILE = Path(__file__).resolve().parent / "data" / "allowed_extensions.json"
_file_cache: dict = {"stamp": None, "ids": set()}

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "testserver"}   # "testserver" is Starlette's TestClient
LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "testclient"}
EXTENSION_ORIGIN = re.compile(r"^(chrome-extension|edge-extension)://([a-z0-9]+)$")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _shipped_extension_ids() -> set[str]:
    try:
        stamp = ALLOWED_FILE.stat().st_mtime_ns
    except OSError:
        return set()
    if _file_cache["stamp"] != stamp:
        try:
            data = json.loads(ALLOWED_FILE.read_text(encoding="utf-8-sig"))
            ids = {str(item).strip().lower() for item in data.get("ids", []) if str(item).strip()}
        except (OSError, ValueError, AttributeError):
            LOGGER.warning("%s is unreadable; any browser extension is accepted until it is fixed", ALLOWED_FILE)
            ids = set()
        _file_cache.update(stamp=stamp, ids=ids)
    return set(_file_cache["ids"])


def pinned_extension_ids() -> set[str]:
    """IDs allowed to change data / use dictation. An empty set means: any extension."""
    configured = os.getenv("BINGLI_ALLOWED_EXTENSIONS", "").strip()
    if configured:
        ids = {item.strip().lower() for item in configured.split(",") if item.strip()}
        return set() if "*" in ids else ids
    return _shipped_extension_ids()


def host_name(host_header: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '[::1]'."""
    if host_header.startswith("["):
        return host_header[: host_header.find("]") + 1] if "]" in host_header else host_header
    return host_header.rsplit(":", 1)[0] if ":" in host_header else host_header


def client_allowed(client_host: str) -> bool:
    return client_host in LOOPBACK_CLIENTS


def host_allowed(host_header: str) -> bool:
    return host_name(host_header.lower()) in LOOPBACK_HOSTS


def origin_allowed(origin: str, host_header: str) -> bool:
    """The browser extension (optionally only the pinned ones) or the service's own pages."""
    match = EXTENSION_ORIGIN.match(origin)
    if match:
        pinned = pinned_extension_ids()
        return not pinned or match.group(2) in pinned
    return origin == f"http://{host_header}" and host_allowed(host_header)


def check(*, kind: str, method: str, client_host: str, host_header: str, origin: str | None,
          fetch_site: str | None = None) -> tuple[bool, str]:
    """Decide one request. kind is 'http' or 'websocket'. Returns (allowed, reason)."""
    if not client_allowed(client_host):
        return False, "not a local client"
    if not host_allowed(host_header):
        return False, "unexpected Host header"
    if origin is not None:
        if not origin_allowed(origin, host_header):
            return False, "origin not allowed"
        return True, ""
    if kind == "websocket":
        return False, "origin required"
    if method.upper() not in SAFE_METHODS:
        return False, "origin required for requests that change data"
    if (fetch_site or "").lower() in ("cross-site", "same-site"):
        return False, "request came from another site"
    return True, ""


class OriginGuard:
    """ASGI middleware applying check() to every HTTP request and WebSocket handshake."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        client = scope.get("client") or ("", 0)
        allowed, reason = check(kind=scope["type"], method=scope.get("method", "GET"), client_host=client[0],
                                host_header=headers.get("host", ""), origin=headers.get("origin"),
                                fetch_site=headers.get("sec-fetch-site"))
        if allowed:
            await self.app(scope, receive, send)
            return
        LOGGER.warning("blocked %s %s from %s (%s; origin=%s host=%s)", scope["type"], scope.get("path"), client[0], reason,
                       headers.get("origin"), headers.get("host"))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})     # refused before the handshake completes
            return
        body = json.dumps({"detail": f"request refused: {reason}"}).encode("utf-8")
        await send({"type": "http.response.start", "status": 403, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})

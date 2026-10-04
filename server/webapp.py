"""Serves the dictation page (the files of extension/) from the local service: http://127.0.0.1:8765/app/

The same page also runs as a browser extension. Serving it here means a doctor only needs the desktop icon:
no extension to load in developer mode (often switched off by hospital policy) and no extension ID to pin.

Only the page's own files are served (a fixed list of types, no directory listing, no path tricks), and the
page carries a strict Content-Security-Policy. The page is open without a login (it has to show the login
screen); everything it fetches from the service is not.
"""
import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse, Response

ROOT = Path(__file__).resolve().parent.parent / "extension"
ALLOWED_SUFFIXES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                    ".png": "image/png"}
NAME = re.compile(r"^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*\.[a-z]{2,4}$")
EXCLUDED = {"background.js", "manifest.json"}            # extension-only files

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self' ws://127.0.0.1:* ws://localhost:* ws://[::1]:*; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'")
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    "Cache-Control": "no-store",
}

router = APIRouter()


def resolve(name: str) -> Path | None:
    """The file for a request path under /app/, or None."""
    if not name:
        name = "editor.html"
    if not NAME.match(name) or name in EXCLUDED:
        return None
    suffix = Path(name).suffix
    if suffix not in ALLOWED_SUFFIXES:
        return None
    path = (ROOT / name).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


@router.get("/app")
def app_redirect():
    return RedirectResponse("/app/", status_code=307)


@router.get("/app/{name:path}")
def app_file(name: str = ""):
    path = resolve(name)
    if path is None:
        return Response("Not found", status_code=404, headers=HEADERS)
    return FileResponse(path, media_type=ALLOWED_SUFFIXES[path.suffix], headers=HEADERS)

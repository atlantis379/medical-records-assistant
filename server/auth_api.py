"""Login for the doctors who share this computer: the guard that protects every route, and the /auth and
/workspace endpoints. See server/accounts.py for what the accounts do and do not protect against.

The token (random, kept in the extension's session storage, so gone when the browser closes) goes in an
`Authorization: Bearer` header; a WebSocket carries it as the second sub-protocol, `token.<value>`, because
browsers cannot set headers there and a URL parameter would end up in the service log.
"""
import contextvars
import json
import os
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.responses import JSONResponse

from . import accounts

router = APIRouter()
SUBPROTOCOL = "bingli.v1"

# Reachable without logging in: what the launcher, the status page and the login screen itself need.
OPEN_PATHS = {"/", "/health", "/service/status", "/service/shutdown", "/auth/status", "/auth/login", "/auth/setup"}
# The dictation page's own files (it has to be able to show the login screen).
OPEN_PREFIXES = ("/app/",)
# Administrators only.
ADMIN_PREFIXES = ("/hotword-miner", "/auth/users", "/auth/audit", "/feedback/export")

_current: contextvars.ContextVar = contextvars.ContextVar("bingli_session", default=None)
_DEV = accounts.Session(token="-", user_id="0" * 32, username="dev", display_name="", role="admin", key=b"\0" * 32)


def enabled() -> bool:
    """BINGLI_AUTH=0 switches the login off. For development and tests only: the service says so in its log."""
    return os.getenv("BINGLI_AUTH", "1") != "0"


def current_session() -> accounts.Session | None:
    return _current.get() if enabled() else (_current.get() or _DEV)


def logged_in_session() -> accounts.Session | None:
    """The doctor who is really logged in; None when the login is off (development, tests)."""
    return _current.get()


def current_user_id() -> str | None:
    session = current_session()
    return session.user_id if session else None


def _headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


def _token(scope, headers) -> str | None:
    if scope["type"] == "websocket":
        for part in headers.get("sec-websocket-protocol", "").split(","):
            part = part.strip()
            if part.startswith("token."):
                return part[len("token."):]
        return None
    value = headers.get("authorization", "")
    return value[7:].strip() if value.lower().startswith("bearer ") else None


class AuthGuard:
    """ASGI middleware: a valid session for every route except OPEN_PATHS, and the admin-only prefixes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "GET")
        headers = _headers(scope)
        is_open = scope["type"] == "http" and (method == "OPTIONS" or path in OPEN_PATHS or path == "/app" or path.startswith(OPEN_PREFIXES))
        if not enabled():
            await self.app(scope, receive, send)
            return
        store = accounts.get_store()
        session = store.sessions.get(_token(scope, headers), touch=not is_open)
        if session is None and not is_open:
            await self._refuse(scope, send, 401, "login_required")
            return
        if session is not None and path.startswith(ADMIN_PREFIXES) and session.role != "admin":
            await self._refuse(scope, send, 403, "只有管理员可以使用这个功能")
            return
        marker = _current.set(session)
        try:
            await self.app(scope, receive, send)
        finally:
            _current.reset(marker)

    @staticmethod
    async def _refuse(scope, send, status: int, detail: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})


def _store() -> accounts.Store:
    return accounts.get_store()


def _need_session() -> accounts.Session:
    session = current_session()
    if session is None:
        raise HTTPException(status_code=401, detail="login_required")
    return session


def _fail(exc: accounts.AccountError) -> HTTPException:
    if isinstance(exc, accounts.Locked):
        return HTTPException(status_code=429, detail=str(exc), headers={"Retry-After": str(exc.seconds)})
    if isinstance(exc, accounts.InvalidCredentials):
        return HTTPException(status_code=401, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _login_payload(session: accounts.Session) -> dict:
    return {"token": session.token, "user": {"id": session.user_id, "username": session.username, "display_name": session.display_name,
                                             "role": session.role}, "idle_seconds": accounts.idle_seconds()}


@router.get("/auth/status")
def auth_status():
    store, session = _store(), _current.get()
    return {"auth_enabled": enabled(), "setup_required": enabled() and not store.has_users(), "authenticated": session is not None or not enabled(),
            "user": None if session is None else {"id": session.user_id, "username": session.username, "display_name": session.display_name,
                                                  "role": session.role},
            "idle_seconds": accounts.idle_seconds(), "workspace_hours": accounts.workspace_ttl_seconds() / 3600}


class SetupRequest(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)
    display_name: str = Field("", max_length=40)


@router.post("/auth/setup")
def auth_setup(request: SetupRequest):
    """The first account is the administrator. Only possible while there are no accounts."""
    store = _store()
    with store.lock:
        if store.has_users():
            raise HTTPException(status_code=409, detail="已经设置过管理员，请直接登录")
        try:
            store.create_user(request.username, request.password, request.display_name, role="admin")
        except accounts.AccountError as exc:
            raise _fail(exc) from exc
    store.audit("setup", request.username.strip())
    try:
        return _login_payload(store.login(request.username, request.password))
    except accounts.AccountError as exc:
        raise _fail(exc) from exc


class LoginRequest(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)


@router.post("/auth/login")
def auth_login(request: LoginRequest):
    try:
        return _login_payload(_store().login(request.username, request.password))
    except accounts.AccountError as exc:
        raise _fail(exc) from exc


class LogoutRequest(BaseModel):
    clear_workspace: bool = False


@router.post("/auth/logout")
def auth_logout(request: LogoutRequest):
    """`clear_workspace`: the doctor is finished, so the saved work is deleted too."""
    session = _need_session()
    _store().logout(session, clear_workspace=request.clear_workspace)
    return {"ok": True}


@router.post("/auth/touch")
def auth_touch():
    _need_session()
    return {"ok": True}


class PasswordRequest(BaseModel):
    old_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


@router.post("/auth/password")
def auth_password(request: PasswordRequest):
    session = _need_session()
    try:
        _store().change_password(session, request.old_password, request.new_password)
    except accounts.AccountError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


# ---------------------------------------------------------------------- administrators
class NewUser(BaseModel):
    username: str = Field(..., max_length=40)
    password: str = Field(..., max_length=200)
    display_name: str = Field("", max_length=40)
    role: str = "doctor"


@router.get("/auth/users")
def users_list():
    return {"users": _store().list_users()}


@router.post("/auth/users")
def users_create(request: NewUser):
    store = _store()
    try:
        user = store.create_user(request.username, request.password, request.display_name, request.role)
    except accounts.AccountError as exc:
        raise _fail(exc) from exc
    store.audit("user_created", user["username"])
    return {"user": accounts.public(user)}


class NewPassword(BaseModel):
    password: str = Field(..., max_length=200)


@router.post("/auth/users/{user_id}/reset-password")
def users_reset_password(user_id: str, request: NewPassword):
    session = _need_session()
    try:
        _store().reset_password(user_id, request.password, session.username)
    except accounts.AccountError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


class UserChange(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    display_name: str | None = Field(None, max_length=40)


@router.post("/auth/users/{user_id}/update")
def users_update(user_id: str, request: UserChange):
    try:
        user = _store().update_user(user_id, request.role, request.disabled, request.display_name)
    except accounts.AccountError as exc:
        raise _fail(exc) from exc
    return {"user": accounts.public(user)}


@router.post("/auth/users/{user_id}/delete")
def users_delete(user_id: str):
    try:
        _store().delete_user(user_id)
    except accounts.AccountError as exc:
        raise _fail(exc) from exc
    return {"ok": True}


@router.get("/auth/audit")
def audit_list(limit: int = 100):
    return {"items": _store().read_audit(max(1, min(limit, 500)))}


# ---------------------------------------------------------------------- the doctor's own saved work
class WorkspaceBody(BaseModel):
    data: str = Field(..., max_length=accounts.MAX_WORKSPACE_BYTES)


@router.get("/workspace")
def workspace_get():
    session = _need_session()
    loaded = _store().load_workspace(session)
    if loaded is None:
        return {"exists": False}
    return {"exists": True, "data": loaded["data"].decode("utf-8"), "expires_at": loaded["expires_at"]}


@router.put("/workspace")
def workspace_put(request: WorkspaceBody):
    session = _need_session()
    try:
        info = _store().save_workspace(session, request.data.encode("utf-8"))
    except accounts.AccountError as exc:
        raise _fail(exc) from exc
    return {"ok": True, **info}


@router.post("/workspace/clear")
def workspace_clear():
    session = _need_session()
    _store().clear_workspace(session.user_id)
    return {"ok": True}

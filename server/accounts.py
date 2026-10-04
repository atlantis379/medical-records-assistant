"""Local doctor accounts for computers that several doctors share one after another.

What it gives, and what it does not (docs/LOCAL_ACCOUNTS.md):

* Each doctor has a password. The login hash and a per-doctor data key come from one scrypt run over the
  password, so the data key exists only while that doctor is logged in (it is kept in the session's memory).
* A doctor's saved work (draft, versions, patient tabs) is encrypted with that key (AES-256-GCM) and deleted
  after a time limit, so a restart does not lose it but another doctor cannot read it, even from the disk.
* One session at a time: a new login ends the previous session (its saved work stays, encrypted).
* It is NOT a barrier against someone with full access to the computer's operating-system account: that person
  can delete the account file and start again (the encrypted work is then unreadable, not exposed).

Nothing here talks to a hospital system. Passwords are never logged or stored; only a scrypt verifier is.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 64}
USERNAME = re.compile(r"^[A-Za-z0-9_\-一-鿿]{2,20}$")
ROLES = ("admin", "doctor")
MIN_PASSWORD = 8
MAX_PASSWORD = 128
MAX_WORKSPACE_BYTES = 8 * 1024 * 1024
FAILURES_BEFORE_LOCK = 5


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def idle_seconds() -> float:
    return _env_number("BINGLI_IDLE_MINUTES", 10) * 60


def session_max_seconds() -> float:
    return _env_number("BINGLI_SESSION_MAX_HOURS", 12) * 3600


def workspace_ttl_seconds() -> float:
    return _env_number("BINGLI_WORKSPACE_HOURS", 12) * 3600


class AccountError(Exception):
    """A refusal with a message the doctor can read."""


class InvalidCredentials(AccountError):
    pass


class Locked(AccountError):
    def __init__(self, seconds: int):
        super().__init__(f"连续输错太多次，请 {seconds} 秒后再试")
        self.seconds = seconds


def home_dir() -> Path:
    explicit = os.getenv("BINGLI_HOME")
    if explicit:
        return Path(explicit)
    base = os.getenv("LOCALAPPDATA")
    return (Path(base) if base else Path.home() / ".local" / "share") / "BingliAssistant"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def derive(password: str, salt: bytes) -> tuple[bytes, bytes]:
    """(login verifier, data key): two halves of one scrypt output."""
    out = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT)
    return out[:32], out[32:]


def check_password_rules(username: str, password: str) -> None:
    if not isinstance(password, str) or not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        raise AccountError(f"密码长度需要 {MIN_PASSWORD}～{MAX_PASSWORD} 个字符")
    if password.lower() == username.lower():
        raise AccountError("密码不能与用户名相同")
    if len(set(password)) < 3:
        raise AccountError("密码过于简单")


def public(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "display_name": user.get("display_name", ""),
            "role": user["role"], "disabled": bool(user.get("disabled")), "created_at": user.get("created_at")}


@dataclass
class Session:
    token: str
    user_id: str
    username: str
    display_name: str
    role: str
    key: bytes = field(repr=False)
    created: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


class Sessions:
    """At most one session at a time (doctors take turns on the computer)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._session: Session | None = None

    def start(self, user: dict, key: bytes) -> Session:
        with self._lock:
            now = time.time()
            self._session = Session(secrets.token_urlsafe(32), user["id"], user["username"], user.get("display_name", ""), user["role"], key,
                                    created=now, last_seen=now)
            return self._session

    def get(self, token: str | None, touch: bool = True) -> Session | None:
        if not token:
            return None
        with self._lock:
            session = self._session
            if session is None or not hmac.compare_digest(session.token, token):
                return None
            now = time.time()
            if now - session.last_seen > idle_seconds() or now - session.created > session_max_seconds():
                self._session = None
                return None
            if touch:
                session.last_seen = now
            return session

    def end(self, token: str | None = None) -> bool:
        with self._lock:
            if self._session is not None and (token is None or hmac.compare_digest(self._session.token, token)):
                self._session = None
                return True
            return False

    def end_user(self, user_id: str) -> None:
        with self._lock:
            if self._session is not None and self._session.user_id == user_id:
                self._session = None


class Store:
    """accounts.json, per-user folders and the audit log under one home folder."""

    def __init__(self, home: Path):
        self.home = Path(home)
        self.lock = threading.RLock()
        self.failures: dict[str, list] = {}
        self.sessions = Sessions()

    # ------------------------------------------------------------------ files
    @property
    def accounts_file(self) -> Path:
        return self.home / "accounts.json"

    def user_dir(self, user_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", user_id):
            raise AccountError("无效的用户")
        return self.home / "users" / user_id

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [u for u in data.get("users", []) if isinstance(u, dict)] if isinstance(data, dict) else []

    def _write(self, users: list[dict]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=self.home, prefix="accounts.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "users": users}, stream, ensure_ascii=False, indent=2)
            os.replace(temp, self.accounts_file)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------ audit
    def audit(self, event: str, username: str = "", ok: bool = True) -> None:
        """Who did what and when. Never a password, never any content."""
        try:
            self.home.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()), "event": event, "user": username, "ok": ok},
                              ensure_ascii=False)
            with (self.home / "audit.log").open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError:
            pass

    def read_audit(self, limit: int = 200) -> list[dict]:
        try:
            lines = (self.home / "audit.log").read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
        items = []
        for line in lines:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return list(reversed(items))

    # ------------------------------------------------------------------ users
    def has_users(self) -> bool:
        with self.lock:
            return bool(self._read())

    def list_users(self) -> list[dict]:
        with self.lock:
            return [public(u) for u in self._read()]

    def _find(self, users: list[dict], username: str) -> dict | None:
        key = username.strip().lower()
        return next((u for u in users if u["username"].lower() == key), None)

    def create_user(self, username: str, password: str, display_name: str = "", role: str = "doctor") -> dict:
        username = (username or "").strip()
        if not USERNAME.match(username):
            raise AccountError("用户名为 2～20 个字符，可用汉字、字母、数字、下划线和短横线")
        if role not in ROLES:
            raise AccountError("角色只能是管理员或医生")
        display_name = " ".join(str(display_name or "").split())[:20]
        check_password_rules(username, password)
        salt = secrets.token_bytes(16)
        verifier, _ = derive(password, salt)
        with self.lock:
            users = self._read()
            if self._find(users, username):
                raise AccountError("用户名已存在")
            user = {"id": uuid.uuid4().hex, "username": username, "display_name": display_name, "role": role, "salt": _b64(salt),
                    "verifier": _b64(verifier), "disabled": False, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            users.append(user)
            self._write(users)
        return user

    def _lock_status(self, key: str) -> int:
        entry = self.failures.get(key)
        if entry and entry[1] > time.time():
            return int(entry[1] - time.time()) + 1
        return 0

    def authenticate(self, username: str, password: str) -> tuple[dict, bytes]:
        """(user, data key). The same message for a wrong name, a wrong password and a disabled account."""
        key = (username or "").strip().lower()
        remaining = self._lock_status(key)
        if remaining:
            self.audit("login_locked", key, ok=False)
            raise Locked(remaining)
        with self.lock:
            user = self._find(self._read(), key)
        salt = _unb64(user["salt"]) if user else b"\0" * 16          # the same work for unknown names
        verifier, data_key = derive(password if isinstance(password, str) else "", salt)
        good = bool(user) and not user.get("disabled") and hmac.compare_digest(verifier, _unb64(user["verifier"]))
        if not good:
            entry = self.failures.setdefault(key, [0, 0.0])
            entry[0] += 1
            if entry[0] >= FAILURES_BEFORE_LOCK:
                entry[1] = time.time() + min(900, 30 * 2 ** (entry[0] - FAILURES_BEFORE_LOCK))
            self.audit("login_failed", key, ok=False)
            raise InvalidCredentials("用户名或密码不正确")
        self.failures.pop(key, None)
        return user, data_key

    def login(self, username: str, password: str) -> Session:
        user, data_key = self.authenticate(username, password)
        self.purge_expired_workspaces()
        session = self.sessions.start(user, data_key)
        self.audit("login", user["username"])
        return session

    def logout(self, session: Session, clear_workspace: bool = False) -> None:
        if clear_workspace:
            self.clear_workspace(session.user_id)
        self.sessions.end(session.token)
        self.audit("logout", session.username)

    def change_password(self, session: Session, old: str, new: str) -> None:
        check_password_rules(session.username, new)
        with self.lock:
            users = self._read()
            user = next((u for u in users if u["id"] == session.user_id), None)
            if not user:
                raise AccountError("用户不存在")
            verifier, old_key = derive(old if isinstance(old, str) else "", _unb64(user["salt"]))
            if not hmac.compare_digest(verifier, _unb64(user["verifier"])):
                self.audit("password_change_failed", session.username, ok=False)
                raise InvalidCredentials("原密码不正确")
            blob = self._load_workspace_locked(user["id"], old_key)
            salt = secrets.token_bytes(16)
            new_verifier, new_key = derive(new, salt)
            user["salt"], user["verifier"] = _b64(salt), _b64(new_verifier)
            self._write(users)
            if blob is not None:
                self._save_workspace_locked(user["id"], new_key, blob[0], expires_at=blob[1])
            session.key = new_key
        self.audit("password_changed", session.username)

    def _admin_count(self, users: list[dict], excluding: str | None = None) -> int:
        return sum(1 for u in users if u["role"] == "admin" and not u.get("disabled") and u["id"] != excluding)

    def reset_password(self, user_id: str, new: str, by: str) -> None:
        """Admin reset. The doctor's saved work cannot be decrypted with the new password, so it is deleted."""
        with self.lock:
            users = self._read()
            user = next((u for u in users if u["id"] == user_id), None)
            if not user:
                raise AccountError("用户不存在")
            check_password_rules(user["username"], new)
            salt = secrets.token_bytes(16)
            verifier, _ = derive(new, salt)
            user["salt"], user["verifier"] = _b64(salt), _b64(verifier)
            self._write(users)
            self.clear_workspace(user_id)
            self.failures.pop(user["username"].lower(), None)
        self.sessions.end_user(user_id)
        self.audit(f"password_reset_by_{by}", user["username"])

    def update_user(self, user_id: str, role: str | None = None, disabled: bool | None = None, display_name: str | None = None) -> dict:
        with self.lock:
            users = self._read()
            user = next((u for u in users if u["id"] == user_id), None)
            if not user:
                raise AccountError("用户不存在")
            if role is not None:
                if role not in ROLES:
                    raise AccountError("角色只能是管理员或医生")
                if user["role"] == "admin" and role != "admin" and self._admin_count(users, excluding=user_id) == 0:
                    raise AccountError("至少要保留一位管理员")
                user["role"] = role
            if disabled is not None:
                if disabled and user["role"] == "admin" and self._admin_count(users, excluding=user_id) == 0:
                    raise AccountError("至少要保留一位可用的管理员")
                user["disabled"] = bool(disabled)
            if display_name is not None:
                user["display_name"] = " ".join(str(display_name).split())[:20]
            self._write(users)
        if user.get("disabled") or role is not None:
            self.sessions.end_user(user_id)
        self.audit("user_updated", user["username"])
        return user

    def delete_user(self, user_id: str) -> None:
        import shutil
        with self.lock:
            users = self._read()
            user = next((u for u in users if u["id"] == user_id), None)
            if not user:
                raise AccountError("用户不存在")
            if user["role"] == "admin" and self._admin_count(users, excluding=user_id) == 0:
                raise AccountError("至少要保留一位管理员")
            self._write([u for u in users if u["id"] != user_id])
            shutil.rmtree(self.user_dir(user_id), ignore_errors=True)
        self.sessions.end_user(user_id)
        self.audit("user_deleted", user["username"])

    # ------------------------------------------------------------------ saved work (encrypted, time-limited)
    def _workspace_file(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "workspace.bin"

    def _save_workspace_locked(self, user_id: str, key: bytes, data: bytes, expires_at: float | None = None) -> float:
        if len(data) > MAX_WORKSPACE_BYTES:
            raise AccountError("保存的内容过大")
        now = time.time()
        expires_at = expires_at if expires_at is not None else now + workspace_ttl_seconds()
        nonce = secrets.token_bytes(12)
        header = {"v": 1, "saved_at": now, "expires_at": expires_at, "nonce": _b64(nonce)}
        aad = f"{user_id}|{expires_at!r}".encode()
        ciphertext = AESGCM(key).encrypt(nonce, data, aad)
        path = self._workspace_file(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix="workspace.", suffix=".tmp")
        with os.fdopen(handle, "wb") as stream:
            stream.write(json.dumps(header).encode("ascii") + b"\n" + ciphertext)
        os.replace(temp, path)
        return expires_at

    def save_workspace(self, session: Session, data: bytes) -> dict:
        with self.lock:
            expires_at = self._save_workspace_locked(session.user_id, session.key, data)
        return {"saved_at": time.time(), "expires_at": expires_at}

    def _load_workspace_locked(self, user_id: str, key: bytes) -> tuple[bytes, float] | None:
        path = self._workspace_file(user_id)
        try:
            raw = path.read_bytes()
            header_line, ciphertext = raw.split(b"\n", 1)
            header = json.loads(header_line)
        except (OSError, ValueError):
            return None
        if float(header.get("expires_at", 0)) <= time.time():
            self._unlink(path)
            return None
        try:
            data = AESGCM(key).decrypt(_unb64(header["nonce"]), ciphertext, f"{user_id}|{float(header['expires_at'])!r}".encode())
        except (InvalidTag, KeyError, ValueError):
            return None
        return data, float(header["expires_at"])

    def load_workspace(self, session: Session) -> dict | None:
        with self.lock:
            loaded = self._load_workspace_locked(session.user_id, session.key)
        if loaded is None:
            return None
        return {"data": loaded[0], "expires_at": loaded[1]}

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def clear_workspace(self, user_id: str) -> None:
        self._unlink(self._workspace_file(user_id))

    def purge_expired_workspaces(self) -> int:
        """Delete every doctor's saved work whose time is up (the expiry is readable without the key)."""
        removed = 0
        with self.lock:
            for path in (self.home / "users").glob("*/workspace.bin"):
                try:
                    header = json.loads(path.read_bytes().split(b"\n", 1)[0])
                    expired = float(header.get("expires_at", 0)) <= time.time()
                except (OSError, ValueError):
                    expired = True
                if expired:
                    self._unlink(path)
                    removed += 1
        return removed


_store: Store | None = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store(home_dir())
        return _store


def reset_store(home: Path | None = None) -> Store:
    """For tests and for pointing the service at another folder."""
    global _store
    with _store_lock:
        _store = Store(home or home_dir())
        return _store

"""Login for doctors who share a computer: accounts, one session at a time, admin-only routes, and the encrypted,
time-limited saved work. The login is switched ON here (the rest of the suite runs with it off)."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import accounts, auth_api
from server import app as srv

PW = "correct-horse-1"
PW2 = "another-good-pass-2"


class AuthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {"BINGLI_AUTH": "1", "BINGLI_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = accounts.reset_store(self.home)
        self.addCleanup(lambda: accounts.reset_store(Path(tempfile.gettempdir()) / "bingli_auth_unused"))
        self.addCleanup(self.tmp.cleanup)
        self.client = TestClient(srv.app, headers={"Origin": "http://testserver"})

    # ------------------------------------------------------------------ helpers
    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def setup_admin(self, name="admin1", password=PW):
        response = self.client.post("/auth/setup", json={"username": name, "password": password, "display_name": "管理员"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]

    def add_doctor(self, admin_token, name="doctor1", password=PW, role="doctor"):
        response = self.client.post("/auth/users", headers=self.auth(admin_token), json={"username": name, "password": password, "role": role})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["user"]

    def login(self, name, password=PW):
        response = self.client.post("/auth/login", json={"username": name, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["token"]


class Setup(AuthCase):
    def test_before_setup_everything_but_the_open_routes_is_closed(self):
        self.assertTrue(self.client.get("/auth/status").json()["setup_required"])
        for path in ("/hotwords", "/specialties", "/workspace", "/hotword-packs", "/asr/config", "/diagnostics/self-check"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for path in ("/health", "/service/status", "/auth/status"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_the_first_account_is_the_administrator_and_setup_works_once(self):
        token = self.setup_admin()
        me = self.client.get("/auth/status", headers=self.auth(token)).json()
        self.assertEqual((me["authenticated"], me["user"]["role"], me["setup_required"]), (True, "admin", False))
        again = self.client.post("/auth/setup", json={"username": "intruder", "password": PW})
        self.assertEqual(again.status_code, 409)

    def test_password_rules(self):
        for bad in ("short1", "admin1admin1"[:6], "aaaaaaaaaa", ""):
            response = self.client.post("/auth/setup", json={"username": "admin1", "password": bad})
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(self.client.post("/auth/setup", json={"username": "Admin1", "password": "admin1"}).status_code, 400)
        self.assertFalse(self.store.has_users())

    def test_user_name_rules(self):
        for bad in ("a", "has space", "x" * 21, "bad/name", ""):
            self.assertEqual(self.client.post("/auth/setup", json={"username": bad, "password": PW}).status_code, 400, bad)
        self.assertEqual(self.client.post("/auth/setup", json={"username": "张医生", "password": PW}).status_code, 200)


class Login(AuthCase):
    def test_wrong_name_wrong_password_and_disabled_all_look_the_same(self):
        admin = self.setup_admin()
        user = self.add_doctor(admin)
        self.client.post(f"/auth/users/{user['id']}/update", headers=self.auth(admin), json={"disabled": True})
        messages = set()
        for name, password in (("nobody", PW), ("admin1", "wrong-password"), ("doctor1", PW)):
            response = self.client.post("/auth/login", json={"username": name, "password": password})
            self.assertEqual(response.status_code, 401, name)
            messages.add(response.json()["detail"])
        self.assertEqual(len(messages), 1)

    def test_repeated_failures_lock_the_account_for_a_while(self):
        self.setup_admin()
        for _ in range(accounts.FAILURES_BEFORE_LOCK):
            self.client.post("/auth/login", json={"username": "admin1", "password": "wrong-password"})
        locked = self.client.post("/auth/login", json={"username": "admin1", "password": PW})
        self.assertEqual(locked.status_code, 429)
        self.assertGreater(int(locked.headers["Retry-After"]), 0)
        with mock.patch.object(accounts.time, "time", return_value=time.time() + 120):
            self.assertEqual(self.client.post("/auth/login", json={"username": "admin1", "password": PW}).status_code, 200)

    def test_a_good_login_resets_the_failure_count(self):
        self.setup_admin()
        for _ in range(accounts.FAILURES_BEFORE_LOCK - 1):
            self.client.post("/auth/login", json={"username": "admin1", "password": "wrong-password"})
        self.login("admin1")
        for _ in range(accounts.FAILURES_BEFORE_LOCK - 1):
            self.client.post("/auth/login", json={"username": "admin1", "password": "wrong-password"})
        self.assertEqual(self.client.post("/auth/login", json={"username": "admin1", "password": PW}).status_code, 200)

    def test_user_names_are_not_case_sensitive(self):
        self.setup_admin("Admin1")
        self.assertEqual(self.client.post("/auth/login", json={"username": "ADMIN1", "password": PW}).status_code, 200)

    def test_a_token_opens_protected_routes_and_a_wrong_one_does_not(self):
        token = self.setup_admin()
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(token)).status_code, 200)
        self.assertEqual(self.client.get("/hotwords", headers=self.auth("not-a-token")).status_code, 401)
        self.assertEqual(self.client.get("/hotwords", headers={"Authorization": token}).status_code, 401)   # no "Bearer"

    def test_logout_ends_the_session(self):
        token = self.setup_admin()
        self.assertEqual(self.client.post("/auth/logout", headers=self.auth(token), json={}).status_code, 200)
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(token)).status_code, 401)

    def test_only_one_session_at_a_time(self):
        admin = self.setup_admin()
        self.add_doctor(admin)
        doctor = self.login("doctor1")
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(admin)).status_code, 401, "the earlier session must have ended")
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(doctor)).status_code, 200)

    def test_an_idle_session_expires_and_touch_keeps_it_alive(self):
        token = self.setup_admin()
        base = time.time()
        with mock.patch.object(accounts.time, "time", return_value=base + accounts.idle_seconds() - 30):
            self.assertEqual(self.client.post("/auth/touch", headers=self.auth(token)).status_code, 200)
        with mock.patch.object(accounts.time, "time", return_value=base + 2 * accounts.idle_seconds() - 60):
            self.assertEqual(self.client.get("/hotwords", headers=self.auth(token)).status_code, 200)
        with mock.patch.object(accounts.time, "time", return_value=base + 4 * accounts.idle_seconds()):
            self.assertEqual(self.client.get("/hotwords", headers=self.auth(token)).status_code, 401)

    def test_polling_open_routes_does_not_keep_a_session_alive(self):
        token = self.setup_admin()
        base = time.time()
        with mock.patch.object(accounts.time, "time", return_value=base + accounts.idle_seconds() - 5):
            self.client.get("/health", headers=self.auth(token))
            self.client.get("/auth/status", headers=self.auth(token))
        with mock.patch.object(accounts.time, "time", return_value=base + accounts.idle_seconds() + 30):
            self.assertEqual(self.client.get("/hotwords", headers=self.auth(token)).status_code, 401)

    def test_change_password(self):
        token = self.setup_admin()
        self.assertEqual(self.client.post("/auth/password", headers=self.auth(token), json={"old_password": "wrong-one-xx", "new_password": PW2}).status_code, 401)
        self.assertEqual(self.client.post("/auth/password", headers=self.auth(token), json={"old_password": PW, "new_password": "short"}).status_code, 400)
        self.assertEqual(self.client.post("/auth/password", headers=self.auth(token), json={"old_password": PW, "new_password": PW2}).status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"username": "admin1", "password": PW}).status_code, 401)
        self.assertEqual(self.client.post("/auth/login", json={"username": "admin1", "password": PW2}).status_code, 200)

    def test_the_login_can_be_switched_off_for_development(self):
        with mock.patch.dict(os.environ, {"BINGLI_AUTH": "0"}):
            self.assertEqual(self.client.get("/hotwords").status_code, 200)

    def test_a_foreign_page_is_refused_even_with_a_valid_token(self):
        token = self.setup_admin()
        foreign = TestClient(srv.app, headers={"Origin": "https://evil.example"})
        self.assertEqual(foreign.get("/hotwords", headers=self.auth(token)).status_code, 403)
        self.assertEqual(foreign.post("/auth/login", json={"username": "admin1", "password": PW}).status_code, 403)

    def test_service_stop_and_status_stay_open_for_the_launcher(self):
        self.assertEqual(self.client.get("/service/status").status_code, 200)
        with mock.patch.object(srv.service_control, "terminate"):
            response = self.client.post("/service/shutdown", headers={"X-Bingli-Control": "1"})
        self.assertNotEqual(response.status_code, 401)


class Roles(AuthCase):
    ADMIN_ROUTES = (("get", "/auth/users"), ("get", "/auth/audit"), ("get", "/feedback/export"), ("post", "/hotword-miner/clear"),
                    ("post", "/hotword-miner/scan"), ("post", "/hotword-miner/build"), ("post", "/hotword-miner/upload"))

    def test_doctors_cannot_use_administrator_routes(self):
        admin = self.setup_admin()
        self.add_doctor(admin)
        doctor = self.login("doctor1")
        for method, path in self.ADMIN_ROUTES:
            response = getattr(self.client, method)(path, headers=self.auth(doctor), **({"json": {}} if method == "post" and "upload" not in path else {}))
            self.assertEqual(response.status_code, 403, path)
        admin = self.login("admin1")
        self.assertEqual(self.client.get("/auth/users", headers=self.auth(admin)).status_code, 200)
        self.assertEqual(self.client.get("/feedback/export", headers=self.auth(admin)).status_code, 200)

    def test_every_route_is_closed_without_a_login(self):
        open_paths = auth_api.OPEN_PATHS
        for route in srv.app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", None)
            if not methods or path in open_paths or path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc") or path == "/app" or path.startswith("/app/"):   # /app: the page itself (tests/test_webapp.py)
                continue
            concrete = path.replace("{user_id}", "0" * 32)
            for method in methods - {"HEAD", "OPTIONS"}:
                response = self.client.request(method, concrete, json={} if method in ("POST", "PUT") else None)
                self.assertEqual(response.status_code, 401, f"{method} {path}")

    def test_user_management(self):
        admin = self.setup_admin()
        user = self.add_doctor(admin)
        duplicate = self.client.post("/auth/users", headers=self.auth(admin), json={"username": "DOCTOR1", "password": PW})
        self.assertEqual(duplicate.status_code, 400)
        listed = self.client.get("/auth/users", headers=self.auth(admin)).json()["users"]
        self.assertEqual({u["username"] for u in listed}, {"admin1", "doctor1"})
        self.assertFalse(any("verifier" in u or "salt" in u for u in listed), "hashes must never be listed")
        updated = self.client.post(f"/auth/users/{user['id']}/update", headers=self.auth(admin), json={"display_name": "李医生"})
        self.assertEqual(updated.json()["user"]["display_name"], "李医生")
        deleted = self.client.post(f"/auth/users/{user['id']}/delete", headers=self.auth(admin))
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"username": "doctor1", "password": PW}).status_code, 401)

    def test_the_last_administrator_cannot_be_removed_demoted_or_disabled(self):
        admin = self.setup_admin()
        me = self.client.get("/auth/users", headers=self.auth(admin)).json()["users"][0]
        for path, body in ((f"/auth/users/{me['id']}/delete", None), (f"/auth/users/{me['id']}/update", {"role": "doctor"}),
                           (f"/auth/users/{me['id']}/update", {"disabled": True})):
            response = self.client.post(path, headers=self.auth(admin), **({"json": body} if body else {}))
            self.assertEqual(response.status_code, 400, path)
        second = self.add_doctor(admin, "admin2", role="admin")
        self.assertEqual(self.client.post(f"/auth/users/{second['id']}/delete", headers=self.auth(admin)).status_code, 200)

    def test_disabling_a_user_ends_their_session(self):
        admin = self.setup_admin()
        user = self.add_doctor(admin)
        doctor = self.login("doctor1")
        admin = self.login("admin1")
        self.client.post(f"/auth/users/{user['id']}/update", headers=self.auth(admin), json={"disabled": True})
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(doctor)).status_code, 401)

    def test_the_audit_log_records_events_and_never_a_password(self):
        admin = self.setup_admin()
        self.client.post("/auth/login", json={"username": "admin1", "password": "wrong-password-zz"})
        items = self.client.get("/auth/audit", headers=self.auth(admin)).json()["items"]
        events = {(i["event"], i["ok"]) for i in items}
        self.assertIn(("login", True), events)
        self.assertIn(("login_failed", False), events)
        raw = (self.home / "audit.log").read_text(encoding="utf-8")
        for secret in (PW, "wrong-password-zz"):
            self.assertNotIn(secret, raw)
        self.assertNotIn(PW, (self.home / "accounts.json").read_text(encoding="utf-8"))


class SavedWork(AuthCase):
    MARK = "患者张三的草稿-UNIQUE-MARKER"

    def put(self, token, text=None):
        return self.client.put("/workspace", headers=self.auth(token), json={"data": json.dumps({"draft": text or self.MARK})})

    def test_saved_work_round_trips_and_is_encrypted_on_disk(self):
        token = self.setup_admin()
        self.assertEqual(self.client.get("/workspace", headers=self.auth(token)).json(), {"exists": False})
        self.assertEqual(self.put(token).status_code, 200)
        got = self.client.get("/workspace", headers=self.auth(token)).json()
        self.assertEqual(json.loads(got["data"])["draft"], self.MARK)
        raw = b"".join(p.read_bytes() for p in self.home.rglob("workspace.bin"))
        self.assertTrue(raw)
        self.assertNotIn("UNIQUE-MARKER".encode(), raw)
        self.assertNotIn(self.MARK.encode("utf-8"), raw)

    def test_another_doctor_cannot_see_it(self):
        admin = self.setup_admin()
        self.add_doctor(admin)
        self.put(admin)
        other = self.login("doctor1")
        self.assertEqual(self.client.get("/workspace", headers=self.auth(other)).json(), {"exists": False})
        self.put(other, "doctor one's own text")
        back = self.login("admin1")
        self.assertEqual(json.loads(self.client.get("/workspace", headers=self.auth(back)).json()["data"])["draft"], self.MARK)

    def test_it_survives_a_restart_of_the_service_and_a_new_login(self):
        token = self.setup_admin()
        self.put(token)
        accounts.reset_store(self.home)                       # the service was restarted: sessions and keys are gone
        self.assertEqual(self.client.get("/workspace", headers=self.auth(token)).status_code, 401)
        again = self.login("admin1")
        self.assertEqual(json.loads(self.client.get("/workspace", headers=self.auth(again)).json()["data"])["draft"], self.MARK)

    def test_it_is_deleted_after_the_time_limit(self):
        token = self.setup_admin()
        self.put(token)
        files = list(self.home.rglob("workspace.bin"))
        self.assertEqual(len(files), 1)
        with mock.patch.object(accounts.time, "time", return_value=time.time() + accounts.workspace_ttl_seconds() + 60):
            fresh = self.login("admin1")
            self.assertEqual(self.client.get("/workspace", headers=self.auth(fresh)).json(), {"exists": False})
        self.assertFalse(files[0].exists(), "expired work must be removed from the disk")

    def test_expired_work_of_other_doctors_is_purged_at_any_login(self):
        admin = self.setup_admin()
        self.add_doctor(admin)
        doctor = self.login("doctor1")
        self.put(doctor)
        with mock.patch.object(accounts.time, "time", return_value=time.time() + accounts.workspace_ttl_seconds() + 60):
            self.login("admin1")
        self.assertEqual(list(self.home.rglob("workspace.bin")), [])

    def test_a_tampered_file_is_not_decrypted(self):
        token = self.setup_admin()
        self.put(token)
        path = next(self.home.rglob("workspace.bin"))
        raw = bytearray(path.read_bytes())
        raw[-3] ^= 0xFF
        path.write_bytes(bytes(raw))
        self.assertEqual(self.client.get("/workspace", headers=self.auth(token)).json(), {"exists": False})

    def test_a_workspace_copied_to_another_doctor_cannot_be_read(self):
        admin = self.setup_admin()
        other = self.add_doctor(admin)
        self.put(admin)
        source = next(self.home.rglob("workspace.bin"))
        target = self.home / "users" / other["id"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "workspace.bin").write_bytes(source.read_bytes())
        token = self.login("doctor1")
        self.assertEqual(self.client.get("/workspace", headers=self.auth(token)).json(), {"exists": False})

    def test_changing_the_password_keeps_the_saved_work(self):
        token = self.setup_admin()
        self.put(token)
        self.client.post("/auth/password", headers=self.auth(token), json={"old_password": PW, "new_password": PW2})
        self.assertEqual(json.loads(self.client.get("/workspace", headers=self.auth(token)).json()["data"])["draft"], self.MARK)
        fresh = self.login("admin1", PW2)
        self.assertEqual(json.loads(self.client.get("/workspace", headers=self.auth(fresh)).json()["data"])["draft"], self.MARK)

    def test_an_administrator_reset_deletes_the_saved_work(self):
        admin = self.setup_admin()
        user = self.add_doctor(admin)
        doctor = self.login("doctor1")
        self.put(doctor)
        admin = self.login("admin1")
        reset = self.client.post(f"/auth/users/{user['id']}/reset-password", headers=self.auth(admin), json={"password": PW2})
        self.assertEqual(reset.status_code, 200)
        self.assertEqual(self.client.post("/auth/login", json={"username": "doctor1", "password": PW}).status_code, 401)
        fresh = self.login("doctor1", PW2)
        self.assertEqual(self.client.get("/workspace", headers=self.auth(fresh)).json(), {"exists": False})

    def test_logout_keeps_the_work_unless_asked_to_clear_it(self):
        token = self.setup_admin()
        self.put(token)
        self.client.post("/auth/logout", headers=self.auth(token), json={})
        self.assertTrue(list(self.home.rglob("workspace.bin")))
        token = self.login("admin1")
        self.assertTrue(self.client.get("/workspace", headers=self.auth(token)).json()["exists"])
        self.client.post("/auth/logout", headers=self.auth(token), json={"clear_workspace": True})
        self.assertEqual(list(self.home.rglob("workspace.bin")), [])

    def test_clear_endpoint_and_size_limit(self):
        token = self.setup_admin()
        self.put(token)
        self.assertEqual(self.client.post("/workspace/clear", headers=self.auth(token)).status_code, 200)
        self.assertEqual(self.client.get("/workspace", headers=self.auth(token)).json(), {"exists": False})
        with mock.patch.object(accounts, "MAX_WORKSPACE_BYTES", 10):
            self.assertIn(self.client.put("/workspace", headers=self.auth(token), json={"data": "x" * 50}).status_code, (400, 422))


class PerDoctorData(AuthCase):
    def test_custom_hotwords_are_separate_for_each_doctor(self):
        admin = self.setup_admin()
        self.add_doctor(admin)
        self.client.put("/hotwords", headers=self.auth(admin), json={"words": ["甲专属词"]})
        doctor = self.login("doctor1")
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(doctor)).json()["words"], [])
        self.client.put("/hotwords", headers=self.auth(doctor), json={"words": ["乙专属词"]})
        back = self.login("admin1")
        self.assertEqual(self.client.get("/hotwords", headers=self.auth(back)).json()["words"], ["甲专属词"])
        legacy = Path(srv.USER_HOTWORD_FILE)
        self.assertNotIn("甲专属词", legacy.read_text(encoding="utf-8") if legacy.exists() else "")

    def test_the_recognition_hotwords_use_the_logged_in_doctors_list(self):
        admin = self.setup_admin()
        self.client.put("/hotwords", headers=self.auth(admin), json={"words": ["甲专属词"]})
        packs = self.client.get("/hotword-packs", headers=self.auth(admin)).json()["packs"]
        self.assertEqual(next(p for p in packs if p["id"] == "user_custom")["count"], 1)
        self.add_doctor(admin)
        doctor = self.login("doctor1")
        packs = self.client.get("/hotword-packs", headers=self.auth(doctor)).json()["packs"]
        self.assertEqual(next(p for p in packs if p["id"] == "user_custom")["count"], 0)


class WebSocketLogin(AuthCase):
    def test_a_websocket_needs_the_token_as_a_sub_protocol(self):
        token = self.setup_admin()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/transcribe"):
                pass
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("/ws/transcribe", subprotocols=["bingli.v1", "token.wrong"]):
                pass
        with self.client.websocket_connect("/ws/transcribe", subprotocols=["bingli.v1", f"token.{token}"]) as socket:
            self.assertEqual(socket.accepted_subprotocol, "bingli.v1")

    def test_a_token_in_the_url_is_not_accepted(self):
        token = self.setup_admin()
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(f"/ws/transcribe?token={token}"):
                pass


if __name__ == "__main__":
    unittest.main()

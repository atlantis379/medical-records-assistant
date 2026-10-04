"""Origin / Host checks for the local service (server/security.py)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import app as srv
from server import security

_SAVED_PIN = os.environ.pop("BINGLI_ALLOWED_EXTENSIONS", None)    # the tests below assume the shipped allow-list
REAL_ID = sorted(security.pinned_extension_ids())[0]              # the ID that manifest.json's key gives the extension
EXT = f"chrome-extension://{REAL_ID}"
OTHER_EXT = "chrome-extension://ponmlkjihgfedcbaponmlkjihgfedcba"
OWN = "http://testserver"
EVIL = "https://evil.example"
client = TestClient(srv.app)


def check(**overrides):
    values = {"kind": "http", "method": "GET", "client_host": "127.0.0.1", "host_header": "127.0.0.1:8765", "origin": None}
    values.update(overrides)
    return security.check(**values)[0]


class DecisionTable(unittest.TestCase):
    def test_reads_without_an_origin_are_allowed(self):
        for method in ("GET", "HEAD", "OPTIONS"):
            self.assertTrue(check(method=method))

    def test_changes_need_an_origin(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            self.assertFalse(check(method=method), method)
            self.assertTrue(check(method=method, origin=EXT), method)

    def test_websocket_needs_an_origin(self):
        self.assertFalse(check(kind="websocket"))
        self.assertTrue(check(kind="websocket", origin=EXT))
        self.assertFalse(check(kind="websocket", origin=EVIL))

    def test_the_extension_and_the_status_page_are_the_only_origins(self):
        for origin in (EXT, f"edge-extension://{REAL_ID}", "http://127.0.0.1:8765"):
            self.assertTrue(check(method="POST", origin=origin), origin)
        for origin in (EVIL, "http://evil.example", "http://127.0.0.1:9999", "http://localhost:8765", "null", "file://",
                       "chrome-extension://", "chrome-extension://abc/def", "https://chrome-extension.evil.example",
                       "moz-extension://abcdef", "http://127.0.0.1:8765.evil.example", ""):
            self.assertFalse(check(method="POST", origin=origin), origin)

    def test_reads_that_the_browser_says_came_from_another_site_are_refused(self):
        for site in ("cross-site", "same-site", "Cross-Site"):
            self.assertFalse(check(fetch_site=site), site)
        for site in ("none", "same-origin", None, ""):
            self.assertTrue(check(fetch_site=site), site)

    def test_a_present_origin_is_checked_even_on_reads(self):
        self.assertFalse(check(origin=EVIL))
        self.assertTrue(check(origin=EXT))

    def test_the_host_header_must_be_a_loopback_name(self):
        for host in ("127.0.0.1:8765", "localhost:8765", "[::1]:8765", "127.0.0.1", "LOCALHOST:8765"):
            self.assertTrue(check(host_header=host), host)
        for host in ("evil.example", "evil.example:8765", "192.168.1.5:8765", "", "127.0.0.1.evil.example:8765"):
            self.assertFalse(check(host_header=host), host)

    def test_dns_rebinding_is_stopped_even_when_origin_and_host_agree(self):
        self.assertFalse(check(method="POST", host_header="rebind.example:8765", origin="http://rebind.example:8765"))
        self.assertFalse(check(host_header="rebind.example:8765"))

    def test_only_local_clients(self):
        self.assertFalse(check(client_host="192.168.1.20", origin=EXT))
        self.assertTrue(check(client_host="::1"))

    def test_host_name_parsing(self):
        self.assertEqual(security.host_name("127.0.0.1:8765"), "127.0.0.1")
        self.assertEqual(security.host_name("[::1]:8765"), "[::1]")
        self.assertEqual(security.host_name("localhost"), "localhost")


class PinnedExtension(unittest.TestCase):
    def test_by_default_only_the_shipped_extension_is_accepted(self):
        self.assertTrue(check(method="POST", origin=EXT))
        self.assertFalse(check(method="POST", origin=OTHER_EXT))
        self.assertFalse(check(kind="websocket", origin=OTHER_EXT))
        self.assertTrue(check(method="POST", origin="http://127.0.0.1:8765"))     # the status page still works

    def test_the_environment_overrides_the_shipped_list(self):
        with mock.patch.dict(os.environ, {"BINGLI_ALLOWED_EXTENSIONS": " aaaa , bbbb "}):
            self.assertEqual(security.pinned_extension_ids(), {"aaaa", "bbbb"})
            self.assertTrue(check(method="POST", origin="chrome-extension://bbbb"))
            self.assertFalse(check(method="POST", origin=EXT))               # no longer the shipped one

    def test_a_star_accepts_any_extension(self):
        with mock.patch.dict(os.environ, {"BINGLI_ALLOWED_EXTENSIONS": "*"}):
            self.assertEqual(security.pinned_extension_ids(), set())
            self.assertTrue(check(method="POST", origin=OTHER_EXT))

    def test_an_empty_list_in_the_file_accepts_any_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowed.json"
            path.write_text(json.dumps({"ids": []}), encoding="utf-8")
            with mock.patch.object(security, "ALLOWED_FILE", path):
                self.assertEqual(security.pinned_extension_ids(), set())
                self.assertTrue(check(method="POST", origin=OTHER_EXT))

    def test_a_store_id_can_be_added_by_editing_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowed.json"
            path.write_text(json.dumps({"ids": [REAL_ID, "storeidstoreidstoreidstoreidstoreid"]}), encoding="utf-8")
            with mock.patch.object(security, "ALLOWED_FILE", path):
                self.assertTrue(check(method="POST", origin="chrome-extension://storeidstoreidstoreidstoreidstoreid"))
                self.assertTrue(check(method="POST", origin=EXT))
                self.assertFalse(check(method="POST", origin=OTHER_EXT))

    def test_an_unreadable_file_falls_back_to_accepting_any_extension_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowed.json"
            path.write_text("{not json", encoding="utf-8")
            with mock.patch.object(security, "ALLOWED_FILE", path), self.assertLogs("uvicorn.error", level="WARNING") as logs:
                self.assertEqual(security.pinned_extension_ids(), set())
            self.assertTrue(any("unreadable" in line for line in logs.output))


class ShippedExtensionId(unittest.TestCase):
    """The fixed ID: manifest.json's key must really produce the ID the service accepts."""

    def manifest(self):
        return json.loads((Path(security.ALLOWED_FILE).parents[2] / "extension" / "manifest.json").read_text(encoding="utf-8"))

    def test_the_manifest_key_produces_the_shipped_id(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("extension_id", Path(security.ALLOWED_FILE).parents[2] / "scripts" / "extension_id.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.extension_id_from_manifest_key(self.manifest()["key"]), REAL_ID)

    def test_the_key_is_a_public_rsa_key_not_a_private_one(self):
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        der = base64.b64decode(self.manifest()["key"])
        public = serialization.load_der_public_key(der)
        self.assertIsInstance(public, rsa.RSAPublicKey)
        self.assertGreaterEqual(public.key_size, 2048)
        with self.assertRaises(ValueError):
            serialization.load_der_private_key(der, password=None)

    def test_the_id_has_the_shape_browsers_use(self):
        self.assertRegex(REAL_ID, r"^[a-p]{32}$")

    def test_no_private_key_is_in_the_repository(self):
        root = Path(security.ALLOWED_FILE).parents[2]
        for path in root.rglob("*"):
            if {".venv", "dist", "build", "node_modules", ".git"} & set(path.parts):   # build outputs and environments are not the repository
                continue
            if path.is_file() and path.suffix in (".pem", ".key"):
                self.fail(f"private key material in the repository: {path}")
            if path.is_file() and path.suffix in (".py", ".json", ".md", ".js", ".ps1", ".cs") and path.stat().st_size < 2_000_000:
                self.assertNotIn("BEGIN PRIVATE KEY", path.read_text(encoding="utf-8", errors="ignore") if path.name != "test_security.py" else "", str(path))
            if path.name == ".gitignore":
                self.assertIn("*.pem", path.read_text(encoding="utf-8"))


def http_routes():
    """Every HTTP route of the app, so an endpoint added later is covered without touching this test."""
    for route in srv.app.routes:
        methods = getattr(route, "methods", None)
        if methods and not route.path.startswith(("/docs", "/redoc", "/openapi")):
            for method in sorted(methods - {"HEAD", "OPTIONS"}):
                yield method, route.path


class EveryEndpoint(unittest.TestCase):
    def call(self, method, path, headers):
        return client.request(method, path, headers=headers)

    def test_the_guard_is_the_outermost_middleware(self):
        self.assertIs(srv.app.user_middleware[0].cls, security.OriginGuard)

    def test_the_route_list_is_not_empty(self):
        self.assertGreater(len(list(http_routes())), 15)

    def test_a_foreign_web_page_is_refused_everywhere(self):
        for method, path in http_routes():
            response = self.call(method, path, {"Origin": EVIL})
            self.assertEqual(response.status_code, 403, f"{method} {path}")

    def test_a_rebinding_host_is_refused_everywhere(self):
        for method, path in http_routes():
            for headers in ({"Host": "evil.example"}, {"Host": "evil.example", "Origin": "http://evil.example"}):
                response = self.call(method, path, headers)
                self.assertEqual(response.status_code, 403, f"{method} {path} {headers}")

    def test_changes_without_an_origin_are_refused(self):
        for method, path in http_routes():
            if method in ("POST", "PUT", "DELETE", "PATCH"):
                self.assertEqual(self.call(method, path, {}).status_code, 403, f"{method} {path}")

    def test_the_extension_gets_through_to_the_endpoint(self):
        # a 403 would mean the guard blocked it; the endpoint itself may answer 200, 400, 405 or 422
        for method, path in http_routes():
            if method == "POST" and path == "/service/shutdown":
                continue
            response = self.call(method, path, {"Origin": EXT})
            self.assertNotEqual(response.status_code, 403, f"{method} {path}")

    def test_plain_reads_without_an_origin_still_work(self):
        for path in ("/health", "/service/status", "/", "/specialties", "/license/status"):
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_a_cross_site_read_without_an_origin_is_refused(self):
        # what an <img> or <script> tag on a hostile page sends: no Origin, but Sec-Fetch-Site: cross-site
        for path in ("/health", "/hotwords", "/feedback/export", "/diagnostics/self-check"):
            self.assertEqual(client.get(path, headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403, path)
            self.assertEqual(client.get(path, headers={"Sec-Fetch-Site": "none"}).status_code, 200, path)

    def test_the_refusal_says_why_and_leaks_nothing(self):
        response = client.post("/feedback", json={"message": "hello there"}, headers={"Origin": EVIL})
        self.assertEqual(response.status_code, 403)
        self.assertIn("origin not allowed", response.json()["detail"])
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_preflight_is_only_granted_to_the_extension(self):
        asking = {"Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"}
        ok = client.options("/hotwords", headers={"Origin": EXT, **asking})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers.get("access-control-allow-origin"), EXT)
        refused = client.options("/hotwords", headers={"Origin": EVIL, **asking})
        self.assertEqual(refused.status_code, 403)
        self.assertNotIn("access-control-allow-origin", refused.headers)

    def test_a_refused_request_changes_nothing(self):
        before = srv.read_custom_hotwords()
        response = client.post("/hotword-packs/import", json={"words": ["不应写入的词"]}, headers={"Origin": EVIL})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(srv.read_custom_hotwords(), before)


class WebSocketGuard(unittest.TestCase):
    def connect(self, headers):
        seen = []

        async def fake_session(websocket):
            await websocket.accept()
            seen.append(True)
            await websocket.close()

        with mock.patch.object(srv, "_ws_transcribe_session", fake_session):
            try:
                with client.websocket_connect("/ws/transcribe", headers=headers):
                    pass
            except WebSocketDisconnect as error:
                return False, error.code
        return bool(seen), None

    def test_no_origin_is_refused(self):
        self.assertEqual(self.connect({}), (False, 1008))

    def test_a_foreign_page_is_refused(self):
        self.assertEqual(self.connect({"Origin": EVIL}), (False, 1008))
        self.assertEqual(self.connect({"Origin": "null"}), (False, 1008))

    def test_a_rebinding_host_is_refused(self):
        self.assertEqual(self.connect({"Origin": "http://evil.example", "Host": "evil.example"}), (False, 1008))

    def test_the_extension_is_accepted(self):
        self.assertEqual(self.connect({"Origin": EXT}), (True, None))

    def test_a_refused_connection_is_not_counted_as_a_dictation(self):
        from server import service_control
        self.connect({"Origin": EVIL})
        self.assertEqual(service_control.active_streams(), 0)


if __name__ == "__main__":
    unittest.main()

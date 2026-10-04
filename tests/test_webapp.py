"""The dictation page served by the local service (/app/). No browser is started."""
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from server import app as srv
from server import auth_api, webapp

EXT = Path(__file__).resolve().parents[1] / "extension"


def own():
    return TestClient(srv.app, headers={"Origin": "http://testserver"})


class Serving(unittest.TestCase):
    def test_the_page_and_every_script_it_loads_are_served(self):
        client = own()
        page = client.get("/app/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.headers["content-type"])
        files = re.findall(r'(?:src|href)="([\w./-]+\.(?:js|css))"', page.text)
        self.assertIn("config.js", files)
        for name in files:
            response = client.get(f"/app/{name}")
            self.assertEqual(response.status_code, 200, name)
            self.assertEqual(response.content, (EXT / name).read_bytes(), name)
        for icon in ("icons/icon16.png", "icons/icon48.png", "icons/icon128.png"):
            self.assertEqual(client.get(f"/app/{icon}").status_code, 200, icon)

    def test_app_without_a_slash_redirects(self):
        response = own().get("/app", follow_redirects=False)
        self.assertEqual((response.status_code, response.headers["location"]), (307, "/app/"))

    def test_the_page_opens_without_a_login_but_the_data_does_not(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"BINGLI_AUTH": "1"}):
            client = own()
            self.assertEqual(client.get("/app/").status_code, 200)
            self.assertEqual(client.get("/app/editor.js").status_code, 200)
            self.assertEqual(client.get("/hotwords").status_code, 401)

    def test_extension_only_files_and_other_types_are_not_served(self):
        client = own()
        for name in ("manifest.json", "background.js", "_locales/zh_CN/messages.json", "editor.html.bak", "README.md"):
            self.assertEqual(client.get(f"/app/{name}").status_code, 404, name)

    def test_no_path_tricks(self):
        client = own()
        for name in ("../server/app.py", "..%2fserver%2fapp.py", "%2e%2e/server/app.py", "..%5cserver%5capp.py", "%5c..%5cserver","icons/../../server/app.py",
                     "editor.js%00.png", "icons/", "icons", "C:/Windows/win.ini", "//etc/passwd"):
            response = client.get(f"/app/{name}")
            self.assertNotEqual(response.status_code, 200, name)
            self.assertNotIn(b"FastAPI(", response.content, name)

    def test_security_headers(self):
        headers = own().get("/app/").headers
        csp = headers["content-security-policy"]
        for needed in ("default-src 'self'", "script-src 'self'", "object-src 'none'", "frame-ancestors 'none'"):
            self.assertIn(needed, csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotRegex(csp, r"script-src[^;]*unsafe-inline")
        self.assertEqual(headers["x-frame-options"], "DENY")
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertIn("microphone=(self)", headers["permissions-policy"])
        self.assertEqual(headers["cache-control"], "no-store")

    def test_the_page_needs_no_inline_script_or_remote_resource(self):
        html = (EXT / "editor.html").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html), [])
        self.assertNotRegex(html, r"\bon(click|change|load|submit|input)=")
        self.assertNotRegex(html, r'(?:src|href)="https?://')
        for name in ("editor.js", "auth.js", "miner.js", "fields.js"):
            self.assertNotRegex((EXT / name).read_text(encoding="utf-8"), r"https?://(?!127\.0\.0\.1|localhost)", name)


class Origins(unittest.TestCase):
    def test_a_foreign_site_cannot_load_or_frame_the_page(self):
        client = TestClient(srv.app)
        self.assertEqual(client.get("/app/", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(client.get("/app/", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(client.get("/app/editor.js", headers={"Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(client.get("/app/", headers={"Host": "evil.example"}).status_code, 403)

    def test_typing_the_address_works(self):
        self.assertEqual(TestClient(srv.app).get("/app/", headers={"Sec-Fetch-Site": "none"}).status_code, 200)

    def test_the_page_may_call_the_service_from_its_own_origin(self):
        client = TestClient(srv.app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"})
        self.assertEqual(client.post("/hotword-miner/clear", json={}).status_code, 200)


class Wiring(unittest.TestCase):
    def test_config_decides_where_the_service_is(self):
        config = (EXT / "config.js").read_text(encoding="utf-8")
        self.assertIn("location.origin", config)
        self.assertIn("SERVICE_WS", config)
        self.assertIn("SERVICE_BASE", (EXT / "editor.js").read_text(encoding="utf-8"))
        self.assertIn("SERVICE_BASE", (EXT / "auth.js").read_text(encoding="utf-8"))
        html = (EXT / "editor.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="config.js"'), html.index('src="auth.js"'))
        self.assertLess(html.index('src="config.js"'), html.index('src="editor.js"'))

    def test_the_launcher_opens_the_dictation_page(self):
        source = (Path(__file__).resolve().parents[1] / "packaging" / "windows" / "launcher" / "BingliLauncher.cs").read_text(encoding="utf-8")
        self.assertIn('OpenStatusPage("/app/")', source)
        self.assertIn("打开听写页面", source)

    def test_the_status_page_links_to_the_dictation_page(self):
        self.assertIn('href="/app/"', srv.service_control.STATUS_PAGE)

    def test_the_extension_file_list_matches_what_is_served(self):
        served = {p.name for p in EXT.iterdir() if p.is_file() and p.suffix in webapp.ALLOWED_SUFFIXES and p.name not in webapp.EXCLUDED}
        for name in served:
            self.assertIsNotNone(webapp.resolve(name), name)


if __name__ == "__main__":
    unittest.main()

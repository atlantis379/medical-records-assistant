"""Guards against version drift between the server, the extension and the docs."""
import json
import re
import unittest
from pathlib import Path

from server import app as srv

ROOT = Path(srv.APP_DIR).parent


class VersionConsistency(unittest.TestCase):
    def manifest(self):
        return json.loads((ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))

    def test_server_version_matches_extension_manifest(self):
        self.assertEqual(srv.APP_VERSION, self.manifest()["version"])
        self.assertEqual(srv.app.version, srv.APP_VERSION)

    def test_readme_title_matches_major_minor(self):
        major_minor = ".".join(srv.APP_VERSION.split(".")[:2])
        first_line = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[0]
        self.assertIn(f"v{major_minor}", first_line)

    def test_one_build_identifier_everywhere(self):
        from fastapi.testclient import TestClient
        client = TestClient(srv.app)
        self.assertEqual(client.get("/health").json()["build"], srv.APP_BUILD)
        self.assertEqual(srv.build_self_check()["app"]["build"], srv.APP_BUILD)

    def test_launch_scripts_do_not_hardcode_an_old_version(self):
        for name in ("start_server.bat", "start_server_gpu.bat"):
            text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
            self.assertIsNone(re.search(r"service v\d+\.\d+", text), name)

    def test_extension_and_server_agree_on_specialty_parameters(self):
        js = (ROOT / "extension" / "editor.js").read_text(encoding="utf-8")
        for token in ("/specialties", "specialties", "include_draft"):
            self.assertIn(token, js)
        # the old hard-coded department must not come back
        self.assertNotIn('department: "infectious_disease"', js)
        self.assertNotIn('"infectious_disease" : "general"', js)
        html = (ROOT / "extension" / "editor.html").read_text(encoding="utf-8")
        for element_id in ("specialtyList", "includeDraftToggle"):
            self.assertIn(f'id="{element_id}"', html)

    def test_manifest_permissions_stay_minimal(self):
        manifest = self.manifest()
        self.assertEqual(manifest["manifest_version"], 3)
        self.assertEqual(manifest["permissions"], ["storage"])
        self.assertEqual(manifest["host_permissions"], ["http://127.0.0.1:8765/*"])



class ExtensionShowsNumberNotices(unittest.TestCase):
    def test_extension_collects_and_displays_number_notices(self):
        js = (ROOT / "extension" / "editor.js").read_text(encoding="utf-8")
        for token in ("number_notices", "registerNumberNotices", "activeNumberNotices", "number_unresolved"):
            self.assertIn(token, js)


if __name__ == "__main__":
    unittest.main()

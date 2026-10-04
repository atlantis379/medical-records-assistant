"""Runs the JavaScript tests for extension/fields.js and, only when BINGLI_BROWSER_TESTS=1, the browser check.

The browser check starts Playwright's bundled Chromium, so an ordinary test run never does it. Never point it
at the installed Edge or Chrome.
"""
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
BROWSER_TESTS = os.environ.get("BINGLI_BROWSER_TESTS") == "1"
BROWSER_SKIP = "browser checks are off by default (they start a browser); set BINGLI_BROWSER_TESTS=1 to run them"


def playwright_python():
    """Playwright is not a project dependency; the offline package's Python happens to have it."""
    for candidate in sorted(ROOT.glob("dist/*/runtime/python/python.exe"), key=lambda p: p.stat().st_mtime, reverse=True):
        probe = subprocess.run([str(candidate), "-c", "import playwright"], capture_output=True)
        if probe.returncode == 0:
            return candidate
    return None


class FieldRouterLogic(unittest.TestCase):
    @unittest.skipUnless(NODE, "node is not installed")
    def test_javascript_unit_tests(self):
        result = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / "fields.test.js")], capture_output=True,
                                text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])


class FieldRoutingInTheBrowser(unittest.TestCase):
    @unittest.skipUnless(BROWSER_TESTS, BROWSER_SKIP)
    def test_editor_page_end_to_end(self):
        python = playwright_python()
        if python is None:
            self.skipTest("no Python with Playwright (the offline package's runtime has one)")
        result = subprocess.run([str(python), "-X", "utf-8", str(ROOT / "tests" / "ui" / "field_routing_check.py")],
                                capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=240)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1500:])
        self.assertIn("0 failure(s)", result.stdout)


class ExtensionWiring(unittest.TestCase):
    def test_page_loads_fields_before_the_editor_script(self):
        html = (ROOT / "extension" / "editor.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="fields.js"'), html.index('src="editor.js"'))
        for element_id in ("fieldBar", "fieldChips", "fieldRoutingToggle", "fieldCurrent"):
            self.assertIn(f'id="{element_id}"', html)

    def test_editor_uses_the_router_for_dictation_and_the_review_panel(self):
        js = (ROOT / "extension" / "editor.js").read_text(encoding="utf-8")
        for token in ("routeIntoFields", "FieldRouter.apply", "unassignedText", "emptyFields", "followCaretToField",
                      "field_routing"):
            self.assertIn(token, js)

    def test_extension_package_includes_the_new_file(self):
        script = (ROOT / "scripts" / "package_extension.ps1").read_text(encoding="utf-8")
        self.assertTrue("extension" in script)   # the whole folder is zipped, so fields.js travels with it
        self.assertTrue((ROOT / "extension" / "fields.js").exists())


if __name__ == "__main__":
    unittest.main()

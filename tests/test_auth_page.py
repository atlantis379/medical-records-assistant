"""The login is wired into the editor page. No browser is started: these are checks of the page's source and of the
pure logic in extension/auth_logic.js (run with Node)."""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension"
NODE = shutil.which("node")


def source(name):
    return (EXT / name).read_text(encoding="utf-8")


class PageWiring(unittest.TestCase):
    def test_every_element_auth_js_needs_exists_and_the_page_starts_locked(self):
        html, script = source("editor.html"), source("auth.js")
        ids = set(re.findall(r'\$\("(\w+)"\)', script)) | set(re.findall(r"getElementById\(\"(\w+)\"\)", script))
        missing = [i for i in sorted(ids) if f'id="{i}"' not in html]
        self.assertEqual(missing, [])
        self.assertIn('<body class="auth-locked">', html)
        self.assertIn("body.auth-locked .app-shell { visibility: hidden; }", source("editor.css"))

    def test_scripts_load_in_the_right_order(self):
        html = source("editor.html")
        order = [html.index(f'src="{name}"') for name in ("fields.js", "auth_logic.js", "auth.js", "editor.js", "miner_logic.js", "miner.js")]
        self.assertEqual(order, sorted(order))

    def test_the_editor_starts_only_after_the_login(self):
        editor = source("editor.js")
        self.assertIn("Auth.start().then(", editor)
        self.assertNotRegex(editor, r"\n(?!.*Auth\.start).*\binitialize\(\);\s*setInterval")

    def test_every_request_to_the_service_carries_the_token(self):
        for name in ("editor.js", "miner.js"):
            self.assertNotRegex(source(name), r"(?<![\w.])fetch\(`\$\{API_BASE\}", name)

    def test_the_websocket_sends_the_token_as_a_sub_protocol_not_in_the_url(self):
        editor = source("editor.js")
        self.assertIn("new WebSocket(WS_URL, Auth.socketProtocols())", editor)
        self.assertNotIn("token=", editor)
        self.assertIn("token.${state.token}", source("auth.js"))

    def test_the_draft_is_not_written_to_browser_storage_by_the_editor(self):
        editor = source("editor.js")
        self.assertNotIn("chrome.storage", editor)
        self.assertNotIn("localStorage", editor)
        self.assertIn("chrome.storage.session", source("auth.js"))          # the token: memory only

    def test_saved_work_keys_match_the_editor(self):
        editor = source("editor.js")
        keys = dict(re.findall(r'(draft|history|patients): "(\w+)"', editor.split("const STORAGE =")[1].split("\n")[0]))
        logic = source("auth_logic.js")
        for key in keys.values():
            self.assertIn(f'"{key}"', logic.split("WORKSPACE_KEYS = [")[1].split("]")[0], key)

    def test_the_miner_button_and_account_dialog_are_for_administrators(self):
        script = source("auth.js")
        self.assertIn('user.role !== "admin"', script)
        self.assertIn("openMinerButton", script)
        self.assertIn("accountsButton", script)

    def test_the_old_unencrypted_draft_is_moved_and_removed(self):
        script = source("auth.js")
        self.assertIn("migrateLegacy", script)
        self.assertIn("rawRemove(L.WORKSPACE_KEYS)", script)


class Layout(unittest.TestCase):
    def test_translated_elements_have_no_children_because_translating_replaces_the_text(self):
        html = source("editor.html")
        found = 0
        for match in re.finditer(r'<(\w+)[^>]*\bdata-i18n="\w+"[^>]*>(.*?)</\1>', html, re.S):
            found += 1
            self.assertNotIn("<", match.group(2), match.group(0)[:80])
        self.assertGreater(found, 20)                       # the pattern really matched the page

    def test_menus_close_on_outside_clicks_and_the_script_is_loaded(self):
        html = source("editor.html")
        self.assertIn('src="ui.js"', html)
        self.assertIn("details.menu", source("ui.js"))
        self.assertIn('class="menu settings-menu"', html)

    def test_the_stylesheet_adapts_to_narrow_windows_and_respects_reduced_motion(self):
        css = source("editor.css")
        self.assertIn("@media (max-width: 1180px)", css)
        self.assertIn("@media (max-width: 760px)", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn(":focus-visible", css)
        self.assertNotIn("min-width: 820px", css)       # the page no longer forces a wide window

    def test_every_class_the_script_adds_to_the_record_has_a_style(self):
        css = source("editor.css")
        for name in ("field-chip", "filled", "empty-heading", "current", "recording", "recording-active", "risk-item", "history-item",
                     "patient-tab", "trace-card", "specialty-option", "hotword-pack", "pack-badge"):
            self.assertIn(name, css, name)


class Logic(unittest.TestCase):
    @unittest.skipUnless(NODE, "node is not installed")
    def test_javascript_unit_tests(self):
        result = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / "auth_logic.test.js")], capture_output=True,
                                text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()

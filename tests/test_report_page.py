"""The import-report dialog is wired into the editor page (no browser is started)."""
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


class Wiring(unittest.TestCase):
    def test_every_element_report_js_needs_exists(self):
        html, script = source("editor.html"), source("report.js")
        ids = set(re.findall(r'\$r\("#(\w+)"\)', script)) | set(re.findall(r'getElementById\("(\w+)"\)', script))
        self.assertEqual([i for i in sorted(ids) if f'id="{i}"' not in html], [])

    def test_scripts_load_after_the_ones_they_use(self):
        html = source("editor.html")
        order = [html.index(f'src="{n}"') for n in ("editor.js", "miner_logic.js", "report_logic.js", "report.js")]
        self.assertEqual(order, sorted(order))

    def test_the_dialog_is_opened_from_the_more_menu(self):
        html = source("editor.html")
        self.assertLess(html.index('class="menu more-menu"'), html.index('id="openReportButton"'))
        self.assertIn('id="reportModal"', html)

    def test_pictures_stay_on_this_computer(self):
        script = source("report.js")
        self.assertNotRegex(script, r"https?://")
        self.assertIn("URL.createObjectURL", script)                  # the original is shown from the doctor's own file ...
        self.assertIn("URL.revokeObjectURL", script)                  # ... and released when the dialog closes
        self.assertIn('modal.addEventListener("close"', script)
        self.assertNotIn("localStorage", script)
        self.assertNotIn("chrome.storage", script)

    def test_nothing_is_written_without_the_button(self):
        script = source("report.js")
        self.assertEqual(script.count("els.draft.value ="), 1)         # only write() touches the draft
        self.assertIn("外院报告", script)

    def test_the_text_is_editable_and_the_picture_stays_next_to_it(self):
        html = source("editor.html")
        self.assertRegex(html, r'<textarea id="reportText"[^>]*aria-label')
        self.assertIn('id="reportImage"', html)

    def test_third_party_components_are_listed(self):
        notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
        for name in ("RapidOCR", "OpenCV", "onnxruntime"):
            self.assertIn(name, notices)

    def test_the_term_list_and_the_install_steps_exist(self):
        self.assertTrue((ROOT / "server" / "data" / "reference" / "imaging_terms.txt").is_file())
        script = (ROOT / "scripts" / "build_package_venv.ps1").read_text(encoding="utf-8")
        self.assertIn("rapidocr-onnxruntime", script)
        self.assertIn("--no-deps", script)
        self.assertIn("opencv_videoio_ffmpeg", script)
        self.assertIn("opencv-python-headless", (ROOT / "server" / "requirements.txt").read_text(encoding="utf-8"))


class Usability(unittest.TestCase):
    def test_the_only_abnormal_option_shows_what_it_does(self):
        html, script = source("editor.html"), source("report.js")
        self.assertIn('id="reportOnlyAbnormal"', html)
        self.assertIn('id="reportCount"', html)
        for needed in ("将写入", "skipped", "regenerate", "renderTable(item.result.table.rows)"):
            self.assertIn(needed, script)
        # the text box and the write button come before the long table, so the effect is seen without scrolling
        self.assertLess(html.index('id="reportText"'), html.index('id="reportTableBox"'))
        self.assertLess(html.index('id="reportWrite"'), html.index('id="reportTableBox"'))

    def test_review_prompts_can_be_clicked_to_find_the_text_in_the_draft(self):
        script = source("editor.js")
        for needed in ("function locateInDraft", "function draftOccurrences", "function draftOffsetTop", 'node.classList.add("locatable")',
                       'node.setAttribute("role", "button")', "setSelectionRange(spot.start, spot.end)", "scrollTop"):
            self.assertIn(needed, script)
        self.assertIn(".risk-item.locatable", source("editor.css"))

    def test_prompts_that_name_no_text_of_their_own_use_the_text_they_refer_to(self):
        script = source("editor.js")
        self.assertIn('id: "dose_without_frequency"', script)
        self.assertRegex(script, r'id: "dose_without_frequency".*issues\.find\(item => item\.id === "drug_dose"\)')


class Logic(unittest.TestCase):
    @unittest.skipUnless(NODE, "node is not installed")
    def test_javascript_unit_tests(self):
        result = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / "report_logic.test.js")], capture_output=True, text=True,
                                encoding="utf-8", cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()

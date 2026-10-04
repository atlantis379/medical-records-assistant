"""Runs tests/ui/hostile_page_check.py and extension_check.py, but only when BINGLI_BROWSER_TESTS=1.

They start a browser (Playwright's bundled Chromium), so they are off by default: an ordinary test run must
never open a browser on a doctor's or developer's computer. Never point them at the installed Edge or Chrome.
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
BROWSER_TESTS = os.environ.get("BINGLI_BROWSER_TESTS") == "1"
BROWSER_SKIP = "browser checks are off by default (they start a browser); set BINGLI_BROWSER_TESTS=1 to run them"


def playwright_python():
    """Playwright is not a project dependency; the offline package's Python happens to have it."""
    for candidate in sorted(ROOT.glob("dist/*/runtime/python/python.exe"), key=lambda p: p.stat().st_mtime, reverse=True):
        if subprocess.run([str(candidate), "-c", "import playwright"], capture_output=True).returncode == 0:
            return candidate
    return None


class HostilePageInARealBrowser(unittest.TestCase):
    @unittest.skipUnless(BROWSER_TESTS, BROWSER_SKIP)
    @unittest.skipUnless(VENV_PYTHON.exists(), "needs the project .venv")
    def test_another_website_cannot_use_the_service(self):
        python = playwright_python()
        if python is None:
            self.skipTest("no Python with Playwright (the offline package's runtime has one)")
        result = subprocess.run([str(python), "-X", "utf-8", str(ROOT / "tests" / "ui" / "hostile_page_check.py")],
                                capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout[-3500:] + result.stderr[-1500:])
        self.assertIn("0 failure(s)", result.stdout)


class RealExtensionInARealBrowser(unittest.TestCase):
    @unittest.skipUnless(BROWSER_TESTS, BROWSER_SKIP)
    @unittest.skipUnless(VENV_PYTHON.exists(), "needs the project .venv")
    def test_the_real_extension_is_let_through(self):
        python = playwright_python()
        if python is None:
            self.skipTest("no Python with Playwright (the offline package's runtime has one)")
        result = subprocess.run([str(python), "-X", "utf-8", str(ROOT / "tests" / "ui" / "extension_check.py")],
                                capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=300)
        if result.returncode == 3:
            self.skipTest("port 8765 is in use (a service may be running); the check will not touch it")
        self.assertEqual(result.returncode, 0, result.stdout[-3500:] + result.stderr[-1500:])
        self.assertIn("0 failure(s)", result.stdout)


if __name__ == "__main__":
    unittest.main()

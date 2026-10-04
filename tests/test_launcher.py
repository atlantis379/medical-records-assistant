"""Integration tests for the Windows launcher (packaging/windows/launcher): it is compiled and run for real.

Skipped where csc.exe or the project's Python environment is missing (for example on non-Windows machines).
Each test uses its own port and log folder, so it never touches a service the doctor may have running.
"""
import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSC = next((p for p in (Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe",
                        Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe")
            if p.exists()), None)
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
IS_WINDOWS = sys.platform == "win32"
READY_TIMEOUT = 90


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def visible_window_count(pids: set[int]) -> int:
    """Visible top-level windows owned by the given processes."""
    user32 = ctypes.windll.user32
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return len(found)


@unittest.skipUnless(IS_WINDOWS and CSC and PYTHON.exists(), "needs Windows, csc.exe and the project .venv")
class LauncherIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_dir = Path(tempfile.mkdtemp(prefix="bingli_launcher_"))
        cls.exe = cls.build_dir / "BingliAssistant.exe"
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts" / "build_windows_launcher.ps1"),
             "-Output", str(cls.exe)], capture_output=True, text=True)
        if result.returncode != 0 or not cls.exe.exists():
            raise RuntimeError("launcher did not compile:\n" + result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.build_dir, ignore_errors=True)

    def setUp(self):
        self.port = free_port()
        self.home = tempfile.mkdtemp(prefix="bingli_home_")
        self.env = {**os.environ, "BINGLI_HOME": self.home}
        self.extra = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for proc in self.extra:
            if proc.poll() is None:
                proc.kill()
        self.run_exe("--stop", timeout=30)
        shutil.rmtree(self.home, ignore_errors=True)

    def run_exe(self, *args, timeout=120):
        return subprocess.run([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-preload", "--no-browser", *args],
                              capture_output=True, text=True, env=self.env, timeout=timeout)

    def status(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/service/status", timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    def wait_until(self, condition, timeout=READY_TIMEOUT):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if condition():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def alive(self):
        try:
            self.status()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ command line
    def test_status_start_stop_cycle(self):
        self.assertEqual(self.run_exe("--status").returncode, 1)          # nothing running yet
        started = self.run_exe("--start")
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        self.assertEqual(self.run_exe("--status").returncode, 0)
        self.assertEqual(self.status()["service"], "bingli-assistant")
        self.assertEqual(self.run_exe("--start").returncode, 0)           # starting twice is harmless
        self.assertEqual(self.status()["pid"], self.status()["pid"])
        stopped = self.run_exe("--stop")
        self.assertEqual(stopped.returncode, 0, stopped.stdout)
        self.assertFalse(self.alive())
        self.assertEqual(self.run_exe("--status").returncode, 1)

    def test_service_runs_without_any_visible_window(self):
        self.assertEqual(self.run_exe("--start").returncode, 0)
        pid = self.status()["pid"]
        parent = subprocess.run(["powershell", "-NoProfile", "-Command",
                                 f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId"],
                                capture_output=True, text=True).stdout.strip()
        pids = {pid} | ({int(parent)} if parent.isdigit() else set())
        self.assertEqual(visible_window_count(pids), 0, f"visible windows for {pids}")

    def test_service_log_is_written_to_the_log_folder(self):
        self.assertEqual(self.run_exe("--start").returncode, 0)
        log = Path(self.home) / "logs" / "service.log"
        self.assertTrue(self.wait_until(lambda: log.exists() and "Uvicorn running" in log.read_text(encoding="utf-8", errors="replace"), 15))
        self.assertTrue((Path(self.home) / "logs" / "launcher.log").exists())

    def test_a_program_holding_the_port_is_never_touched(self):
        blocker = subprocess.Popen([str(PYTHON), "-c",
                                    f"import socket,time; s=socket.socket(); s.bind(('127.0.0.1',{self.port})); s.listen(); time.sleep(120)"])
        self.extra.append(blocker)
        self.assertTrue(self.wait_until(lambda: socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close() is None, 10))
        self.assertEqual(self.run_exe("--status").returncode, 2)
        self.assertEqual(self.run_exe("--start").returncode, 2)
        self.assertEqual(self.run_exe("--stop").returncode, 2)
        self.assertIsNone(blocker.poll(), "the other program must still be running")

    def test_a_service_that_does_not_stop_politely_is_ended_by_pid(self):
        # a stand-in that identifies itself as the service but ignores the shutdown request
        stand_in = subprocess.Popen([str(PYTHON), "-c", (
            "import http.server,json,os\n"
            "class H(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        body=json.dumps({'service':'bingli-assistant','version':'t','pid':os.getpid()},separators=(',',':')).encode()\n"
            "        self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)\n"
            "    def do_POST(self):\n"
            "        self.send_response(500); self.send_header('Content-Length','0'); self.end_headers()\n"
            "    def log_message(self,*a): pass\n"
            f"http.server.HTTPServer(('127.0.0.1',{self.port}),H).serve_forever()\n")])
        self.extra.append(stand_in)
        self.assertTrue(self.wait_until(self.alive, 15))
        self.assertEqual(self.run_exe("--stop", timeout=60).returncode, 0)
        self.assertTrue(self.wait_until(lambda: stand_in.poll() is not None, 10))

    # ------------------------------------------------------------------ "ready" means the models are loaded
    def stand_in_service(self, health_script):
        """A fake service that identifies itself as ours and reports model loading as `health_script` says."""
        code = "\n".join([
            "import http.server, json, os, time",
            "STARTED = time.time()",
            "def health():",
            "    " + health_script,
            "class H(http.server.BaseHTTPRequestHandler):",
            "    def do_GET(self):",
            "        if self.path == '/health': body = json.dumps(health(), separators=(',', ':')).encode()",
            "        else: body = json.dumps({'service': 'bingli-assistant', 'version': 't', 'pid': os.getpid()}, separators=(',', ':')).encode()",
            "        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)",
            "    def do_POST(self):",
            "        self.send_response(200); self.send_header('Content-Length', '0'); self.end_headers(); os._exit(0)",
            "    def log_message(self, *a): pass",
            f"http.server.HTTPServer(('127.0.0.1', {self.port}), H).serve_forever()",
        ]) + "\n"
        process = subprocess.Popen([str(PYTHON), "-c", code])
        self.extra.append(process)
        self.assertTrue(self.wait_until(self.alive, 15))
        return process

    def start_and_wait_for_models(self, *args, timeout=60):
        started = time.time()
        result = subprocess.run([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-browser", "--start", *args],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.env, timeout=timeout)
        return result, time.time() - started

    def test_start_waits_until_the_models_are_loaded(self):
        self.stand_in_service("return {'model_loaded': time.time() - STARTED > 3, 'streaming_model_loaded': time.time() - STARTED > 3, "
                              "'streaming_model_error': None, 'batch_model_error': None}")
        result, elapsed = self.start_and_wait_for_models()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertGreaterEqual(elapsed, 2.0, "it reported ready before the models were loaded")

    def test_start_reports_a_model_that_failed_to_load(self):
        self.stand_in_service("return {'model_loaded': False, 'streaming_model_loaded': False, 'streaming_model_error': None, 'batch_model_error': 'model files missing'}")
        result, elapsed = self.start_and_wait_for_models()
        self.assertEqual(result.returncode, 1)
        self.assertIn("failed", result.stdout)
        self.assertLess(elapsed, 20, "a load error must end the wait at once")

    def test_start_gives_up_when_the_models_never_finish(self):
        self.stand_in_service("return {'model_loaded': False, 'streaming_model_loaded': False, 'streaming_model_error': None, 'batch_model_error': None}")
        result, elapsed = self.start_and_wait_for_models("--model-wait", "3")
        self.assertEqual(result.returncode, 1)
        self.assertGreaterEqual(elapsed, 3)
        self.assertLess(elapsed, 30)

    def test_a_failed_streaming_model_does_not_block_a_working_batch_model(self):
        self.stand_in_service("return {'model_loaded': True, 'streaming_model_loaded': False, 'streaming_model_error': 'no streaming model', 'batch_model_error': None}")
        result, _ = self.start_and_wait_for_models()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_no_preload_does_not_wait_for_models(self):
        self.stand_in_service("return {'model_loaded': False, 'streaming_model_loaded': False, 'streaming_model_error': None, 'batch_model_error': None}")
        self.assertEqual(self.run_exe("--start").returncode, 0)

    def test_the_command_line_works_without_any_webview2_file(self):
        # the window needs WebView2 files; everything else must keep working when they are missing or cannot be loaded
        alone = Path(self.home) / "alone"
        alone.mkdir()
        shutil.copy2(self.exe, alone / "BingliAssistant.exe")
        for args in (("--status",), ("--stop",)):
            result = subprocess.run([str(alone / "BingliAssistant.exe"), "--root", str(ROOT), "--port", str(self.port), *args],
                                    capture_output=True, text=True, env=self.env, timeout=60)
            self.assertIn(result.returncode, (0, 1), (args, result.stdout, result.stderr))

    def test_missing_python_environment_is_reported(self):
        with tempfile.TemporaryDirectory() as empty:
            result = subprocess.run([str(self.exe), "--root", empty, "--port", str(self.port), "--start"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", env=self.env, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Python", result.stdout)

    # ------------------------------------------------------------------ tray program
    def test_tray_program_starts_the_service_and_survives_a_stop_and_restart(self):
        tray = subprocess.Popen([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-preload", "--no-browser"], env=self.env)
        self.extra.append(tray)
        self.assertTrue(self.wait_until(self.alive), "tray program did not bring the service up")

        # the doctor stops the service (as the extension's stop button does): the tray stays
        self.assertEqual(self.run_exe("--stop").returncode, 0)
        self.assertTrue(self.wait_until(lambda: not self.alive(), 20))
        time.sleep(3)
        self.assertIsNone(tray.poll(), "the tray program should stay until the doctor quits it")

        # double-clicking the desktop icon again asks the running tray to start the service again
        again = subprocess.run([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-preload", "--no-browser"],
                               env=self.env, timeout=30)
        self.assertEqual(again.returncode, 0)
        self.assertTrue(self.wait_until(self.alive), "second launch did not restart the service")

    def test_closing_the_tray_does_not_stop_the_service(self):
        tray = subprocess.Popen([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-preload", "--no-browser"], env=self.env)
        self.extra.append(tray)
        self.assertTrue(self.wait_until(self.alive))
        tray.kill()
        tray.wait(timeout=10)
        time.sleep(3)
        self.assertTrue(self.alive(), "the service must keep running when only the tray program ends")
        self.assertEqual(self.run_exe("--stop").returncode, 0)

    def test_only_one_tray_program_runs(self):
        first = subprocess.Popen([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-preload", "--no-browser"], env=self.env)
        self.extra.append(first)
        self.assertTrue(self.wait_until(self.alive))
        second = subprocess.run([str(self.exe), "--root", str(ROOT), "--port", str(self.port), "--no-preload", "--no-browser"], env=self.env, timeout=30)
        self.assertEqual(second.returncode, 0)
        self.assertIsNone(first.poll())


class LauncherSourceChecks(unittest.TestCase):
    source = (ROOT / "packaging" / "windows" / "launcher" / "BingliLauncher.cs").read_text(encoding="utf-8")

    def test_the_launcher_never_kills_by_port(self):
        self.assertNotIn("taskkill", self.source.lower())
        self.assertNotIn("netstat", self.source.lower())

    def test_ready_means_the_models_are_loaded(self):
        self.assertIn("WaitModelsLoaded", self.source)
        self.assertIn("正在加载语音模型", self.source)

    def test_the_own_window_is_optional_and_falls_back_to_the_browser(self):
        window = (ROOT / "packaging" / "windows" / "launcher" / "AppWindow.cs").read_text(encoding="utf-8")
        self.assertIn("WhyNot", window)
        self.assertIn("RuntimeVersion", window)
        self.assertIn("Is64BitProcess", window)
        self.assertIn("NoInlining", window)                       # a load failure must surface where it can be caught
        self.assertIn("TryOpenWindow", self.source)
        self.assertIn("FallBackToBrowser", self.source)
        self.assertIn('"--browser"', self.source)
        self.assertIn("windowUnavailable = true", self.source)

    def test_the_window_runs_in_its_own_process_that_can_be_ended_when_it_hangs(self):
        # a WebView2 problem must never freeze the tray program or the service (it once did, in-process)
        for needed in ('"--window', "WatchWindow", "window.Kill()", "WindowStartSeconds", "windowReady", "RunWindow", "BingliAssistantWindow"):
            self.assertIn(needed, self.source)
        tray = self.source.split("sealed class TrayApp")[1].split("static class Program")[0]
        self.assertNotIn("AppWindowLauncher", tray, "the tray program must not load the WebView2 types itself")
        self.assertNotIn("dictationWindow", self.source)
        self.assertIn("Kill", tray.split("void Leave()")[1])         # quitting the tray ends the window process too

    def test_main_is_single_threaded_apartment(self):
        # WebView2 and WinForms fail with RPC_E_CHANGED_MODE otherwise (the attribute once ended up on another method)
        lines = [line.strip() for line in self.source.splitlines()]
        main_at = next(i for i, line in enumerate(lines) if line.startswith("static int Main("))
        self.assertTrue(lines[main_at - 1].startswith("[STAThread]"), lines[main_at - 1])

    def test_slow_model_loading_is_waited_out_instead_of_reported_as_a_failure(self):
        self.assertIn("LastWaitTimedOut", self.source)
        self.assertIn("语音模型仍在加载（电脑较慢）", self.source)

    def test_the_window_logs_each_step_so_a_hang_can_be_located(self):
        window = (ROOT / "packaging" / "windows" / "launcher" / "AppWindow.cs").read_text(encoding="utf-8")
        for step in ("window: creating", "window: shown", "creating the WebView2 environment", "initialising the control", "page loaded"):
            self.assertIn(step, window)

    def test_only_the_window_file_uses_webview2_types(self):
        for path in (ROOT / "packaging" / "windows" / "launcher").glob("*.cs"):
            uses = "Microsoft.Web.WebView2" in path.read_text(encoding="utf-8").replace("Microsoft.Web.WebView2.Core.dll", "")
            self.assertEqual(uses, path.name == "AppWindow.cs", path.name)

    def test_the_window_allows_only_the_microphone_for_its_own_page_and_no_other_address(self):
        window = (ROOT / "packaging" / "windows" / "launcher" / "AppWindow.cs").read_text(encoding="utf-8")
        for needed in ("PermissionKind == Microsoft.Web.WebView2.Core.CoreWebView2PermissionKind.Microphone", "NavigationStarting",
                       "e.Cancel = true", "NewWindowRequested", "e.Handled = true", "AreDevToolsEnabled = false", "IsPasswordAutosaveEnabled = false"):
            self.assertIn(needed, window)

    def test_the_webview2_components_are_vendored_and_shipped(self):
        vendor = ROOT / "packaging" / "windows" / "vendor" / "webview2"
        for name in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll", "WebView2Loader.dll", "LICENSE.txt"):
            self.assertTrue((vendor / name).is_file(), name)
        verify = (ROOT / "scripts" / "verify_beta_offline_package.ps1").read_text(encoding="utf-8")
        for name in ("Microsoft.Web.WebView2.Core.dll", "WebView2Loader.dll"):
            self.assertIn(name, verify)
        self.assertIn("WebView2", (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8"))

    def test_the_service_never_reads_the_users_own_python_packages(self):
        self.assertIn('env["PYTHONNOUSERSITE"] = "1"', self.source)

    def test_local_traffic_bypasses_the_proxy(self):
        requests = self.source.count("WebRequest.Create(")
        self.assertGreaterEqual(requests, 2)
        self.assertEqual(self.source.count("request.Proxy = null"), requests)   # every HTTP request skips the proxy

    def test_the_service_is_identified_before_it_is_stopped(self):
        self.assertIn('"service\\":\\"bingli-assistant\\""', self.source.replace("'", '"'))


if __name__ == "__main__":
    unittest.main()

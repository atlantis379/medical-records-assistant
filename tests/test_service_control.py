"""Status page, status endpoint and the guarded shutdown (server/service_control.py)."""
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as srv
from server import security, service_control

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(srv.app)
os.environ.pop("BINGLI_ALLOWED_EXTENSIONS", None)
REAL_ID = sorted(security.pinned_extension_ids())[0]
EXTENSION = f"chrome-extension://{REAL_ID}"
GOOD = {"Origin": EXTENSION, "X-Bingli-Control": "1"}


class StatusEndpoints(unittest.TestCase):
    def test_status_identifies_the_service(self):
        body = client.get("/service/status").json()
        self.assertEqual(body["service"], "bingli-assistant")
        self.assertEqual(body["version"], srv.APP_VERSION)
        self.assertEqual(body["build"], srv.APP_BUILD)
        self.assertEqual(body["pid"], os.getpid())
        self.assertGreaterEqual(body["uptime_seconds"], 0)
        self.assertEqual(body["active_streams"], 0)

    def test_status_page_explains_how_to_stop(self):
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("停止服务", response.text)
        self.assertIn("/service/shutdown", response.text)
        self.assertEqual(response.headers["x-frame-options"], "DENY")   # the stop button must not be framed

    def test_status_page_does_not_load_anything_remote(self):
        self.assertNotIn("http://", service_control.STATUS_PAGE.replace("http://127.0.0.1", ""))
        self.assertNotIn("https://", service_control.STATUS_PAGE)

    def test_active_dictations_are_counted(self):
        self.assertEqual(service_control.active_streams(), 0)
        service_control.stream_opened()
        service_control.stream_opened()
        self.assertEqual(client.get("/service/status").json()["active_streams"], 2)
        service_control.stream_closed()
        service_control.stream_closed()
        service_control.stream_closed()      # never goes negative
        self.assertEqual(service_control.active_streams(), 0)

    def test_a_websocket_session_is_counted_while_it_is_open(self):
        seen = []

        async def fake_session(websocket):
            await websocket.accept()
            seen.append(service_control.active_streams())
            await websocket.close()

        with mock.patch.object(srv, "_ws_transcribe_session", fake_session):
            with client.websocket_connect("/ws/transcribe", headers={"Origin": "http://testserver"}):
                pass
        self.assertEqual(seen, [1])
        self.assertEqual(service_control.active_streams(), 0)


class ShutdownGuard(unittest.TestCase):
    def post(self, headers):
        with mock.patch.object(service_control, "terminate") as terminate, \
                mock.patch.object(service_control.threading, "Timer") as timer:
            response = client.post("/service/shutdown", headers=headers)
        return response, terminate, timer

    def test_the_extension_may_stop_the_service(self):
        response, _, timer = self.post(GOOD)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        timer.assert_called_once()                      # termination is scheduled, not immediate
        timer.return_value.start.assert_called_once()

    def test_edge_extension_origin_is_accepted_too(self):
        response, _, _ = self.post({"Origin": f"edge-extension://{REAL_ID}", "X-Bingli-Control": "1"})
        self.assertEqual(response.status_code, 200)

    def test_the_status_page_may_stop_the_service(self):
        response, _, _ = self.post({"Origin": "http://testserver", "X-Bingli-Control": "1"})
        self.assertEqual(response.status_code, 200)

    def test_other_web_pages_are_refused(self):
        for origin in ("https://evil.example", "http://evil.example", "http://127.0.0.1:9999", "null",
                       "chrome-extension://", "https://chrome-extension.evil.example"):
            response, _, timer = self.post({"Origin": origin, "X-Bingli-Control": "1"})
            self.assertEqual(response.status_code, 403, origin)
            timer.assert_not_called()

    def test_requests_without_origin_or_control_header_are_refused(self):
        for headers in ({}, {"X-Bingli-Control": "1"}, {"Origin": EXTENSION}, {"Origin": EXTENSION, "X-Bingli-Control": "0"}):
            response, _, timer = self.post(headers)
            self.assertEqual(response.status_code, 403, headers)
            timer.assert_not_called()

    def test_a_foreign_host_header_is_refused(self):
        response, _, timer = self.post({**GOOD, "Host": "evil.example"})
        self.assertEqual(response.status_code, 403)
        timer.assert_not_called()

    def test_get_cannot_stop_the_service(self):
        self.assertEqual(client.get("/service/shutdown").status_code, 405)

    def test_browser_preflight_is_only_granted_to_extensions(self):
        preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-bingli-control"}
        allowed = client.options("/service/shutdown", headers={"Origin": EXTENSION, **preflight})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), EXTENSION)
        blocked = client.options("/service/shutdown", headers={"Origin": "https://evil.example", **preflight})
        self.assertIsNone(blocked.headers.get("access-control-allow-origin"))

    def test_terminate_forces_the_exit_if_the_graceful_one_stalls(self):
        with mock.patch.object(service_control.threading, "Timer") as timer, \
                mock.patch.object(service_control.signal, "raise_signal") as raise_signal:
            service_control.terminate()
        self.assertEqual(timer.call_args.args[0], service_control.GRACE_SECONDS)
        self.assertIs(timer.call_args.args[1], os._exit)
        raise_signal.assert_called_once()


class RealProcess(unittest.TestCase):
    """The shutdown really ends a real uvicorn process, cleanly and quickly."""

    def test_shutdown_ends_the_process(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "ASR_PRELOAD_STREAMING": "0", "PYTHONUTF8": "1"}
        process = subprocess.Popen([sys.executable, "-B", "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(port)],
                                   cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.poll() is None and process.kill())

        def call(method, headers=None):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/service/shutdown", method=method, headers=headers or {},
                                             data=b"" if method == "POST" else None)
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/service/status", timeout=1).close()
                break
            except Exception:
                time.sleep(0.3)
        self.assertEqual(call("POST", {"Origin": "https://evil.example", "X-Bingli-Control": "1"}), 403)
        self.assertIsNone(process.poll(), "a refused request must not stop the service")
        self.assertEqual(call("POST", {"Origin": f"http://127.0.0.1:{port}", "X-Bingli-Control": "1"}), 200)
        self.assertEqual(process.wait(timeout=20), 0)


if __name__ == "__main__":
    unittest.main()

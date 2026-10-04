"""A hostile web page against a real service, in a real browser (Playwright's bundled Chromium).

    dist\\<package>\\runtime\\python\\python.exe -X utf8 tests\\ui\\hostile_page_check.py

Starts the service on a spare port and a second web server on another port that plays "some other website the
doctor has open". It then checks, from the service's own access log, that nothing the other website tried got
through, and that the service's own page still works. DNS rebinding is simulated with Chrome's host mapping.
"""
import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
failures = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + (f"  [{detail}]" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


EVIL_PAGE = """<!doctype html><meta charset="utf-8"><title>some other site</title>
<script>
const target = "http://127.0.0.1:%(port)d";
window.__result = { done: false, wsOpened: false, wsClosed: false, fetches: {} };
async function attempt(name, url, init) {
  try { const r = await fetch(url, init); window.__result.fetches[name] = "answered " + r.status; }
  catch (e) { window.__result.fetches[name] = "blocked by browser"; }
}
(async () => {
  // a "simple" request: no preflight, so the browser sends it straight away
  await attempt("import", target + "/hotword-packs/import", { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify({ words: [] }) });
  await attempt("feedback", target + "/feedback", { method: "POST", headers: { "Content-Type": "text/plain" }, body: JSON.stringify({ message: "planted by another site" }) });
  await attempt("transcribe", target + "/transcribe", { method: "POST", body: new FormData() });
  await attempt("put", target + "/hotwords", { method: "PUT", headers: { "Content-Type": "application/json" }, body: "{}" });
  await attempt("read_feedback", target + "/feedback/export");
  await attempt("read_health", target + "/health");
  await attempt("shutdown", target + "/service/shutdown", { method: "POST", headers: { "X-Bingli-Control": "1" } });
  await new Promise(resolve => {
    const ws = new WebSocket("ws://127.0.0.1:%(port)d/ws/transcribe");
    ws.onopen = () => { window.__result.wsOpened = true; };
    ws.onclose = () => { window.__result.wsClosed = true; resolve(); };
    ws.onerror = () => {};
    setTimeout(resolve, 4000);
  });
  window.__result.done = true;
})();
</script>"""


def main():
    service_port, evil_port = free_port(), free_port()
    log_path = Path(tempfile.mkdtemp(prefix="bingli_sec_")) / "service.log"
    log = open(log_path, "w", encoding="utf-8")
    service = subprocess.Popen([str(PYTHON), "-B", "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(service_port)],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, "ASR_PRELOAD_STREAMING": "0", "PYTHONUTF8": "1"})

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = (EVIL_PAGE % {"port": service_port}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    evil = http.server.ThreadingHTTPServer(("127.0.0.1", evil_port), Handler)
    threading.Thread(target=evil.serve_forever, daemon=True).start()
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{service_port}/service/status", timeout=1).close()
                break
            except Exception:
                time.sleep(0.4)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True,
                                        args=["--no-proxy-server", "--host-resolver-rules=MAP rebind.test 127.0.0.1,MAP otherwebsite.test 127.0.0.1"])
            page = browser.new_page()

            # ---- another website (different origin) tries everything
            page.goto(f"http://otherwebsite.test:{evil_port}/")
            page.wait_for_function("window.__result && window.__result.done", timeout=30000)
            result = page.evaluate("window.__result")
            check("the other site could not open a WebSocket", result["wsOpened"] is False, result)
            check("the other site could not read the feedback", result["fetches"]["read_feedback"] == "blocked by browser", result["fetches"])
            check("the other site could not read /health", result["fetches"]["read_health"] == "blocked by browser", result["fetches"])

            time.sleep(0.5)
            text = log_path.read_text(encoding="utf-8", errors="replace")
            check("service refused the planted POSTs", '"POST /hotword-packs/import HTTP/1.1" 403' in text and '"POST /feedback HTTP/1.1" 403' in text and
                  '"POST /transcribe HTTP/1.1" 403' in text, text[-1500:])
            check("service refused the PUT at the preflight", '"OPTIONS /hotwords HTTP/1.1" 403' in text)
            check("service refused the read", '"GET /feedback/export HTTP/1.1" 403' in text)
            check("service refused the shutdown", '"POST /service/shutdown HTTP/1.1" 403' in text or '"OPTIONS /service/shutdown HTTP/1.1" 403' in text)
            check("service refused the WebSocket", '"WebSocket /ws/transcribe" 403' in text, text[-800:])
            check("nothing from the other site was answered normally",
                  not any(f'"{m} {path}' in text and f"{m} {path} HTTP/1.1\" 200" in text for m, path in
                          [("POST", "/hotword-packs/import"), ("POST", "/feedback"), ("PUT", "/hotwords"), ("GET", "/feedback/export")]))
            check("the service is still running after the attack", service.poll() is None)

            # ---- DNS rebinding: the hostile name now resolves to this computer, so the browser treats it as same-origin
            rebound = page.goto(f"http://rebind.test:{service_port}/health")
            check("a rebound host name is refused (Host header)", rebound.status == 403, rebound.status)
            check("the same request by IP address works", page.goto(f"http://127.0.0.1:{service_port}/health").status == 200)

            # ---- the service's own page is not over-blocked
            page.goto(f"http://127.0.0.1:{service_port}/")
            check("the status page loads", "停止服务" in page.inner_text("body"))
            own = page.evaluate("""async () => {
                const post = await fetch("/hotword-packs/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ words: [] }) });
                const read = await fetch("/specialties");
                return { post: post.status, read: read.status };
            }""")
            check("its own POST reaches the endpoint (400 = empty import, not 403)", own["post"] == 400, own)
            check("its own reads work", own["read"] == 200, own)
            browser.close()
    finally:
        evil.shutdown()
        if service.poll() is None:
            service.kill()
        log.close()
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

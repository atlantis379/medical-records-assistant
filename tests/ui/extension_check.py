"""The real extension, loaded into Playwright's bundled Chromium, talking to a real service: the origin checks must let it through.

    dist\\<package>\\runtime\\python\\python.exe -X utf8 tests\\ui\\extension_check.py

The extension has the service address built in (127.0.0.1:8765), so this check needs that port to be free and
refuses to run when a service the doctor may be using is there. It only makes requests that change nothing.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
EXTENSION = ROOT / "extension"
PORT = 8765
failures = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + (f"  [{detail}]" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def port_busy():
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def start_service(log, extra_env=None):
    process = subprocess.Popen([str(PYTHON), "-B", "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(PORT)],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, "ASR_PRELOAD_STREAMING": "0", "PYTHONUTF8": "1", **(extra_env or {})})
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/service/status", timeout=1).close()
            break
        except Exception:
            time.sleep(0.4)
    return process


def main():
    if port_busy():
        print(f"SKIP: port {PORT} is in use (a service may be running); this check will not touch it")
        return 3
    log = open(Path(tempfile.mkdtemp(prefix="bingli_ext_")) / "service.log", "w", encoding="utf-8")
    service = subprocess.Popen([str(PYTHON), "-B", "-m", "uvicorn", "server.app:app", "--host", "127.0.0.1", "--port", str(PORT)],
                               cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                               env={**os.environ, "ASR_PRELOAD_STREAMING": "0", "PYTHONUTF8": "1"})
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/service/status", timeout=1).close()
                break
            except Exception:
                time.sleep(0.4)
        profile = tempfile.mkdtemp(prefix="bingli_profile_")
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                profile, headless=False,
                args=["--headless=new", f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", "--no-proxy-server"])
            worker = context.service_workers[0] if context.service_workers else context.wait_for_event("serviceworker", timeout=20000)
            extension_id = worker.url.split("/")[2]
            check("the extension loaded", len(extension_id) == 32, worker.url)

            page = context.new_page()
            page.goto(f"chrome-extension://{extension_id}/editor.html")
            page.wait_for_timeout(2500)
            check("the page reports the service as connected", "已连接" in page.inner_text("#serviceStatus"), page.inner_text("#serviceStatus"))
            check("the stop button is offered", page.is_visible("#stopServiceButton"))
            page.wait_for_timeout(1500)
            check("the specialty list loads", page.locator("#specialtySelect option").count() >= 4)
            check("the hotword packs load", page.locator("#hotwordPackList .hotword-pack").count() >= 8)

            results = page.evaluate("""async () => {
                const base = "http://127.0.0.1:8765";
                const out = {};
                out.get = (await fetch(base + "/health")).status;
                // JSON content type forces a preflight; an empty import changes nothing (400 = reached the endpoint)
                out.postJson = (await fetch(base + "/hotword-packs/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ words: [] }) })).status;
                out.ws = await new Promise(resolve => {
                    const ws = new WebSocket("ws://127.0.0.1:8765/ws/transcribe");
                    ws.onopen = () => { ws.close(); resolve("opened"); };
                    ws.onerror = () => resolve("error");
                    setTimeout(() => resolve("timeout"), 5000);
                });
                return out;
            }""")
            check("GET from the extension works", results["get"] == 200, results)
            check("POST with a preflight from the extension reaches the endpoint", results["postJson"] == 400, results)
            check("WebSocket from the extension is accepted", results["ws"] == "opened", results)

            # an ordinary web page in the same browser is refused
            other = context.new_page()
            other.goto("about:blank")
            other.route("http://otherwebsite.test/", lambda route: route.fulfill(status=200, content_type="text/html", body="<html></html>"))
            other.goto("http://otherwebsite.test/")
            foreign = other.evaluate("""async () => {
                const out = {};
                try { await fetch("http://127.0.0.1:8765/hotword-packs/import", { method: "POST", headers: { "Content-Type": "text/plain" }, body: "{}" }); out.post = "answered"; }
                catch (e) { out.post = "blocked"; }
                out.ws = await new Promise(resolve => {
                    const ws = new WebSocket("ws://127.0.0.1:8765/ws/transcribe");
                    ws.onopen = () => resolve("opened");
                    ws.onerror = () => resolve("refused");
                    setTimeout(() => resolve("timeout"), 5000);
                });
                return out;
            }""")
            check("another website cannot open the WebSocket in the same browser", foreign["ws"] == "refused", foreign)

            # pinning the extension: a different ID locks this extension out, the right ID lets it back in
            service.kill()
            service.wait()
            service = start_service(log, {"BINGLI_ALLOWED_EXTENSIONS": "a" * 32})
            page.reload()
            page.wait_for_timeout(2500)
            pinned_out = page.evaluate("""async () => {
                const post = (await fetch("http://127.0.0.1:8765/hotword-packs/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).status;
                const ws = await new Promise(resolve => {
                    const socket = new WebSocket("ws://127.0.0.1:8765/ws/transcribe");
                    socket.onopen = () => { socket.close(); resolve("opened"); };
                    socket.onerror = () => resolve("refused");
                    setTimeout(() => resolve("timeout"), 5000);
                });
                return { post, ws };
            }""")
            check("with another extension pinned, this extension may not change data", pinned_out["post"] == 403, pinned_out)
            check("with another extension pinned, this extension has no WebSocket", pinned_out["ws"] == "refused", pinned_out)
            service.kill()
            service.wait()
            service = start_service(log, {"BINGLI_ALLOWED_EXTENSIONS": extension_id})
            page.reload()
            page.wait_for_timeout(2500)
            again = page.evaluate("""async () => (await fetch("http://127.0.0.1:8765/hotword-packs/import", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).status""")
            check("with its own ID pinned, the extension works again", again == 400 and "已连接" in page.inner_text("#serviceStatus"), again)
            context.close()
        log.flush()
    finally:
        if service.poll() is None:
            service.kill()
        log.close()
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

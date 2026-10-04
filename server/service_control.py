"""Start/stop support for non-technical users: a status page, a status endpoint and a guarded shutdown.

The shutdown endpoint can stop the service, so a web page must never be able to call it. It requires
  * a loopback client and a loopback Host header (blocks DNS rebinding),
  * an Origin that is the browser extension or this service's own status page (browsers always send it
    on a POST, and a page on another site cannot forge it),
  * the custom header X-Bingli-Control: 1 (forces a CORS preflight, which only the extension passes).
The launcher (a native program) sends the same headers.
"""
import os
import signal
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import security

SERVICE_ID = "bingli-assistant"
STARTED_AT = time.time()
GRACE_SECONDS = 6.0

router = APIRouter()
_info = {"version": "", "build": ""}
_lock = threading.Lock()
_active_streams = 0

def configure(version: str, build: str) -> None:
    _info["version"], _info["build"] = version, build


def stream_opened() -> None:
    global _active_streams
    with _lock:
        _active_streams += 1


def stream_closed() -> None:
    global _active_streams
    with _lock:
        _active_streams = max(0, _active_streams - 1)


def active_streams() -> int:
    with _lock:
        return _active_streams


def terminate() -> None:
    """Stop the process: ask uvicorn to exit cleanly, and force it if that does not finish in time."""
    timer = threading.Timer(GRACE_SECONDS, os._exit, args=(0,))
    timer.daemon = True
    timer.start()
    try:
        signal.raise_signal(signal.SIGINT)
    except Exception:
        os._exit(0)


def control_request_allowed(request: Request) -> tuple[bool, str]:
    """The shutdown is stricter than ordinary calls: same origin rules, an Origin is mandatory, and a custom header."""
    host_header = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if origin is None:
        return False, "origin required"
    allowed, reason = security.check(kind="http", method="POST", client_host=request.client.host if request.client else "",
                                     host_header=host_header, origin=origin)
    if not allowed:
        return False, reason
    if request.headers.get("x-bingli-control") != "1":
        return False, "missing control header"
    return True, ""


@router.get("/service/status")
def service_status():
    return {
        "service": SERVICE_ID,
        "version": _info["version"],
        "build": _info["build"],
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
        "active_streams": active_streams(),
    }


@router.post("/service/shutdown")
def service_shutdown(request: Request):
    allowed, reason = control_request_allowed(request)
    if not allowed:
        raise HTTPException(status_code=403, detail=f"shutdown refused: {reason}")
    threading.Timer(0.4, terminate).start()   # let this response reach the caller first
    return {"ok": True, "message": "服务正在停止", "pid": os.getpid()}


STATUS_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>病历助手本地服务</title>
<style>
  :root { --ink:#17352f; --muted:#667a75; --green:#126a56; --paper:#fffdf8; --line:#d9e3df; --danger:#a33a37; }
  body { margin:0; font:16px/1.7 "Microsoft YaHei",system-ui,sans-serif; color:var(--ink); background:#f3f7f5; }
  main { max-width:640px; margin:48px auto; padding:0 20px; }
  .card { background:var(--paper); border:1px solid var(--line); border-radius:16px; padding:24px 28px; }
  h1 { margin:0 0 4px; font-size:24px; } .state { display:flex; align-items:center; gap:10px; font-weight:600; }
  .dot { width:12px; height:12px; border-radius:50%; background:var(--green); } .off .dot { background:var(--danger); }
  ol { padding-left:22px; } small { color:var(--muted); }
  button { font:inherit; padding:8px 20px; border-radius:10px; border:1px solid var(--danger); color:var(--danger); background:#fff; cursor:pointer; }
  button:disabled { opacity:.5; cursor:default; }
</style></head>
<body><main><div class="card" id="card">
  <h1>病历助手</h1>
  <p class="state" id="state"><span class="dot"></span><span id="stateText">本地语音服务正在运行</span></p>
  <p><small id="meta"></small></p>
  <h2 style="font-size:18px">怎么用</h2>
  <ol>
    <li><a href="/app/">点这里打开听写页面</a>（也可以双击桌面“病历助手”图标，或右键托盘图标选“打开听写页面”）；</li>
    <li>听写、核对，再复制到医院病历系统；</li>
    <li><b>用完后点击下面的“停止服务”</b>，释放电脑资源。</li>
  </ol>
  <p><button id="stop" type="button">停止服务</button> <small id="note"></small></p>
  <p><small>语音和病历内容只在本机处理，不会上传。关闭本页面不会停止服务。</small></p>
</div></main>
<script>
const $ = id => document.getElementById(id);
async function refresh() {
  try {
    const s = await (await fetch("/service/status", { cache: "no-store" })).json();
    const minutes = Math.floor(s.uptime_seconds / 60);
    $("meta").textContent = "版本 " + s.version + " · 已运行 " + (minutes < 60 ? minutes + " 分钟" : Math.floor(minutes / 60) + " 小时 " + (minutes % 60) + " 分钟");
  } catch (e) { stopped(); }
}
function stopped() {
  $("card").classList.add("off"); $("stateText").textContent = "服务已停止";
  $("stop").disabled = true; $("note").textContent = "需要时，双击桌面“病历助手”图标重新启动。";
}
$("stop").addEventListener("click", async () => {
  let busy = "";
  try { const s = await (await fetch("/service/status", { cache: "no-store" })).json(); if (s.active_streams > 0) busy = "当前有正在进行的听写，停止会中断它。\\n\\n"; } catch (e) {}
  if (!confirm(busy + "确定要停止病历助手服务吗？")) return;
  $("stop").disabled = true;
  try {
    const r = await fetch("/service/shutdown", { method: "POST", headers: { "X-Bingli-Control": "1" } });
    if (!r.ok) throw new Error(r.status);
    setTimeout(stopped, 1200);
  } catch (e) { $("stop").disabled = false; $("note").textContent = "停止失败，请使用托盘图标中的“停止服务并退出”。"; }
});
refresh(); setInterval(refresh, 5000);
</script></body></html>
"""


@router.get("/", response_class=HTMLResponse)
def status_page():
    return HTMLResponse(STATUS_PAGE, headers={"X-Frame-Options": "DENY", "Cache-Control": "no-store"})

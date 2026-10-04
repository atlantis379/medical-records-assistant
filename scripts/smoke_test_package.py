"""End-to-end check of an offline package folder or an installed copy.

    .venv\\Scripts\\python.exe scripts\\smoke_test_package.py <package folder> [--audio file.wav] [--port 8791]

It starts the package's own launcher (BingliAssistant.exe), asks the service to recognise a short recording,
looks at what the service actually imported, and stops it again. It checks that:
  * the package is self-contained: the interpreter and every key library are loaded from inside the folder,
    not from this computer's Python (a missing library would otherwise be hidden);
  * the service starts, answers, and recognises speech with the bundled models, offline;
  * numbers and units come out normalised (the whole pipeline: model, hotwords, normalisation);
  * the stop works and leaves nothing running.

The recording defaults to a synthetic smoke clip. The speech is only a test signal.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures = []


def check(name, condition, detail=""):
    print(("ok   " if condition else "FAIL ") + name + (f"  [{detail}]" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(method, url, *, headers=None, body=None, timeout=120):
    request = urllib.request.Request(url, method=method, headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read()
        except (ConnectionError, OSError):
            return error.code, b""
    except (ConnectionError, OSError) as error:
        # a service that refuses a large POST at once may close the connection before the upload ends
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)) or "10054" in str(error):
            return 0, b""
        raise


def multipart(fields: dict, file_field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode("utf-8") + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def report_test_picture() -> bytes | None:
    """A picture with a line of Chinese text, drawn here (never a real report). None when this computer cannot draw it."""
    try:
        import io
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    font_file = next((f for f in (r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf") if Path(f).exists()), None)
    if font_file is None:
        return None
    image = Image.new("RGB", (900, 130), "white")
    ImageDraw.Draw(image).text((30, 40), "检查所见：双侧胸腔积液，盆腔少量积液。", fill="black", font=ImageFont.truetype(font_file, 34))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def package_environment(root: Path) -> dict:
    """The environment the launcher gives the service in an offline package."""
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "1", "PYTHONNOUSERSITE": "1", "ASR_DEVICE": "cpu", "MODELSCOPE_OFFLINE": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "MODELSCOPE_CACHE": str(root / "models" / "modelscope" / "hub"),
                "PYTHONPATH": str(root / ".venv" / "Lib" / "site-packages") + ";" + str(root)})
    return env


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("package")
    parser.add_argument("--audio", default=str(ROOT / "evaluation" / "datasets" / "smoke" / "audio" / "r03.wav"))
    parser.add_argument("--expect", default="120/80mmHg", help="text the recognised, normalised result must contain")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)

    root = Path(args.package).resolve()
    exe = root / "BingliAssistant.exe"
    python = root / "runtime" / "python" / "python.exe"
    audio = Path(args.audio)
    port = args.port or free_port()
    base = f"http://127.0.0.1:{port}"
    home = Path(os.environ.get("TEMP", ".")) / f"bingli_smoke_{port}"
    env = {**os.environ, "BINGLI_HOME": str(home)}

    check("launcher present", exe.exists(), str(exe))
    check("bundled Python present", python.exists(), str(python))
    check("recognition models present", (root / "models" / "modelscope" / "hub" / "models" / "iic").exists())
    check("recording present", audio.exists(), str(audio))
    check("no stray second model copy", not (root / "models" / "modelscope" / "models").exists())
    if failures:
        return 1

    # --- 1. what the service would import: everything must come from inside the package
    probe = subprocess.run(
        [str(python), "-B", "-c",
         "import sys, json, importlib\n"
         "mods = ['fastapi', 'uvicorn', 'numpy', 'torch', 'funasr', 'modelscope', 'multipart', 'server.app']\n"
         "print(json.dumps({m: (getattr(importlib.import_module(m), '__file__', '') or '') for m in mods}))\n"
         "from funasr.register import tables\n"
         "print(json.dumps(sorted(tables.model_classes)))\n"
         "print(json.dumps(sys.path))"],
        cwd=root, env=package_environment(root), capture_output=True, text=True, encoding="utf-8", timeout=300)
    check("the packaged Python can import the service's libraries", probe.returncode == 0, probe.stderr[-600:])
    if probe.returncode == 0:
        lines = probe.stdout.strip().splitlines()
        files, registered, paths = json.loads(lines[-3]), json.loads(lines[-2]), json.loads(lines[-1])
        # FunASR skips a model whose imports fail and only complains when it is first used
        for needed in ("SeacoParaformer", "ParaformerStreaming"):
            check(f"FunASR registered the model class {needed}", needed in registered, registered[:12])
        outside = {m: f for m, f in files.items() if f and not Path(f).resolve().is_relative_to(root)}
        check("every key library is loaded from inside the package", not outside, outside)
        leaks = [p for p in paths if p and Path(p).exists() and not Path(p).resolve().is_relative_to(root)]
        check("nothing outside the package is on the import path", not leaks, leaks)

    # --- 2. start, recognise, stop
    def launcher(*extra, timeout=300):
        return subprocess.run([str(exe), "--root", str(root), "--port", str(port), "--no-browser", *extra],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)

    started = time.time()
    result = launcher("--start")
    check("the launcher starts the service", result.returncode == 0, result.stdout + result.stderr)
    try:
        if result.returncode == 0:
            print(f"     ready after {time.time() - started:.1f}s")
            status, body = http("GET", f"{base}/service/status")
            check("status answers", status == 200 and b"bingli-assistant" in body, body[:200])
            status, body = http("GET", f"{base}/health")
            health = json.loads(body) if status == 200 else {}
            check("health is ok and runs on the CPU", health.get("status") == "ok" and health.get("device") == "cpu", health)

            # the service is closed to everyone until a doctor has logged in (the account lives in the temporary BINGLI_HOME)
            status, _ = http("GET", f"{base}/hotwords")
            check("a protected route is closed without a login", status == 401, f"status {status}")
            account = json.dumps({"username": "smoketest", "password": "smoke-test-pass-1"}).encode()
            status, body = http("POST", f"{base}/auth/setup", headers={"Content-Type": "application/json", "Origin": base}, body=account)
            token = json.loads(body).get("token") if status == 200 else None
            check("the first account can be set up and logs in", bool(token), f"status {status}")
            login = {"Authorization": f"Bearer {token}"}
            status, _ = http("GET", f"{base}/hotwords", headers={"Authorization": "Bearer wrong"})
            check("a wrong token is refused", status == 401, f"status {status}")
            status, _ = http("GET", f"{base}/hotwords", headers=login)
            check("the token opens protected routes", status == 200, f"status {status}")

            form, content_type = multipart({"language": "zh-CN", "profile": "fast", "specialties": "respiratory_critical_care",
                                            "include_draft": "false"}, "file", "smoke.wav", audio.read_bytes())
            began = time.time()
            status, body = http("POST", f"{base}/transcribe", headers={"Content-Type": content_type, "Origin": base, **login}, body=form, timeout=600)
            elapsed = time.time() - began
            reply = json.loads(body) if status == 200 else {}
            check("recognition works with the bundled models", status == 200 and bool(reply.get("text")), body[:300])
            print(f"     text: {reply.get('text')!r}  ({elapsed:.1f}s, RTF {reply.get('metrics', {}).get('realtime_factor')})")
            check(f"the result is normalised ({args.expect})", args.expect in (reply.get("text") or ""), reply.get("text"))
            began = time.time()
            status, body = http("POST", f"{base}/transcribe", headers={"Content-Type": content_type, "Origin": base, **login}, body=form, timeout=600)
            second = time.time() - began
            again = json.loads(body) if status == 200 else {}
            print(f"     first request {elapsed:.1f}s, second {second:.1f}s, RTF {again.get('metrics', {}).get('realtime_factor')}")
            # --start returns only after the models are loaded, so the first dictation must not stall on loading them
            check("the first recognition after --start does not wait for model loading", elapsed < 15 and status == 200, f"{elapsed:.1f}s then {second:.1f}s")

            # outside reports: the picture recognition components are part of the package and read a picture on this computer
            status, body = http("GET", f"{base}/report-import/status", headers=login)
            ocr = json.loads(body) if status == 200 else {}
            check("the picture recognition components are in the package", ocr.get("available") is True, ocr)
            if ocr.get("available"):
                png = report_test_picture()
                if png:
                    form_r, type_r = multipart({}, "files", "report.png", png)
                    status, body = http("POST", f"{base}/report-import/upload", headers={"Content-Type": type_r, "Origin": base, **login}, body=form_r)
                    up = json.loads(body) if status == 200 else {}
                    check("a picture can be handed in", bool(up.get("added")), body[:200])
                    if up.get("added"):
                        began = time.time()
                        payload = json.dumps({"session": up["session"], "image_id": up["added"][0]["id"]}).encode()
                        status, body = http("POST", f"{base}/report-import/recognize", headers={"Content-Type": "application/json", "Origin": base, **login}, body=payload, timeout=300)
                        reply_r = json.loads(body) if status == 200 else {}
                        print(f"     picture recognised in {time.time() - began:.1f}s: {reply_r.get('text')!r}")
                        check("the picture's text is read (offline, in the package)", "胸腔积液" in (reply_r.get("text") or ""), body[:300])
                        http("POST", f"{base}/report-import/clear", headers={"Content-Type": "application/json", "Origin": base, **login}, body=json.dumps({"session": up["session"]}).encode())
                else:
                    print("     (no Pillow or Chinese font on this computer: the recognition itself was not tried)")

            status, _ = http("POST", f"{base}/transcribe", headers={"Content-Type": content_type, "Origin": "https://evil.example", **login}, body=form)
            check("a foreign web page is refused by the packaged service", status in (403, 0), f"status {status} (0 = connection closed)")
    finally:
        stopped = launcher("--stop", timeout=90)
        check("the launcher stops the service", stopped.returncode == 0, stopped.stdout)

    time.sleep(1)
    try:
        http("GET", f"{base}/service/status", timeout=2)
        gone = False
    except Exception:
        gone = True
    check("nothing is left listening", gone)
    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

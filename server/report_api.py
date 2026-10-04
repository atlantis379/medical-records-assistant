"""Web API behind the "import outside report" dialog: pictures of lab sheets and imaging reports -> text for the doctor to check.

The pictures are kept in memory only (never on disk, never logged) for one session, which ends when the dialog closes, when
the doctor starts another one, or after SESSION_TTL seconds without activity. Recognition runs here, on the CPU, offline.
The recognised text is a draft: the page shows it next to the picture and nothing is written into the record by itself.
"""
import asyncio
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from .report_ocr import engine, lab_table, layout, similar

router = APIRouter(prefix="/report-import")

SESSION_TTL = 30 * 60
MAX_FILES = 60
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_SESSION_BYTES = 300 * 1024 * 1024
ALLOWED = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NOT_YET = {".pdf": "PDF 暂不支持，请先把每一页转成图片（JPG 或 PNG）", ".heic": "iPhone 的 HEIC 暂不支持，请先转成 JPG", ".heif": "iPhone 的 HEIC 暂不支持，请先转成 JPG"}

_terms: Callable[[], set] = lambda: set()
_lock = threading.Lock()
_session: dict | None = None
_recognize_lock = asyncio.Lock()


def configure(known_terms: Callable[[], set]) -> None:
    """known_terms(): the words (drug names, packs, imaging terms) the look-alike check compares with."""
    global _terms
    _terms = known_terms


def _get(session_id: str | None) -> dict | None:
    global _session
    with _lock:
        if _session is None or _session["id"] != session_id:
            return None
        if time.time() - _session["touched"] > SESSION_TTL:
            _session = None
            return None
        _session["touched"] = time.time()
        return _session


def clear_all() -> None:
    global _session
    with _lock:
        _session = None


def active() -> bool:
    with _lock:
        return _session is not None and time.time() - _session["touched"] <= SESSION_TTL


@router.get("/status")
def status():
    reason = engine.unavailable_reason()
    return {"available": reason is None, "reason": reason}


@router.post("/upload")
async def upload(request: Request):
    """One batch of pictures (multipart: `files`, and `session` after the first batch)."""
    global _session
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if length > MAX_REQUEST_BYTES + 1024 * 1024:
        raise HTTPException(status_code=413, detail="一次上传的图片太大，请减少每批的张数")
    form = await request.form(max_files=MAX_FILES + 1)
    files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
    session_id = form.get("session")
    if not files:
        raise HTTPException(status_code=400, detail="没有收到图片")
    with _lock:
        if session_id:
            current = _session if _session and _session["id"] == session_id and time.time() - _session["touched"] <= SESSION_TTL else None
            if current is None:
                raise HTTPException(status_code=404, detail="图片已从内存清除（超时或已关闭），请重新选择")
        else:
            current = _session = {"id": secrets.token_urlsafe(12), "images": {}, "touched": time.time(), "bytes": 0}
        current["touched"] = time.time()
    added, refused, total = [], [], 0
    for item in files:
        name = Path(item.filename or "").name
        suffix = Path(name).suffix.lower()
        if suffix in NOT_YET:
            refused.append({"name": name, "reason": NOT_YET[suffix]})
            continue
        if suffix not in ALLOWED:
            refused.append({"name": name, "reason": "不是图片文件（支持 JPG、PNG、BMP、WEBP）"})
            continue
        raw = await item.read()
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES:
            refused.append({"name": name, "reason": "图片超过 25 MB"})
            continue
        if total > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="一次上传的图片太大，请减少每批的张数")
        if len(current["images"]) >= MAX_FILES or current["bytes"] + len(raw) > MAX_SESSION_BYTES:
            raise HTTPException(status_code=413, detail="图片太多或总量过大，请分批处理")
        try:
            width, height = await asyncio.to_thread(engine.image_size, raw) if engine.unavailable_reason() is None else (0, 0)
        except engine.BadImage as exc:
            refused.append({"name": name, "reason": str(exc)})
            continue
        image_id = secrets.token_hex(6)
        current["images"][image_id] = {"name": name, "bytes": raw, "size": len(raw), "result": {}}
        current["bytes"] += len(raw)
        added.append({"id": image_id, "name": name, "size": len(raw), "width": width, "height": height})
    return {"session": current["id"], "added": added, "refused": refused}


class RecognizeRequest(BaseModel):
    session: str
    image_id: str
    rotate: int = Field(0, ge=0, le=359)


@router.post("/recognize")
async def recognize(request: RecognizeRequest):
    current = _get(request.session)
    if current is None:
        raise HTTPException(status_code=404, detail="图片已从内存清除（超时或已关闭），请重新选择")
    image = current["images"].get(request.image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="没有这张图片")
    if engine.unavailable_reason():
        raise HTTPException(status_code=503, detail=engine.unavailable_reason())
    if _recognize_lock.locked():
        raise HTTPException(status_code=409, detail="正在识别另一张图片，请稍候")
    async with _recognize_lock:
        try:
            raw = await asyncio.to_thread(engine.recognize, image["bytes"], request.rotate)
        except engine.BadImage as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except engine.OcrUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"识别失败：{exc}") from exc
    lines, removed = layout.drop_screen_furniture(raw["lines"], raw["height"])
    table = lab_table.parse(lines)
    text = "\n".join(lab_table.line_for(row) for row in table["rows"]) if table else "\n".join(layout.paragraphs(lines))
    result = {
        "image_id": request.image_id, "name": image["name"], "seconds": round(raw["seconds"], 1),
        "size": [raw["width"], raw["height"]], "line_count": len(lines), "removed": removed,
        "low_confidence": sum(1 for line in lines if line["score"] < 0.85),
        "text": text,
        "suggestions": [] if table else (similar.suggest(text, similar.Index(_terms())) if text else []),
        "check_items": [f"{row['name']}：{row['note']}" for row in table["rows"] if row["note"]] if table else layout.check_items(text),
        "table": table,
    }
    image["result"] = {"text": text}
    return result


class ClearRequest(BaseModel):
    session: str | None = None


@router.post("/clear")
def clear(request: ClearRequest):
    global _session
    with _lock:
        cleared = _session is not None and (request.session is None or _session["id"] == request.session)
        if cleared:
            _session = None
    return {"cleared": cleared}

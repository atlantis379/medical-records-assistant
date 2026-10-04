"""Web API behind the "extract hotwords from our own cases" dialog of the editor page.

Cases never leave this computer: the page uploads them to this local service, they are kept in memory only
(see server/hotword_miner/pipeline.py) and only words and counts are returned. The pack written at the end
contains words only. Every POST needs an allowed Origin (server/security.py), like all other changing requests.
"""
import asyncio
import re
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser

from .hotword_miner import mining, output, pipeline

router = APIRouter(prefix="/hotword-miner")

PACK_PREFIX = "hospital_"
PACK_SLUG = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_REQUEST_FILES = 200
MAX_WORDS = 1000

# Starlette keeps an uploaded file in memory only up to 1 MB and then writes it to a temporary file on disk. Case
# files must not reach the disk, so the limit is raised to the largest request this API accepts (a few other
# uploads, such as a recording for /transcribe, are small enough to stay in memory too).
MultiPartParser.spool_max_size = MAX_REQUEST_BYTES + 1024 * 1024

_config: dict[str, Callable] = {}
_scan_lock = asyncio.Lock()


def configure(pack_dir: Callable[[], Path], specialty_ids: Callable[[], list[str]], known_words: Callable[[], set[str]]) -> None:
    """Callables, so tests that point the service at another folder are respected."""
    _config.update(pack_dir=pack_dir, specialty_ids=specialty_ids, known_words=known_words)


def _session_or_404(session_id: str | None) -> pipeline.Session:
    session = pipeline.store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="病例已从内存清除（超时或已关闭），请重新选择病例")
    return session


@router.post("/upload")
async def upload(request: Request):
    """One batch of case files (multipart: `files`, and `session` after the first batch).

    Without a session id a new session starts (and wipes any earlier one).
    """
    try:
        length = int(request.headers.get("content-length") or 0)
    except ValueError:
        length = 0
    if length > MAX_REQUEST_BYTES + 1024 * 1024:
        raise HTTPException(status_code=413, detail="一次上传的数据过大，请减小每批的文件数")
    form = await request.form(max_files=MAX_REQUEST_FILES + 1)
    files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
    session = form.get("session")
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > MAX_REQUEST_FILES:
        raise HTTPException(status_code=413, detail=f"一次最多 {MAX_REQUEST_FILES} 个文件")
    current = pipeline.store.get(session) if session else None
    if session and current is None:
        raise HTTPException(status_code=404, detail="病例已从内存清除（超时或已关闭），请重新选择病例")
    items, total = [], 0
    for item in files:
        raw = await item.read()
        total += len(raw)
        if total > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="一次上传的数据过大，请减小每批的文件数")
        items.append((Path(item.filename or "").name, raw))
    current = current or pipeline.store.create()
    try:
        report = pipeline.add_files(current, items)
    except pipeline.LimitError as exc:
        pipeline.store.clear(current.id)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    return {"session": current.id, **report}


class ScanRequest(BaseModel):
    session: str
    min_cases: int = Field(5, ge=1, le=1000)
    case_start: str | None = Field(None, max_length=30)
    min_pmi: float = Field(3.0, ge=0, le=10)
    min_entropy: float = Field(0.5, ge=0, le=3)
    common_freq: int = Field(1000, ge=0, le=1_000_000)
    top: int = Field(2000, ge=50, le=3000)


@router.post("/scan")
async def scan(request: ScanRequest):
    session = _session_or_404(request.session)
    settings = mining.Settings(min_cases=request.min_cases, min_pmi=request.min_pmi, min_entropy=request.min_entropy,
                               common_freq=request.common_freq, top=request.top)
    if _scan_lock.locked():
        raise HTTPException(status_code=409, detail="正在分析中，请稍候")
    async with _scan_lock:
        try:
            return await asyncio.to_thread(pipeline.scan, session, settings, _config["known_words"](), request.case_start)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


class SessionRequest(BaseModel):
    session: str | None = None


@router.post("/clear")
def clear(request: SessionRequest):
    """Forget the uploaded cases now (the page calls this when its dialog closes)."""
    return {"cleared": pipeline.store.clear(request.session)}


class BuildRequest(BaseModel):
    session: str
    words: list[str] = Field(..., max_length=MAX_WORDS)
    specialty: str
    label: str = Field(..., min_length=1, max_length=40)
    reviewers: list[str] = Field(default_factory=list, max_length=5)
    pack_id: str | None = Field(None, max_length=60)


def _pack_id(request: BuildRequest) -> str:
    slug = (request.pack_id or request.specialty).strip().lower()
    if slug.startswith(PACK_PREFIX):
        slug = slug[len(PACK_PREFIX):]
    if not PACK_SLUG.match(slug):
        raise HTTPException(status_code=400, detail="词库编号只能用小写字母、数字和下划线")
    return PACK_PREFIX + slug


@router.post("/build")
def build(request: BuildRequest):
    """Write the ticked words as a specialty pack. A pack with no named reviewer is a draft (off by default)."""
    session = _session_or_404(request.session)
    if session.scan is None:
        raise HTTPException(status_code=400, detail="还没有扫描结果")
    if request.specialty not in _config["specialty_ids"]():
        raise HTTPException(status_code=400, detail="未知专业")
    offered = {c["word"].lower() for c in session.scan["candidates"]}
    words, seen = [], set()
    for word in (w.strip() for w in request.words):
        if not word or word.lower() in seen:
            continue
        if word.lower() not in offered:
            raise HTTPException(status_code=400, detail=f"“{word[:20]}”不在扫描结果中")
        problem = output.problem_with(word)
        if problem:
            raise HTTPException(status_code=400, detail=f"“{word[:20]}”：{problem}")
        seen.add(word.lower())
        words.append(word)
    if not words:
        raise HTTPException(status_code=400, detail="没有勾选任何词")
    label = " ".join(request.label.split())
    reviewers = [" ".join(r.split()) for r in request.reviewers if r.strip()]
    if not label or any(len(r) > 20 for r in reviewers):
        raise HTTPException(status_code=400, detail="名称或审核人不合要求")
    pack_id = _pack_id(request)
    settings = session.scan["settings"]
    txt, manifest = output.write_pack(_config["pack_dir"](), pack_id, request.specialty, label, words,
                                      session.scan["cases"], settings["min_cases"], reviewers)
    return {"id": pack_id, "count": len(words), "status": "reviewed" if reviewers else "draft", "files": [txt.name, manifest.name],
            "advice": output.PACK_ADVICE, "over_advice": len(words) > output.PACK_ADVICE}


class DeletePackRequest(BaseModel):
    pack_id: str = Field(..., max_length=80)


@router.post("/packs/delete")
def delete_pack(request: DeletePackRequest):
    """Remove a pack made by this tool. Built-in packs cannot be removed here."""
    pack_id = request.pack_id.strip()
    if not pack_id.startswith(PACK_PREFIX) or not PACK_SLUG.match(pack_id[len(PACK_PREFIX):]):
        raise HTTPException(status_code=400, detail="只能删除本工具生成的词库（编号以 hospital_ 开头）")
    folder = _config["pack_dir"]()
    removed = 0
    for suffix in (".txt", ".manifest.json"):
        path = folder / f"{pack_id}{suffix}"
        if path.is_file():
            path.unlink()
            removed += 1
    if not removed:
        raise HTTPException(status_code=404, detail="没有找到这个词库")
    return {"removed": pack_id}

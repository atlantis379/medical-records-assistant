import asyncio
import os
import re
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

APP_DIR = Path(__file__).resolve().parent
HOTWORD_FILE = APP_DIR / "data" / "infectious_disease_hotwords.txt"
HOTWORD_PACK_DIR = APP_DIR / "data" / "hotword_packs"
USER_HOTWORD_FILE = HOTWORD_PACK_DIR / "user_custom.txt"
FEEDBACK_FILE = APP_DIR / "data" / "feedback.jsonl"
HOTWORD_PACKS = [
    {"id": "general_medical", "filename": "general_medical.txt", "label": "通用医学词库", "label_en": "General medical", "built_in": True, "enabled": True},
    {"id": "infectious_disease", "filename": "infectious_disease.txt", "label": "感染科词库", "label_en": "Infectious disease", "built_in": True, "enabled": True},
    {"id": "antimicrobials", "filename": "antimicrobials.txt", "label": "抗菌药词库", "label_en": "Antimicrobials", "built_in": True, "enabled": True},
    {"id": "pathogens", "filename": "pathogens.txt", "label": "病原体词库", "label_en": "Pathogens", "built_in": True, "enabled": True},
    {"id": "user_custom", "filename": "user_custom.txt", "label": "用户自定义热词", "label_en": "User custom", "built_in": False, "enabled": True},
]
MODEL_NAME = os.getenv("ASR_MODEL", "paraformer-zh")
STREAMING_MODEL_NAME = os.getenv("ASR_STREAMING_MODEL", "paraformer-zh-streaming")
EN_MODEL_NAME = os.getenv("ASR_MODEL_EN", "paraformer-en")
DEVICE = os.getenv("ASR_DEVICE", "cpu")
MAX_HOTWORDS = 1000

app = FastAPI(title="病历助手本地服务", version="0.10.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^(chrome-extension|edge-extension)://.*$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["*"],
)

model = None
english_model = None
model_lock = Lock()
english_model_lock = Lock()
streaming_model = None
streaming_model_lock = Lock()
streaming_model_error = None
funasr_runtime_lock = Lock()

def read_words_from_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    words: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if value and not value.startswith("#") and value not in words:
            words.append(value)
    return words


def clean_hotword_values(words: list[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in words:
        value = re.sub(r"\s+", " ", str(raw)).strip()
        if not value or value.startswith("#"):
            continue
        if len(value) > 80:
            raise HTTPException(status_code=400, detail=f"热词过长：{value[:20]}…")
        if value not in cleaned:
            cleaned.append(value)
    if len(cleaned) > MAX_HOTWORDS:
        raise HTTPException(status_code=400, detail=f"自定义热词不能超过 {MAX_HOTWORDS} 个")
    return cleaned


def pack_path(pack: dict) -> Path:
    return HOTWORD_PACK_DIR / pack["filename"]


def read_pack_words(pack: dict) -> list[str]:
    return read_words_from_file(pack_path(pack))


def read_custom_hotwords() -> list[str]:
    if USER_HOTWORD_FILE.exists():
        return read_words_from_file(USER_HOTWORD_FILE)
    return read_words_from_file(HOTWORD_FILE)


def read_hotwords() -> list[str]:
    combined: list[str] = []
    for pack in HOTWORD_PACKS:
        if not pack.get("enabled", True):
            continue
        words = read_custom_hotwords() if pack["id"] == "user_custom" else read_pack_words(pack)
        for word in words:
            if word not in combined:
                combined.append(word)
    return combined


def load_hotwords() -> str:
    return " ".join(read_hotwords())


def write_hotwords(words: list[str]) -> list[str]:
    cleaned = clean_hotword_values(words)
    HOTWORD_PACK_DIR.mkdir(parents=True, exist_ok=True)
    content = "# 用户自定义热词。每行一个词。\n" + "\n".join(cleaned) + "\n"
    USER_HOTWORD_FILE.write_text(content, encoding="utf-8")
    HOTWORD_FILE.parent.mkdir(parents=True, exist_ok=True)
    HOTWORD_FILE.write_text(content, encoding="utf-8")
    return cleaned


def pack_payload(pack: dict) -> dict:
    words = read_custom_hotwords() if pack["id"] == "user_custom" else read_pack_words(pack)
    return {
        "id": pack["id"],
        "label": pack["label"],
        "label_en": pack["label_en"],
        "built_in": pack["built_in"],
        "enabled": pack.get("enabled", True),
        "count": len(words),
        "filename": pack["filename"],
    }


def get_model():
    global model
    if model is not None:
        return model
    with model_lock:
        if model is not None:
            return model
        try:
            model = build_asr_model(MODEL_NAME)
        except ImportError as exc:
            raise RuntimeError("尚未安装 FunASR，请先运行 install.bat") from exc
    return model


def get_english_model():
    global english_model
    if english_model is not None:
        return english_model
    with english_model_lock:
        if english_model is not None:
            return english_model
        if not EN_MODEL_NAME:
            raise RuntimeError("英文识别模型未配置，请设置 ASR_MODEL_EN")
        try:
            english_model = build_asr_model(EN_MODEL_NAME)
        except ImportError as exc:
            raise RuntimeError("尚未安装 FunASR，请先运行 install.bat") from exc
        except Exception as exc:
            raise RuntimeError(f"英文识别模型未就绪：{exc}") from exc
    return english_model


def resolve_language(value: str | None) -> str:
    normalized = (value or "zh-CN").lower().replace("_", "-")
    if normalized.startswith("en"):
        return "en-US"
    return "zh-CN"


def normalize_clinical_text(text: str) -> str:
    text = re.sub(r"\s+", "", text).strip()
    spoken_commands = [
        ("另起一段", "\n\n"), ("换一行", "\n"), ("换行", "\n"),
        ("句号", "。"), ("逗号", "，"), ("分号", "；"), ("冒号", "："),
        ("问号", "？"), ("左括号", "（"), ("右括号", "）"),
    ]
    for source, target in spoken_commands:
        text = text.replace(source, target)
    replacements = {
        "摄氏度": "℃", "毫克": " mg", "微克": " μg", "毫升": " mL",
        "国际单位": " IU", "百分之": "%", "每八小时一次": "q8h",
        "每日一次": "qd", "每日两次": "bid", "每日三次": "tid", "每日四次": "qid",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = re.sub(r"(\d)(mg|g|μg|ug|mL|ml|IU)\b", r"\1 \2", text, flags=re.IGNORECASE)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。\.]{2,}", "。", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def normalize_english_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    commands = {
        " period": ".", " comma": ",", " semicolon": ";", " colon": ":",
        " question mark": "?", " new line": "\n", " newline": "\n",
        " new paragraph": "\n\n",
    }
    padded = " " + value.lower()
    for source, target in commands.items():
        padded = padded.replace(source, target)
    value = padded.strip()
    value = re.sub(r"\s+([,.;:?])", r"\1", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def normalize_text_for_language(text: str, language: str) -> str:
    if language == "en-US":
        return normalize_english_text(text)
    return normalize_clinical_text(text)


@app.get("/health")
def health():
    return {
        "status": "ok", "version": app.version, "model": MODEL_NAME, "device": DEVICE,
        "license_tier": os.getenv("APP_LICENSE_TIER", "free"),
        "model_loaded": model is not None, "hotword_count": len(read_hotwords()),
        "streaming_model_loaded": streaming_model is not None,
        "streaming_supported": True,
        "streaming_model_error": streaming_model_error,
        "build": "medical-vocab-packs-20260624",
        "languages": {"ui": ["zh-CN", "en-US"], "dictation": ["zh-CN", "en-US"]},
        "english_model": EN_MODEL_NAME,
        "english_model_loaded": english_model is not None,
        "hotword_pack_count": len(HOTWORD_PACKS),
    }


@app.get("/license/status")
def license_status():
    """Return the local feature tier.

    v0.6 ships as a free local-first MVP. This endpoint gives the extension,
    installer, and future Pro/Hospital licensing flow a stable integration point
    without changing dictation behavior.
    """
    return {
        "tier": os.getenv("APP_LICENSE_TIER", "free"),
        "status": os.getenv("APP_LICENSE_STATUS", "active"),
        "offline": True,
        "features": {
            "local_asr": True,
            "streaming_asr": True,
            "pause_punctuation": True,
            "templates": True,
            "multi_patient_drafts": True,
            "hotwords": True,
            "advanced_qc_rules": False,
            "organization_management": False,
            "beta_feedback": True,
            "english_dictation": True,
            "bilingual_ui": True,
        },
        "message": "当前为免费本地版；后续 Pro/机构版可在此接口接入授权状态。",
    }

@app.get("/hotwords")
def get_hotwords():
    words = read_hotwords()
    return {"words": words, "count": len(words)}


@app.put("/hotwords")
def update_hotwords(words: list[str] = Body(..., embed=True)):
    saved = write_hotwords(words)
    return {"words": saved, "count": len(saved)}


@app.get("/hotword-packs")
def get_hotword_packs():
    packs = [pack_payload(pack) for pack in HOTWORD_PACKS]
    return {"packs": packs, "total_count": len(read_hotwords()), "sources": "server/data/hotword_packs/SOURCES.md"}


@app.get("/hotword-packs/export")
def export_hotword_packs():
    packs = []
    for pack in HOTWORD_PACKS:
        words = read_custom_hotwords() if pack["id"] == "user_custom" else read_pack_words(pack)
        packs.append({**pack_payload(pack), "words": words})
    return {
        "version": app.version,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "packs": packs,
        "note": "Built-in packs are read-only. Import writes to user_custom only.",
    }


@app.post("/hotword-packs/import")
def import_hotword_pack(payload: dict = Body(...)):
    words: list[str] = []
    if isinstance(payload.get("words"), list):
        words.extend(str(item) for item in payload["words"])
    if isinstance(payload.get("text"), str):
        words.extend(payload["text"].splitlines())
    if isinstance(payload.get("packs"), list):
        for pack in payload["packs"]:
            if isinstance(pack, dict) and pack.get("id") == "user_custom" and isinstance(pack.get("words"), list):
                words.extend(str(item) for item in pack["words"])
    if not words:
        raise HTTPException(status_code=400, detail="未找到可导入的热词")
    existing = read_custom_hotwords()
    merged = existing[:]
    for word in clean_hotword_values(words):
        if word not in merged:
            merged.append(word)
    saved = write_hotwords(merged)
    return {"ok": True, "count": len(saved), "added": len(saved) - len(existing), "words": saved}


@app.post("/feedback")
def submit_feedback(payload: dict = Body(...)):
    category = str(payload.get("category", "general")).strip()[:40] or "general"
    rating = payload.get("rating", None)
    try:
        rating = int(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None
    if rating is not None and not 1 <= rating <= 5:
        raise HTTPException(status_code=400, detail="评分必须在 1 到 5 之间")

    message = str(payload.get("message", "")).strip()
    if len(message) < 3:
        raise HTTPException(status_code=400, detail="请至少填写 3 个字的反馈内容")
    if len(message) > 3000:
        raise HTTPException(status_code=400, detail="反馈内容不能超过 3000 字")

    contact = str(payload.get("contact", "")).strip()[:120]
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    record = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "category": category,
        "rating": rating,
        "message": message,
        "contact": contact,
        "diagnostics": diagnostics,
        "app_version": app.version,
        "license_tier": os.getenv("APP_LICENSE_TIER", "free"),
    }
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    import json
    with FEEDBACK_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"ok": True, "id": record["id"], "saved_to": str(FEEDBACK_FILE)}


@app.get("/feedback/export")
def export_feedback():
    if not FEEDBACK_FILE.exists():
        return {"count": 0, "items": []}
    import json
    items = []
    for line in FEEDBACK_FILE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {"count": len(items), "items": items}



def run_batch_generate(recognizer, kwargs):
    with funasr_runtime_lock:
        return recognizer.generate(**kwargs)

@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...), department: str = Form(default="infectious_disease"), language: str = Form(default="zh-CN")):
    if file.content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(status_code=400, detail="当前版本仅接受 WAV 音频")
    audio = await file.read()
    if len(audio) < 1024:
        raise HTTPException(status_code=400, detail="录音过短，请重新听写")
    if len(audio) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="录音过长，请分段听写")
    temp_path = None
    started = time.perf_counter()
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
            temp.write(audio)
            temp_path = temp.name
        resolved_language = resolve_language(language)
        recognizer = await asyncio.to_thread(get_english_model if resolved_language == "en-US" else get_model)
        kwargs = {"input": temp_path, "batch_size_s": 60}
        if resolved_language == "zh-CN" and department == "infectious_disease":
            kwargs["hotword"] = load_hotwords()
        result = await asyncio.to_thread(run_batch_generate, recognizer, kwargs)
        text = result[0].get("text", "") if result else ""
        text = normalize_text_for_language(text, resolved_language)
        if not text:
            raise HTTPException(status_code=422, detail="未识别到有效语音")
        return {"text": text, "elapsed_seconds": time.perf_counter() - started, "model": EN_MODEL_NAME if resolved_language == "en-US" else MODEL_NAME, "device": DEVICE, "language": resolved_language}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"本地识别失败：{exc}") from exc
    finally:
        if temp_path:
            Path(temp_path).unlink(missing_ok=True)


def ensure_terminal_punctuation(text: str) -> str:
    value = text.strip()
    if value and re.search(r"[\u4e00-\u9fffA-Za-z0-9]", value) and not re.search(r"[。！？；：，、,.!?;:]$", value):
        value += "。"
    return value


def apply_pause_punctuation(text: str, duration_ms: int) -> str:
    value = text.rstrip()
    if not value or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", value):
        return value
    if duration_ms >= 1400:
        if value.endswith("，"):
            return value[:-1] + "。"
        if not re.search(r"[。！？；：]$", value):
            return value + "。"
    elif duration_ms >= 650 and not re.search(r"[。！？；：，、]$", value):
        return value + "，"
    return value

def meaningful_stream_text(text: str) -> str:
    normalized = normalize_clinical_text(text)
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", normalized):
        return ""
    if re.fullmatch(r"[嗯啊呃哦唉哎]+", normalized):
        return ""
    return normalized


def get_streaming_model():
    global streaming_model, streaming_model_error
    if streaming_model is not None:
        return streaming_model
    with streaming_model_lock:
        if streaming_model is not None:
            return streaming_model
        try:
            with funasr_runtime_lock:
                from funasr import AutoModel
                streaming_model = AutoModel(
                    model=STREAMING_MODEL_NAME,
                    device=DEVICE,
                    disable_update=True,
                )
            streaming_model_error = None
        except Exception as exc:
            streaming_model_error = str(exc)
            raise
    return streaming_model


async def preload_streaming_model():
    global streaming_model_error
    try:
        await asyncio.to_thread(get_streaming_model)
    except Exception as exc:
        streaming_model_error = str(exc)


@app.on_event("startup")
async def start_streaming_preload():
    if os.getenv("ASR_PRELOAD_STREAMING", "1") == "1":
        asyncio.create_task(preload_streaming_model())


def run_streaming_generate(recognizer, samples, cache, is_final, chunk_size, hotword_str):
    with funasr_runtime_lock:
        return recognizer.generate(
            input=samples,
            cache=cache,
            is_final=is_final,
            chunk_size=chunk_size,
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
            hotword=hotword_str,
        )


@app.websocket("/ws/transcribe")
async def ws_transcribe(websocket: WebSocket):
    await websocket.accept()
    try:
        config = await websocket.receive_json()
        department = config.get("department", "infectious_disease")
        language = resolve_language(config.get("language", "zh-CN"))
        if language != "zh-CN":
            await websocket.send_json({"type": "error", "detail": "English streaming is not enabled yet; use batch dictation."})
            await websocket.close(code=1003)
            return
        chunk_size = config.get("chunk_size", [5, 10, 5])
        await websocket.send_json({"type": "status", "status": "loading", "detail": "正在准备流式模型"})
        recognizer = await asyncio.to_thread(get_streaming_model)
        await websocket.send_json({"type": "ready"})

        cache = {}
        hotword_str = load_hotwords() if department == "infectious_disease" else ""
        full_text = ""
        pause_open = False

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message:
                import json
                try:
                    cmd = json.loads(message["text"])
                except (json.JSONDecodeError, TypeError):
                    cmd = {}
                if cmd.get("type") == "pause":
                    duration_ms = max(0, min(int(cmd.get("duration_ms", 0)), 10000))
                    if not pause_open:
                        result = await asyncio.to_thread(
                            run_streaming_generate,
                            recognizer,
                            np.zeros(1600, dtype=np.float32),
                            cache,
                            True,
                            chunk_size,
                            hotword_str,
                        )
                        if result and result[0].get("text"):
                            full_text += result[0]["text"]
                        cache = {}
                        pause_open = True
                    punctuated = apply_pause_punctuation(full_text, duration_ms)
                    if punctuated != full_text:
                        full_text = punctuated
                        await websocket.send_json({"type": "partial", "text": full_text, "pause_punctuation": True})
                    continue
                if cmd.get("type") == "end":
                    result = await asyncio.to_thread(
                        run_streaming_generate,
                        recognizer,
                        np.zeros(1600, dtype=np.float32),
                        cache,
                        True,
                        chunk_size,
                        hotword_str,
                    )
                    if result and result[0].get("text"):
                        full_text += result[0]["text"]
                    final_text = ensure_terminal_punctuation(meaningful_stream_text(full_text))
                    await websocket.send_json({"type": "final", "text": final_text})
                    break
                continue

            if "bytes" in message:
                audio_bytes = message["bytes"]
                if not audio_bytes:
                    continue
                samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                if np.sqrt(np.mean(samples * samples)) >= 0.008:
                    pause_open = False
                if len(samples) < 160:
                    continue
                result = await asyncio.to_thread(
                    run_streaming_generate,
                    recognizer,
                    samples,
                    cache,
                    False,
                    chunk_size,
                    hotword_str,
                )
                if result and result[0].get("text"):
                    full_text += result[0]["text"]
                    partial = meaningful_stream_text(full_text)
                    if partial:
                        await websocket.send_json({"type": "partial", "text": partial})

        if websocket.client_state.name == "CONNECTED":
            await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
            await websocket.close(code=1011)
        except Exception:
            pass
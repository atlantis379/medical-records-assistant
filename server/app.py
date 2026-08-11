import asyncio
import importlib.util
import io
import json
import os
import platform
import re
import sys
import tempfile
import time
import uuid
import wave
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
    {"id": "respiratory_history", "filename": "respiratory_history.txt", "label": "呼吸道病史词库", "label_en": "Respiratory history", "built_in": True, "enabled": True},
    {"id": "medical_history", "filename": "medical_history.txt", "label": "既往史词库", "label_en": "Past medical history", "built_in": True, "enabled": True},
    {"id": "clinical_metrics", "filename": "clinical_metrics.txt", "label": "临床指标词库", "label_en": "Clinical metrics", "built_in": True, "enabled": True},
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
CORRECTION_RULE_DIR = APP_DIR / "data" / "correction_rules"
STREAMING_RMS_THRESHOLD = float(os.getenv("ASR_STREAMING_RMS_THRESHOLD", "0.008"))
ASR_PROFILES = {
    "fast": {"id": "fast", "label": "快速", "label_en": "Fast", "batch_size_s": 45, "stream_chunk_size": [0, 8, 4], "encoder_chunk_look_back": 3, "decoder_chunk_look_back": 1, "hotword_limit": 190, "hotword_char_limit": 3200, "description": "低延迟优先，减少上下文和热词数量。"},
    "balanced": {"id": "balanced", "label": "均衡", "label_en": "Balanced", "batch_size_s": 60, "stream_chunk_size": [5, 10, 5], "encoder_chunk_look_back": 4, "decoder_chunk_look_back": 1, "hotword_limit": 320, "hotword_char_limit": 5600, "description": "兼顾识别速度、准确度和 CPU 占用。"},
    "accurate": {"id": "accurate", "label": "准确优先", "label_en": "Accuracy first", "batch_size_s": 90, "stream_chunk_size": [5, 12, 6], "encoder_chunk_look_back": 6, "decoder_chunk_look_back": 2, "hotword_limit": 480, "hotword_char_limit": 8200, "description": "更多上下文和热词，CPU 推理会更慢。"},
}
DEFAULT_ASR_PROFILE = os.getenv("ASR_PROFILE", "balanced")
MODEL_PACKAGE_ROOT = APP_DIR.parent / "models" / "modelscope"
MODEL_CACHE_ALIASES = {
    # FunASR accepts short aliases, while ModelScope stores the expanded model ids.
    "paraformer-zh": [
        "paraformer-zh",
        "speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
    ],
    "paraformer-zh-streaming": [
        "paraformer-zh-streaming",
        "speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online",
    ],
    "fsmn-vad": [
        "fsmn-vad",
        "speech_fsmn_vad_zh-cn-16k-common-pytorch",
    ],
    "ct-punc": [
        "ct-punc",
        "punc_ct-transformer_cn-en-common-vocab471067-large",
    ],
}

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
asr_stats_lock = Lock()
asr_stats = {
    "batch_requests": 0,
    "streaming_sessions": 0,
    "total_audio_seconds": 0.0,
    "total_inference_seconds": 0.0,
    "last_batch": None,
    "last_streaming": None,
}


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


def read_hotword_entries() -> list[dict]:
    entries: list[dict] = []
    priority = {"user_custom": 0, "respiratory_history": 1, "medical_history": 2, "clinical_metrics": 3, "antimicrobials": 4, "pathogens": 5, "infectious_disease": 6, "general_medical": 7}
    for pack in HOTWORD_PACKS:
        if not pack.get("enabled", True):
            continue
        words = read_custom_hotwords() if pack["id"] == "user_custom" else read_pack_words(pack)
        for order, word in enumerate(words):
            entries.append({"word": word, "pack_id": pack["id"], "priority": priority.get(pack["id"], 9), "order": order})
    return entries


def read_hotwords() -> list[str]:
    combined: list[str] = []
    seen: set[str] = set()
    for entry in read_hotword_entries():
        key = entry["word"].casefold()
        if key not in seen:
            combined.append(entry["word"])
            seen.add(key)
    return combined


def resolve_asr_profile(value: str | None = None) -> dict:
    key = (value or DEFAULT_ASR_PROFILE or "balanced").strip().lower()
    return ASR_PROFILES.get(key, ASR_PROFILES["balanced"])


def active_hotwords(profile: dict | None = None, department: str = "infectious_disease") -> list[str]:
    if department != "infectious_disease":
        return []
    cfg = profile or resolve_asr_profile()
    limit = int(cfg.get("hotword_limit", 260))
    char_limit = int(cfg.get("hotword_char_limit", 4200))
    selected: list[str] = []
    seen: set[str] = set()
    total_chars = 0
    entries = sorted(read_hotword_entries(), key=lambda item: (item["priority"], -len(item["word"]), item["order"]))
    for entry in entries:
        word = entry["word"].strip()
        key = word.casefold()
        if not word or key in seen:
            continue
        projected = total_chars + len(word) + 1
        if len(selected) >= limit or projected > char_limit:
            break
        selected.append(word)
        seen.add(key)
        total_chars = projected
    return selected


def load_hotwords(profile: dict | None = None, department: str = "infectious_disease") -> str:
    return " ".join(active_hotwords(profile, department))


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


def build_asr_model(model_name: str):
    """Build a FunASR AutoModel lazily.

    Keep this small and environment-driven so offline beta packages can point
    ModelScope to bundled local caches without changing application code.
    """
    from funasr import AutoModel

    kwargs = {
        "model": model_name,
        "device": DEVICE,
        "disable_update": True,
    }
    vad_model = os.getenv("ASR_VAD_MODEL", "").strip()
    punc_model = os.getenv("ASR_PUNC_MODEL", "").strip()
    if vad_model:
        kwargs["vad_model"] = vad_model
    if punc_model:
        kwargs["punc_model"] = punc_model
    return AutoModel(**kwargs)


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


CN_DIGIT_MAP = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_NUMBER_PATTERN = r"[零〇一二两三四五六七八九十百点半]+"
NUMBER_TOKEN_PATTERN = rf"[<>≤≥]?\d+(?:\.\d+)?(?:[到至~～－—-]\d+(?:\.\d+)?)?|{CN_NUMBER_PATTERN}"
CLINICAL_METRIC_NAMES = [
    "超敏C反应蛋白", "C反应蛋白", "CRP", "降钙素原", "PCT",
    "白细胞计数", "白细胞", "中性粒细胞比例", "中性粒细胞", "淋巴细胞比例", "淋巴细胞",
    "血红蛋白", "血小板", "血糖", "空腹血糖", "随机血糖", "乳酸",
    "肌酐", "尿素氮", "尿酸", "白蛋白", "总胆红素", "直接胆红素", "间接胆红素",
    "丙氨酸氨基转移酶", "天门冬氨酸氨基转移酶", "ALT", "AST",
    "D-二聚体", "D二聚体", "凝血酶原时间", "活化部分凝血活酶时间", "国际标准化比值", "INR",
]
CLINICAL_METRIC_PATTERN = "|".join(re.escape(name) for name in sorted(CLINICAL_METRIC_NAMES, key=len, reverse=True))
CLINICAL_UNIT_PATTERN = r"×10\^9/L|10\^9/L|mmol/L|μmol/L|umol/L|mg/L|ng/mL|g/L|U/L|IU/L|%|秒|s"


def parse_chinese_integer(value: str) -> int | None:
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if len(value) > 1 and all(char in CN_DIGIT_MAP for char in value):
        return int("".join(str(CN_DIGIT_MAP[char]) for char in value))
    if "百" in value:
        left, right = value.split("百", 1)
        hundreds = 1 if left == "" else CN_DIGIT_MAP.get(left)
        if hundreds is None:
            return None
        if not right:
            return hundreds * 100
        # Spoken shorthand such as "一百三" usually means 130 in vital signs/BP context.
        if "十" not in right and len(right) == 1 and right in CN_DIGIT_MAP:
            return hundreds * 100 + CN_DIGIT_MAP[right] * 10
        rest = parse_chinese_integer(right)
        return None if rest is None else hundreds * 100 + rest
    if "十" in value:
        left, right = value.split("十", 1)
        tens = 1 if left == "" else CN_DIGIT_MAP.get(left)
        ones = 0 if right == "" else CN_DIGIT_MAP.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    if len(value) == 1:
        return CN_DIGIT_MAP.get(value)
    return None


def spoken_number_to_digits(value: str) -> str:
    raw = (value or "").strip().replace("．", ".")
    if not raw:
        return raw
    if re.search(r"\d", raw):
        return re.sub(r"(?<=\d)[到至~～－—](?=\d)", "-", raw)
    if raw.endswith("半"):
        base = parse_chinese_integer(raw[:-1])
        if base is not None:
            return f"{base}.5"
    if "点" in raw:
        left, right = raw.split("点", 1)
        integer = parse_chinese_integer(left) if left else 0
        if integer is None:
            return raw
        decimals = "".join(str(CN_DIGIT_MAP[char]) for char in right if char in CN_DIGIT_MAP)
        return f"{integer}.{decimals}" if decimals else str(integer)
    integer = parse_chinese_integer(raw)
    return str(integer) if integer is not None else raw


def normalize_unit_label(unit: str | None) -> str:
    if not unit:
        return ""
    value = unit.replace("／", "/")
    lowered = value.lower()
    if lowered == "umol/l":
        return "μmol/L"
    if lowered == "ng/ml":
        return "ng/mL"
    if lowered in {"u/l", "iu/l", "mmol/l", "mg/l", "g/l"}:
        return {"u/l": "U/L", "iu/l": "IU/L", "mmol/l": "mmol/L", "mg/l": "mg/L", "g/l": "g/L"}[lowered]
    if lowered == "10^9/l":
        return "×10^9/L"
    return value


def format_number_with_unit(number: str, unit: str | None = "") -> str:
    normalized_unit = normalize_unit_label(unit)
    if not normalized_unit:
        return number
    if normalized_unit in {"%", "℃", "秒", "s"} or normalized_unit.startswith("×10^"):
        return f"{number}{normalized_unit}"
    return f"{number} {normalized_unit}"


def canonical_metric_name(name: str) -> str:
    lowered = name.lower()
    aliases = {
        "crp": "CRP",
        "pct": "PCT",
        "alt": "ALT",
        "ast": "AST",
        "inr": "INR",
        "d二聚体": "D-二聚体",
    }
    return aliases.get(lowered, name)


def metric_value_prefix(prefix: str | None) -> str:
    value = prefix or ""
    aliases = {
        "大于": ">",
        "高于": ">",
        "超过": ">",
        "小于": "<",
        "低于": "<",
        "不超过": "≤",
        "不高于": "≤",
        "不低于": "≥",
        "约": "约",
    }
    return aliases.get(value, "")


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
    text = normalize_clinical_metric_text(text)
    text = re.sub(r"(\d)(mg|g|μg|ug|mL|ml|IU|U|mmol/L|μmol/L|umol/L|mg/L|ng/mL|g/L|U/L|IU/L)\b", r"\1 \2", text, flags=re.IGNORECASE)
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。\.]{2,}", "。", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def normalize_clinical_metric_text(text: str) -> str:
    value = normalize_blood_pressure_text(text)
    value = normalize_vital_sign_text(value)
    value = normalize_lab_unit_text(value)
    return normalize_lab_metric_text(value)


def normalize_blood_pressure_text(text: str) -> str:
    value = text or ""
    phrase_rules = [
        ("一百三十到一百四十，八十到九十毫米汞柱", "130-140/80-90mmHg"),
        ("一百三十到一百四十/八十到九十毫米汞柱", "130-140/80-90mmHg"),
        ("一百三十到一百四十八十到九十毫米汞柱", "130-140/80-90mmHg"),
        ("一百三到一百四，八十到九十毫米汞柱", "130-140/80-90mmHg"),
        ("一百三到一百四/八十到九十毫米汞柱", "130-140/80-90mmHg"),
        ("一百三到一百四八十到九十毫米汞柱", "130-140/80-90mmHg"),
        ("一百三十到一百四十，八十到九十mmHg", "130-140/80-90mmHg"),
        ("一百三十到一百四十/八十到九十mmHg", "130-140/80-90mmHg"),
        ("一百三十到一百四十八十到九十mmHg", "130-140/80-90mmHg"),
        ("一百三到一百四，八十到九十mmHg", "130-140/80-90mmHg"),
        ("一百三到一百四/八十到九十mmHg", "130-140/80-90mmHg"),
        ("一百三到一百四八十到九十mmHg", "130-140/80-90mmHg"),
        ("一百六一百毫米汞柱", "160/100mmHg"),
        ("一百六一百mmHg", "160/100mmHg"),
    ]
    for source, target in phrase_rules:
        value = value.replace(source, target)
    value = value.replace("毫米汞柱", "mmHg").replace("毫米汞汞柱", "mmHg")
    value = re.sub(r"(?i)\s*mm\s*hg\b", "mmHg", value)
    value = re.sub(r"(?i)\s*mmhg\b", "mmHg", value)
    value = re.sub(
        r"(?<!\d)(\d{2,3})[到至~～－—-](\d{2,3})[、，,/\s]+(\d{2,3})[到至~～－—-](\d{2,3})\s*mmHg",
        r"\1-\2/\3-\4mmHg",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(?<!\d)(\d{2,3})[到至~～－—-](\d{2,3})/(\d{2,3})[到至~～－—-](\d{2,3})\s*mmHg",
        r"\1-\2/\3-\4mmHg",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(?<!\d)(\d{2,3})-(\d{2,3})/(\d{2,3})-(\d{2,3})\s*mmHg",
        r"\1-\2/\3-\4mmHg",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<!\d)(\d{2,3})/(\d{2,3})\s*mmHg", r"\1/\2mmHg", value, flags=re.IGNORECASE)
    return value


def normalize_vital_sign_text(text: str) -> str:
    value = text or ""
    value = re.sub(r"(?i)\bspo\s*2\b", "SpO2", value)
    value = re.sub(r"(?i)\bspo2\b", "SpO2", value)
    value = re.sub(r"次\s*(?:每分钟|每分|/分钟|／分钟)", "次/分", value)

    def replace_temperature(match: re.Match) -> str:
        name = match.group(1)
        number = spoken_number_to_digits(match.group(2))
        tail = match.group(3) or ""
        if tail and "." not in number:
            tail_number = spoken_number_to_digits(tail)
            if re.fullmatch(r"\d", tail_number):
                number = f"{number}.{tail_number}"
        return f"{name}{number}℃"

    value = re.sub(
        rf"(体温|T)({NUMBER_TOKEN_PATTERN})(?:摄氏度|℃|度)([零〇一二两三四五六七八九\d])?",
        replace_temperature,
        value,
        flags=re.IGNORECASE,
    )

    def replace_rate(match: re.Match) -> str:
        return f"{match.group(1)}{spoken_number_to_digits(match.group(2))}次/分"

    rate_names = r"心率|脉搏|呼吸频率|呼吸"
    value = re.sub(rf"({rate_names})({NUMBER_TOKEN_PATTERN})(?:次)?/分", replace_rate, value)
    value = re.sub(rf"({rate_names})({NUMBER_TOKEN_PATTERN})次(?!/分)", replace_rate, value)

    oxygen_names = r"血氧饱和度|指脉氧|SpO2"

    def replace_spoken_percent(match: re.Match) -> str:
        return f"{match.group(1)}{spoken_number_to_digits(match.group(2))}%"

    def replace_numeric_oxygen(match: re.Match) -> str:
        name, number = match.group(1), match.group(2)
        try:
            numeric = float(number)
        except ValueError:
            return match.group(0)
        if 50 <= numeric <= 100:
            return f"{name}{number}%"
        return match.group(0)

    value = re.sub(rf"({oxygen_names})(?:为|是|达|约)?(?:百分之|%)({CN_NUMBER_PATTERN})", replace_spoken_percent, value, flags=re.IGNORECASE)
    value = re.sub(rf"({oxygen_names})(?:为|是|达|约)?(\d{{2,3}}(?:\.\d+)?)(?:百分比|%)", r"\1\2%", value, flags=re.IGNORECASE)
    value = re.sub(rf"({oxygen_names})(?:为|是|达|约)?(\d{{2,3}})(?![\d%])", replace_numeric_oxygen, value, flags=re.IGNORECASE)
    return value


def normalize_lab_unit_text(text: str) -> str:
    value = text or ""
    value = value.replace("百分比", "%")
    unit_rules = [
        (r"(?:乘以)?(?:十的九次方|10的9次方)(?:每升|/升|／升)?", "×10^9/L"),
        (r"(?i)(?:x|×)\s*10\s*(?:\^|的)?\s*9\s*/?\s*(?:l|L|升)", "×10^9/L"),
        (r"(?i)\s*(?:mmol|毫摩尔)\s*(?:每升|/升|／升|/L|／L)", "mmol/L"),
        (r"(?i)\s*(?:μmol|umol|微摩尔)\s*(?:每升|/升|／升|/L|／L)", "μmol/L"),
        (r"(?i)\s*(?:ng|纳克)\s*(?:每\s*(?:毫升|ml|mL)|[/／]\s*(?:毫升|ml|mL))", "ng/mL"),
        (r"(?i)\s*(?:mg|毫克)\s*(?:每升|/升|／升|/L|／L)", "mg/L"),
        (r"(?i)\s*(?:g|克)\s*(?:每升|/升|／升|/L|／L)", "g/L"),
        (r"(?i)\s*(?:IU|国际单位)\s*(?:每升|/升|／升|/L|／L)", "IU/L"),
        (r"(?i)\s*(?:U|单位)\s*(?:每升|/升|／升|/L|／L)", "U/L"),
    ]
    for pattern, replacement in unit_rules:
        value = re.sub(pattern, replacement, value)
    value = value.replace("μmol/L", "μmol/L").replace("umol/L", "μmol/L")
    value = re.sub(r"(?i)ng/ml", "ng/mL", value)
    value = re.sub(r"(?i)mmol/l", "mmol/L", value)
    value = re.sub(r"(?i)mg/l", "mg/L", value)
    value = re.sub(r"(?i)g/l", "g/L", value)
    value = re.sub(r"(?i)\bu/l\b", "U/L", value)
    value = re.sub(r"(?i)\biu/l\b", "IU/L", value)
    return value


def normalize_lab_metric_text(text: str) -> str:
    value = text or ""
    connector_pattern = r"为|是|约|达|升高至|降低至|大于|高于|超过|小于|低于|不超过|不高于|不低于|[:：]"

    def replace_metric_percent(match: re.Match) -> str:
        name = canonical_metric_name(match.group(1))
        number = metric_value_prefix(match.group(2)) + spoken_number_to_digits(match.group(3))
        return f"{name} {number}%"

    def replace_metric(match: re.Match) -> str:
        name = canonical_metric_name(match.group(1))
        number = metric_value_prefix(match.group(2)) + spoken_number_to_digits(match.group(3))
        unit = normalize_unit_label(match.group(4) or "")
        return f"{name} {format_number_with_unit(number, unit)}"

    value = re.sub(
        rf"({CLINICAL_METRIC_PATTERN})(?:({connector_pattern}))?(?:百分之|%)({NUMBER_TOKEN_PATTERN})",
        replace_metric_percent,
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        rf"({CLINICAL_METRIC_PATTERN})(?:({connector_pattern}))?({NUMBER_TOKEN_PATTERN})\s*({CLINICAL_UNIT_PATTERN})?",
        replace_metric,
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\bCRP\s*([<>≤≥]?\d+(?:\.\d+)?)", r"CRP \1", value, flags=re.IGNORECASE)
    value = re.sub(r"\bPCT\s*([<>≤≥]?\d+(?:\.\d+)?)", r"PCT \1", value, flags=re.IGNORECASE)
    value = re.sub(r"\bINR\s*([<>≤≥]?\d+(?:\.\d+)?)", r"INR \1", value, flags=re.IGNORECASE)
    return value


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


def analyze_wav_quality(audio: bytes) -> dict:
    quality = {"duration_seconds": 0.0, "sample_rate": None, "channels": None, "sample_width": None, "rms_dbfs": None, "peak_dbfs": None, "clipping_percent": 0.0, "warnings": []}
    try:
        with wave.open(io.BytesIO(audio), "rb") as wf:
            channels = wf.getnchannels()
            sample_rate = wf.getframerate()
            sample_width = wf.getsampwidth()
            frames = wf.getnframes()
            quality.update({"duration_seconds": round(frames / sample_rate, 3) if sample_rate else 0.0, "sample_rate": sample_rate, "channels": channels, "sample_width": sample_width})
            raw = wf.readframes(frames)
    except Exception as exc:
        quality["warnings"].append(f"无法解析 WAV 音频：{exc}")
        return quality
    if quality["duration_seconds"] and quality["duration_seconds"] < 0.8:
        quality["warnings"].append("录音时间偏短，建议至少 1 秒以上。")
    if quality["sample_rate"] != 16000:
        quality["warnings"].append("建议使用 16kHz 单声道录音。")
    if quality["channels"] != 1:
        quality["warnings"].append("检测到非单声道音频，已合并声道后分析。")
    if quality["sample_width"] != 2 or not raw:
        return quality
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if quality["channels"] and quality["channels"] > 1:
        samples = samples.reshape(-1, quality["channels"]).mean(axis=1)
    if samples.size == 0:
        return quality
    rms = float(np.sqrt(np.mean(samples * samples)))
    peak = float(np.max(np.abs(samples)))
    clipping = float(np.mean(np.abs(samples) >= 0.98) * 100)
    quality["rms_dbfs"] = round(20 * np.log10(max(rms, 1e-9)), 1)
    quality["peak_dbfs"] = round(20 * np.log10(max(peak, 1e-9)), 1)
    quality["clipping_percent"] = round(clipping, 3)
    if quality["rms_dbfs"] < -38:
        quality["warnings"].append("录音音量偏低，建议靠近麦克风或调高输入增益。")
    if quality["rms_dbfs"] > -8:
        quality["warnings"].append("录音音量偏高，可能导致削波失真。")
    if clipping > 0.2:
        quality["warnings"].append("检测到削波/爆音，建议降低麦克风输入音量。")
    return quality


def directory_size_mb(path: Path) -> float | None:
    try:
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
        return round(total / 1024 / 1024, 1)
    except OSError:
        return None


def model_cache_roots() -> list[Path]:
    roots: list[Path] = []
    env_cache = os.getenv("MODELSCOPE_CACHE", "").strip()
    if env_cache:
        roots.append(Path(env_cache))
    roots.extend([
        MODEL_PACKAGE_ROOT,
        MODEL_PACKAGE_ROOT / "hub",
        Path.home() / ".cache" / "modelscope" / "hub",
        Path.home() / ".cache" / "modelscope",
    ])
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key not in seen:
            unique.append(root)
            seen.add(key)
    return unique


def model_cache_candidates(model_name: str) -> list[Path]:
    if not model_name:
        return []
    candidates: list[Path] = []
    aliases = MODEL_CACHE_ALIASES.get(model_name, [model_name])
    if model_name not in aliases:
        aliases = [model_name, *aliases]
    for alias in aliases:
        owners: list[str] = []
        leaf = alias
        if "/" in alias:
            owner, leaf = alias.split("/", 1)
            owners.append(owner)
        owners.extend(["iic", "damo", "modelscope", "FunAudioLLM", "QwenAudio"])
        for root in model_cache_roots():
            for owner in owners:
                candidates.append(root / "models" / owner / leaf)
                candidates.append(root / "hub" / "models" / owner / leaf)
            candidates.append(root / "models" / leaf)
            candidates.append(root / leaf)
    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def model_package_status() -> list[dict]:
    packages = [
        {"id": "zh_default", "label": "中文默认识别模型", "env": "ASR_MODEL", "model": MODEL_NAME, "required": True, "default_included": True, "loaded": model is not None},
        {"id": "zh_streaming", "label": "中文流式识别模型", "env": "ASR_STREAMING_MODEL", "model": STREAMING_MODEL_NAME, "required": True, "default_included": True, "loaded": streaming_model is not None, "error": streaming_model_error},
        {"id": "en_optional", "label": "英文可选识别模型", "env": "ASR_MODEL_EN", "model": EN_MODEL_NAME, "required": False, "default_included": False, "loaded": english_model is not None},
    ]
    result: list[dict] = []
    for package in packages:
        candidates = model_cache_candidates(package.get("model", ""))
        existing = [path for path in candidates if path.exists()]
        result.append({
            **package,
            "configured": bool(package.get("model")),
            "installed": bool(existing),
            "path": str(existing[0]) if existing else None,
            "size_mb": directory_size_mb(existing[0]) if existing else None,
            "checked_paths": [str(path) for path in candidates[:8]],
        })
    return result


def module_status(module_name: str) -> dict:
    spec = importlib.util.find_spec(module_name)
    return {
        "name": module_name,
        "ok": spec is not None,
        "origin": getattr(spec, "origin", None) if spec else None,
    }


def build_self_check() -> dict:
    dependencies = [module_status(name) for name in ["fastapi", "uvicorn", "numpy", "funasr", "modelscope", "torch"]]
    models = model_package_status()
    hotword_packs = [pack_payload(pack) for pack in HOTWORD_PACKS]
    correction_count = len(read_correction_rules())
    missing_required = [item["label"] for item in models if item["required"] and not item["loaded"] and not item["installed"]]
    checks: list[dict] = [
        {"id": "service", "label": "本地服务", "status": "pass", "detail": "127.0.0.1:8765 已响应。"},
        {"id": "dependencies", "label": "Python 依赖", "status": "pass" if all(item["ok"] for item in dependencies[:4]) else "fail", "detail": f"{sum(1 for item in dependencies if item['ok'])}/{len(dependencies)} 个核心模块可导入。"},
        {"id": "hotwords", "label": "医学词库", "status": "pass" if read_hotwords() else "warning", "detail": f"共 {len(read_hotwords())} 个热词，当前模式启用 {len(active_hotwords(resolve_asr_profile()))} 个。"},
        {"id": "corrections", "label": "医学后处理", "status": "pass" if correction_count else "warning", "detail": f"已加载 {correction_count} 条保守纠错规则。"},
        {"id": "models", "label": "模型包", "status": "warning" if missing_required else "pass", "detail": "缺少：" + "、".join(missing_required) if missing_required else "中文默认、中文流式模型已在常见缓存目录发现或已加载。"},
        {"id": "streaming", "label": "流式识别", "status": "warning" if streaming_model_error else "pass", "detail": streaming_model_error or "未发现流式模型错误。"},
    ]
    if DEVICE == "cpu":
        checks.append({"id": "performance", "label": "性能模式", "status": "pass", "detail": "当前为 CPU 推理，可正常测试；建议优先使用“快速/均衡”模式。"})
    with asr_stats_lock:
        performance = dict(asr_stats)
    warnings = []
    if missing_required:
        warnings.append("未在常见缓存目录发现部分必需模型；如果首次识别会自动下载，请确认网络或使用离线包。")
    if streaming_model_error:
        warnings.append(f"流式模型最近加载失败：{streaming_model_error}")
    if not all(item["ok"] for item in dependencies[:4]):
        warnings.append("核心 Python 依赖不完整，请重新运行 install.bat。")
    overall = "fail" if any(item["status"] == "fail" for item in checks) else ("warning" if warnings or any(item["status"] == "warning" for item in checks) else "pass")
    return {
        "overall": overall,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "app": {"name": "病历助手", "version": app.version, "build": "diagnostics-segmented-asr-20260811"},
        "runtime": {"python": sys.version.split()[0], "platform": platform.platform(), "device": DEVICE, "app_dir": str(APP_DIR), "modelscope_cache": os.getenv("MODELSCOPE_CACHE")},
        "checks": checks,
        "dependencies": dependencies,
        "models": models,
        "hotword_packs": hotword_packs,
        "hotword_total": len(read_hotwords()),
        "active_hotwords": len(active_hotwords(resolve_asr_profile())),
        "correction_rules": correction_count,
        "asr_profiles": list(ASR_PROFILES.values()),
        "performance": performance,
        "warnings": warnings,
        "privacy": "自检不读取、不上传病历正文和录音文件。",
    }


def read_correction_rules() -> list[dict]:
    rules: list[dict] = []
    if not CORRECTION_RULE_DIR.exists():
        return rules
    for path in sorted(CORRECTION_RULE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        items = payload.get("rules", payload if isinstance(payload, list) else [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            source = str(item.get("from", "")).strip()
            target = str(item.get("to", "")).strip()
            if source and target and source != target:
                rules.append({"from": source, "to": target, "category": item.get("category", "general"), "source": path.name})
    return rules


def apply_correction_rules(text: str, language: str = "zh-CN") -> tuple[str, list[dict]]:
    value = text or ""
    applied: list[dict] = []
    if not value:
        return value, applied
    for rule in read_correction_rules():
        source = rule["from"]
        target = rule["to"]
        if re.fullmatch(r"[A-Za-z0-9+./-]+", source):
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(source)}(?![A-Za-z0-9])", re.IGNORECASE)
            count = 0

            def replace_match(match: re.Match) -> str:
                nonlocal count
                if match.group(0) != target:
                    count += 1
                return target

            value = pattern.sub(replace_match, value)
        else:
            count = value.count(source)
            if count:
                value = value.replace(source, target)
        if count:
            applied.append({"from": source, "to": target, "count": count, "category": rule.get("category", "general")})
    if language == "zh-CN":
        value = cleanup_clinical_asr_artifacts(value)
    return value, applied


def cleanup_clinical_asr_artifacts(text: str) -> str:
    value = text or ""
    punctuation_rules = [
        ("主诉反复", "主诉：反复"),
        ("主诉：反复咳嗽咳痰3年。加重", "主诉：反复咳嗽、咳痰3年，加重"),
        ("气促1周现病史患者", "气促1周。\n现病史：患者"),
        ("气促一周现病史患者", "气促1周。\n现病史：患者"),
        ("现病史患者", "现病史：患者"),
        ("样痰偶有", "样痰，偶有"),
        ("咳嗽。咳白色", "咳嗽，咳白色"),
        ("呼吸困难曾于", "呼吸困难，曾于"),
        ("慢性支气管炎一周前", "慢性支气管炎。一周前"),
        ("慢性支气管炎1周前", "慢性支气管炎。1周前"),
        ("受凉后上述症状", "受凉后，上述症状"),
        ("发作咳嗽频繁", "发作，咳嗽频繁"),
        ("咳嗽频繁痰量", "咳嗽频繁，痰量"),
        ("脓痰不易", "脓痰，不易"),
        ("不易咳出自觉", "不易咳出，自觉"),
        ("气短活动后", "气短，活动后"),
        ("尤甚无胸痛", "尤甚，无胸痛"),
        ("无胸痛咯血", "无胸痛、咯血"),
        ("咯血无恶心", "咯血，无恶心"),
        ("无恶心呕吐", "无恶心、呕吐"),
        ("呕吐发病以来", "呕吐。发病以来"),
        ("欠佳二便", "欠佳，二便"),
        ("正常体重", "正常，体重"),
        ("既往史高血压", "既往史：高血压"),
        ("变化。既往史：", "变化。\n既往史："),
        ("5年最高", "5年，最高"),
        ("mmHg平日", "mmHg，平日"),
        ("缓释片控制血压", "缓释片控制，血压"),
        ("mmHg否认", "mmHg。否认"),
        ("病史否认肝炎", "病史，否认肝炎"),
        ("肝炎结核", "肝炎、结核"),
        ("传染病史。吸烟史", "传染病史。\n吸烟史"),
    ]
    for source, target in punctuation_rules:
        value = value.replace(source, target)
    # Remove an isolated Latin tail commonly emitted by ASR, e.g. "。i。".
    value = re.sub(r"([。！？；：，、,.!?;:])\s*[A-Za-z]\s*[。！？；：，、,.!?;:]*$", r"\1", value)
    return value.strip()


def record_asr_metric(kind: str, audio_seconds: float, elapsed_seconds: float, detail: dict) -> None:
    with asr_stats_lock:
        asr_stats["total_audio_seconds"] += max(0.0, float(audio_seconds or 0.0))
        asr_stats["total_inference_seconds"] += max(0.0, float(elapsed_seconds or 0.0))
        if kind == "batch":
            asr_stats["batch_requests"] += 1
            asr_stats["last_batch"] = detail
        elif kind == "streaming":
            asr_stats["streaming_sessions"] += 1
            asr_stats["last_streaming"] = detail


@app.get("/health")
def health():
    return {
        "status": "ok", "version": app.version, "model": MODEL_NAME, "device": DEVICE,
        "license_tier": os.getenv("APP_LICENSE_TIER", "free"),
        "model_loaded": model is not None, "hotword_count": len(read_hotwords()),
        "active_hotword_count": len(active_hotwords(resolve_asr_profile())),
        "streaming_model_loaded": streaming_model is not None,
        "streaming_supported": True,
        "streaming_model_error": streaming_model_error,
        "build": "asr-quality-performance-20260811",
        "languages": {"ui": ["zh-CN", "en-US"], "dictation": ["zh-CN", "en-US"]},
        "english_model": EN_MODEL_NAME,
        "english_model_loaded": english_model is not None,
        "hotword_pack_count": len(HOTWORD_PACKS),
        "asr_profile": resolve_asr_profile(),
        "correction_rule_count": len(read_correction_rules()),
    }


@app.get("/asr/config")
def asr_config(profile: str | None = None):
    selected_profile = resolve_asr_profile(profile)
    return {
        "default_profile": resolve_asr_profile(),
        "selected_profile": selected_profile,
        "profiles": list(ASR_PROFILES.values()),
        "total_hotwords": len(read_hotwords()),
        "active_hotwords": len(active_hotwords(selected_profile)),
        "correction_rules": len(read_correction_rules()),
    }


@app.get("/asr/performance")
def asr_performance():
    with asr_stats_lock:
        total_audio = asr_stats["total_audio_seconds"]
        total_infer = asr_stats["total_inference_seconds"]
        snapshot = dict(asr_stats)
    snapshot["average_realtime_factor"] = round(total_infer / total_audio, 3) if total_audio > 0 else None
    snapshot["profile"] = resolve_asr_profile()
    return snapshot


@app.get("/models/status")
def models_status():
    return {"packages": model_package_status(), "cache_roots": [str(path) for path in model_cache_roots()]}


@app.get("/diagnostics/self-check")
def diagnostics_self_check():
    return build_self_check()


@app.get("/correction-rules")
def correction_rules():
    rules = read_correction_rules()
    categories: dict[str, int] = {}
    for rule in rules:
        category = rule.get("category", "general")
        categories[category] = categories.get(category, 0) + 1
    return {"count": len(rules), "categories": categories, "rules": rules}


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
async def transcribe(
    file: UploadFile = File(...),
    department: str = Form(default="infectious_disease"),
    language: str = Form(default="zh-CN"),
    profile: str = Form(default="balanced"),
    segment_index: int = Form(default=1),
    segment_count: int = Form(default=1),
):
    if file.content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(status_code=400, detail="仅支持 WAV 音频")
    audio = await file.read()
    if len(audio) < 1024:
        raise HTTPException(status_code=400, detail="录音太短，请重新录制")
    if len(audio) > 30 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="录音文件过大，请缩短单次录音")
    temp_path = None
    started = time.perf_counter()
    quality = analyze_wav_quality(audio)
    cfg = resolve_asr_profile(profile)
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp:
            temp.write(audio)
            temp_path = temp.name
        resolved_language = resolve_language(language)
        recognizer = await asyncio.to_thread(get_english_model if resolved_language == "en-US" else get_model)
        kwargs = {"input": temp_path, "batch_size_s": cfg["batch_size_s"]}
        hotword_count = 0
        if resolved_language == "zh-CN" and department == "infectious_disease":
            hotwords = load_hotwords(cfg, department)
            hotword_count = len(hotwords.split()) if hotwords else 0
            if hotwords:
                kwargs["hotword"] = hotwords
        result = await asyncio.to_thread(run_batch_generate, recognizer, kwargs)
        raw_text = result[0].get("text", "") if result else ""
        normalized_text = normalize_text_for_language(raw_text, resolved_language)
        text = normalized_text
        text, corrections = apply_correction_rules(text, resolved_language)
        text = ensure_terminal_punctuation(text) if resolved_language == "zh-CN" else text
        if not text:
            raise HTTPException(status_code=422, detail="未识别到有效内容")
        elapsed = time.perf_counter() - started
        audio_seconds = float(quality.get("duration_seconds") or 0.0)
        realtime_factor = round(elapsed / audio_seconds, 3) if audio_seconds > 0 else None
        metrics = {
            "elapsed_seconds": elapsed,
            "audio_seconds": audio_seconds,
            "realtime_factor": realtime_factor,
            "profile": cfg["id"],
            "hotword_count": hotword_count,
            "correction_count": sum(item["count"] for item in corrections),
            "segment_index": max(1, int(segment_index or 1)),
            "segment_count": max(1, int(segment_count or 1)),
        }
        record_asr_metric("batch", audio_seconds, elapsed, metrics)
        return {
            "text": text,
            "raw_text": raw_text,
            "normalized_text": normalized_text,
            "elapsed_seconds": elapsed,
            "model": EN_MODEL_NAME if resolved_language == "en-US" else MODEL_NAME,
            "device": DEVICE,
            "language": resolved_language,
            "profile": cfg,
            "quality": quality,
            "metrics": metrics,
            "corrections": corrections,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"语音识别失败：{exc}") from exc
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


def run_streaming_generate(recognizer, samples, cache, is_final, chunk_size, hotword_str, profile: dict):
    with funasr_runtime_lock:
        return recognizer.generate(
            input=samples,
            cache=cache,
            is_final=is_final,
            chunk_size=chunk_size,
            encoder_chunk_look_back=int(profile.get("encoder_chunk_look_back", 4)),
            decoder_chunk_look_back=int(profile.get("decoder_chunk_look_back", 1)),
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
        cfg = resolve_asr_profile(config.get("profile"))
        chunk_size = config.get("chunk_size") or cfg["stream_chunk_size"]
        await websocket.send_json({"type": "status", "status": "loading", "detail": "正在加载流式识别模型", "profile": cfg})
        recognizer = await asyncio.to_thread(get_streaming_model)
        await websocket.send_json({"type": "ready", "profile": cfg})

        cache = {}
        hotword_str = load_hotwords(cfg, department) if department == "infectious_disease" else ""
        full_text = ""
        pause_open = False
        stream_started = time.perf_counter()
        audio_seconds = 0.0
        generate_calls = 0

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if "text" in message:
                try:
                    cmd = json.loads(message["text"])
                except (json.JSONDecodeError, TypeError):
                    cmd = {}
                if cmd.get("type") == "pause":
                    duration_ms = max(0, min(int(cmd.get("duration_ms", 0)), 10000))
                    if not pause_open:
                        result = await asyncio.to_thread(run_streaming_generate, recognizer, np.zeros(1600, dtype=np.float32), cache, True, chunk_size, hotword_str, cfg)
                        generate_calls += 1
                        if result and result[0].get("text"):
                            full_text += result[0]["text"]
                        cache = {}
                        pause_open = True
                    punctuated = apply_pause_punctuation(full_text, duration_ms)
                    if punctuated != full_text:
                        full_text = punctuated
                        partial = meaningful_stream_text(full_text)
                        partial, _ = apply_correction_rules(partial, language)
                        await websocket.send_json({"type": "partial", "text": partial, "pause_punctuation": True})
                    continue
                if cmd.get("type") == "end":
                    result = await asyncio.to_thread(run_streaming_generate, recognizer, np.zeros(1600, dtype=np.float32), cache, True, chunk_size, hotword_str, cfg)
                    generate_calls += 1
                    if result and result[0].get("text"):
                        full_text += result[0]["text"]
                    raw_text = meaningful_stream_text(full_text)
                    final_text, corrections = apply_correction_rules(raw_text, language)
                    final_text = ensure_terminal_punctuation(final_text)
                    elapsed = time.perf_counter() - stream_started
                    metrics = {"elapsed_seconds": elapsed, "audio_seconds": round(audio_seconds, 3), "realtime_factor": round(elapsed / audio_seconds, 3) if audio_seconds > 0 else None, "profile": cfg["id"], "hotword_count": len(hotword_str.split()) if hotword_str else 0, "generate_calls": generate_calls, "correction_count": sum(item["count"] for item in corrections)}
                    record_asr_metric("streaming", audio_seconds, elapsed, metrics)
                    await websocket.send_json({"type": "final", "text": final_text, "raw_text": raw_text, "metrics": metrics, "corrections": corrections})
                    break
                continue

            if "bytes" in message:
                audio_bytes = message["bytes"]
                if not audio_bytes:
                    continue
                samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                audio_seconds += len(samples) / 16000.0
                if np.sqrt(np.mean(samples * samples)) >= STREAMING_RMS_THRESHOLD:
                    pause_open = False
                if len(samples) < 160:
                    continue
                result = await asyncio.to_thread(run_streaming_generate, recognizer, samples, cache, False, chunk_size, hotword_str, cfg)
                generate_calls += 1
                if result and result[0].get("text"):
                    full_text += result[0]["text"]
                    partial = meaningful_stream_text(full_text)
                    partial, _ = apply_correction_rules(partial, language)
                    if partial:
                        await websocket.send_json({"type": "partial", "text": partial})

        if websocket.client_state.name == "CONNECTED":
            await websocket.close()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

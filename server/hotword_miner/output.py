"""Pack files for the service, and the checks a word must pass before it is written."""
import datetime
import json
import re
from pathlib import Path

from . import privacy

PACK_ADVICE = 300       # about what the "balanced" recognition profile can hold in total, all packs together


def problem_with(word: str) -> str | None:
    """Checked again when the pack is written, whatever the page sent."""
    if not (2 <= len(word) <= 30):
        return "长度应为 2～30 个字符"
    if not re.fullmatch(r"[一-鿿A-Za-z0-9\-+]+", word):
        return "含有汉字、字母、数字、- + 以外的字符"
    if any(p.search(word) for p in privacy.PRIVATE_PATTERNS) or re.search(r"\d{4,}", word):
        return "像证件号、电话或编号"
    return None


def write_pack(out_dir: Path, pack_id: str, specialty: str, label: str, words: list[str], cases: int,
               min_cases: int, reviewers: list[str]) -> tuple[Path, Path]:
    label = " ".join(label.split())          # one line: it goes into a comment line of the word file
    out_dir.mkdir(parents=True, exist_ok=True)
    txt, manifest = out_dir / f"{pack_id}.txt", out_dir / f"{pack_id}.manifest.json"
    today = datetime.date.today().isoformat()
    status = "reviewed" if reviewers else "draft"
    txt.write_text(
        f"# {label}（本院病历词频统计，{'已由医生审核' if reviewers else '草稿，待医生审核；审核前不得默认启用'}）\n"
        "# 每行一个词。词表中不含病例内容；每个词都在多份不同病例中出现过。\n\n" + "\n".join(words) + "\n", encoding="utf-8")
    manifest.write_text(json.dumps({
        "id": pack_id,
        "specialty": specialty,
        "label": label,
        "label_en": label,
        "filename": txt.name,
        "language": "zh-CN",
        "version": f"0.1.0-{status}",
        "status": status,
        "compiled_on": today,
        "compile_method": "由 hotword_miner 对本院病历做词频统计得出，医生在候选表中逐条勾选；词表中不含病例内容。",
        "sources": [{
            "tier": "H",
            "name": "本院病历词频统计（只保留在不少于 %d 份不同病例中出现的词）" % min_cases,
            "url": None,
            "used_for": "候选词来源；扫描了 %d 份病例" % cases,
            "page_read": True,
        }],
        "reviewers": reviewers,
        "reviewed_on": today if reviewers else None,
        "review_notes": "",
        "known_gaps": ["词频高不等于识别会听错；是否值得作为热词需结合识别错误判断。"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return txt, manifest

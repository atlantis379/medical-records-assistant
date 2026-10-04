"""Evaluation manifest: one JSON object per line (JSONL).

Fields
  id          unique id (also the audio file stem when `audio` is omitted)
  audio       path to a 16 kHz mono WAV, relative to the manifest (optional if `hypothesis` is given)
  specialty   infectious_disease | respiratory_critical_care | orthopedics | ...
  speaker     pseudonym such as doctor_a (never a real name)
  source      doctor_recording | synthetic_tts | transcript_only | public_corpus
  spoken      verbatim transcript of what was said (numerals as spoken)        [optional]
  reference   the text the record should contain after normalisation            [optional]
  hypothesis  raw recognizer text, for evaluating the text pipeline without audio [optional]
  entities    extra [{"text": "...", "type": "drug"}] that must appear in the final text
  must_not    strings that must NOT appear (e.g. a look-alike drug)
  tags        free labels (numbers, negation, bp, vent, rom ...)
"""
import json
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path

SOURCES = {"doctor_recording", "synthetic_tts", "transcript_only", "public_corpus"}
# Only doctor_recording / public_corpus data may be used for acceptance decisions.
ACCEPTANCE_SOURCES = {"doctor_recording", "public_corpus"}

_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_PHI_WORDS = re.compile(r"身份证|住院号|病案号|门诊号|医保号|手机号|电话号码|联系电话")


@dataclass
class Sample:
    id: str
    audio: Path | None = None
    specialty: str = ""
    speaker: str = ""
    source: str = "doctor_recording"
    spoken: str | None = None
    reference: str | None = None
    hypothesis: str | None = None
    entities: list[dict] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Issue:
    level: str   # "error" | "warning"
    sample_id: str
    message: str


def load_manifest(path: Path) -> list[Sample]:
    path = Path(path)
    samples: list[Sample] = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} line {number}: invalid JSON ({exc})") from exc
        if not isinstance(raw, dict) or not raw.get("id"):
            raise ValueError(f"{path.name} line {number}: every sample needs an 'id'")
        audio = raw.get("audio")
        samples.append(Sample(
            id=str(raw["id"]),
            audio=(path.parent / audio) if audio else None,
            specialty=str(raw.get("specialty", "")),
            speaker=str(raw.get("speaker", "")),
            source=str(raw.get("source", "doctor_recording")),
            spoken=raw.get("spoken"),
            reference=raw.get("reference"),
            hypothesis=raw.get("hypothesis"),
            entities=list(raw.get("entities", [])),
            must_not=list(raw.get("must_not", [])),
            tags=list(raw.get("tags", [])),
            notes=str(raw.get("notes", "")),
        ))
    return samples


def audio_info(path: Path) -> dict:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        return {"seconds": wf.getnframes() / rate if rate else 0.0, "rate": rate,
                "channels": wf.getnchannels(), "width": wf.getsampwidth()}


def privacy_findings(text: str) -> list[str]:
    findings = []
    if _PHONE.search(text or ""):
        findings.append("looks like a phone number")
    if _ID_CARD.search(text or ""):
        findings.append("looks like an ID card number")
    if _PHI_WORDS.search(text or ""):
        findings.append("mentions an identifier field (身份证/住院号/病案号 ...)")
    return findings


def validate(samples: list[Sample], *, need_audio: bool = True) -> list[Issue]:
    issues: list[Issue] = []
    seen: set[str] = set()
    for sample in samples:
        if sample.id in seen:
            issues.append(Issue("error", sample.id, "duplicate id"))
        seen.add(sample.id)
        if sample.source not in SOURCES:
            issues.append(Issue("error", sample.id, f"unknown source {sample.source!r} (use one of {sorted(SOURCES)})"))
        if sample.hypothesis is None and need_audio:
            if sample.audio is None:
                issues.append(Issue("error", sample.id, "no audio and no hypothesis"))
            elif not sample.audio.exists():
                issues.append(Issue("error", sample.id, f"audio file not found: {sample.audio}"))
            else:
                try:
                    info = audio_info(sample.audio)
                except (wave.Error, EOFError) as exc:
                    issues.append(Issue("error", sample.id, f"not a readable WAV file ({exc})"))
                else:
                    if info["seconds"] < 0.5:
                        issues.append(Issue("error", sample.id, f"audio is only {info['seconds']:.2f}s long"))
                    if info["rate"] != 16000 or info["channels"] != 1:
                        issues.append(Issue("warning", sample.id, f"audio is {info['rate']} Hz / {info['channels']} ch; 16 kHz mono is expected"))
        if sample.reference is None and sample.spoken is None:
            issues.append(Issue("warning", sample.id, "no reference or spoken text: only digit-style statistics will be produced"))
        if sample.source == "transcript_only" and sample.hypothesis is None:
            issues.append(Issue("error", sample.id, "source is transcript_only but there is no hypothesis"))
        for name in ("spoken", "reference", "hypothesis", "notes"):
            for finding in privacy_findings(getattr(sample, name) or ""):
                issues.append(Issue("error", sample.id, f"{name} {finding}: remove personal information"))
        if sample.speaker and re.search(r"[一-鿿]{2,4}医生|^[一-鿿]{2,4}$", sample.speaker):
            issues.append(Issue("warning", sample.id, "speaker looks like a real name; use a pseudonym such as doctor_a"))
    return issues

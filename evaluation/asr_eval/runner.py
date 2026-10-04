"""Runs configurations over a dataset and scores the results.

A configuration chooses the recognizer, the hotword mode and whether the production text
post-processing is applied, so the same audio can answer questions such as
"do specialty hotwords help?" or "how much does number normalisation fix?".
"""
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .backends import BACKENDS, Backend, BackendUnavailable, RawResult
from .dataset import ACCEPTANCE_SOURCES, Sample
from .metrics import Lexicon, SampleScore, aggregate, collapse_spaces, score_sample

HOTWORD_MODES = {"off", "common", "specialty"}


@dataclass
class Config:
    name: str
    backend: str = "paraformer"
    hotwords: str = "off"              # off | common (shared packs only) | specialty
    specialties: object = "sample"     # "sample" = each sample's own specialty, or an explicit list
    include_draft: bool = False
    profile: str = "balanced"
    postprocess: bool = True           # False: judge the recognizer text as it comes out


def load_configs(path: Path) -> list[Config]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    items = data["configs"] if isinstance(data, dict) else data
    configs = []
    for item in items:
        config = Config(**item)
        if config.backend not in BACKENDS:
            raise ValueError(f"config {config.name!r}: unknown backend {config.backend!r} (use {sorted(BACKENDS)})")
        if config.hotwords not in HOTWORD_MODES:
            raise ValueError(f"config {config.name!r}: hotwords must be one of {sorted(HOTWORD_MODES)}")
        configs.append(config)
    if len({c.name for c in configs}) != len(configs):
        raise ValueError("config names must be unique")
    return configs


def specialties_for(config: Config, sample: Sample) -> list[str]:
    if config.hotwords == "common":
        return []
    if config.specialties == "sample":
        return [sample.specialty] if sample.specialty else []
    return list(config.specialties)


def hotword_string(srv, config: Config, sample: Sample) -> str:
    if config.hotwords == "off":
        return ""
    return srv.load_hotwords(srv.resolve_asr_profile(config.profile), specialties=specialties_for(config, sample),
                             include_draft=config.include_draft)


@dataclass
class ConfigResult:
    config: Config
    skipped: str | None = None
    scores: list[SampleScore] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    groups: dict = field(default_factory=dict)
    rtf: float | None = None
    acceptance_valid: bool = False


def _group(rows_scores: list[tuple[dict, SampleScore]], key) -> dict:
    buckets: dict[str, list[SampleScore]] = defaultdict(list)
    for row, score in rows_scores:
        for name in key(row):
            buckets[name].append(score)
    return {name: aggregate(scores) for name, scores in sorted(buckets.items())}


def raw_key(config_name: str, sample_id: str) -> str:
    return f"{config_name}\t{sample_id}"


def load_raw_cache(path: Path) -> dict[str, RawResult]:
    cache: dict[str, RawResult] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            cache[raw_key(item["config"], item["sample_id"])] = RawResult(
                item["raw_text"], item["audio_seconds"], item["infer_seconds"], item.get("hotword_count", 0))
    return cache


def run(samples: list[Sample], configs: list[Config], *, raw_out: Path | None = None,
        raw_cache: dict[str, RawResult] | None = None, lexicon: Lexicon | None = None,
        warmup: bool = True, progress=lambda message: None) -> dict[str, ConfigResult]:
    from server import app as srv   # the same post-processing the service applies

    lexicon = lexicon or Lexicon.load()
    results: dict[str, ConfigResult] = {}
    backends: dict[str, Backend | BackendUnavailable] = {}
    raw_file = raw_out.open("w", encoding="utf-8") if raw_out else None
    try:
        for config in configs:
            result = ConfigResult(config)
            results[config.name] = result
            needs_backend = not (raw_cache and all(raw_key(config.name, s.id) in raw_cache for s in samples))
            backend = None
            if needs_backend:
                if config.backend not in backends:
                    instance = BACKENDS[config.backend]()
                    try:
                        progress(f"[{config.name}] loading {config.backend} ...")
                        instance.prepare()
                        if warmup and samples:
                            instance.warmup(samples[0], srv.resolve_asr_profile(config.profile))
                        backends[config.backend] = instance
                    except BackendUnavailable as exc:
                        backends[config.backend] = exc
                backend = backends[config.backend]
                if isinstance(backend, BackendUnavailable):
                    result.skipped = str(backend)
                    progress(f"[{config.name}] skipped: {result.skipped}")
                    continue

            paired: list[tuple[dict, SampleScore]] = []
            infer = audio = 0.0
            for index, sample in enumerate(samples, 1):
                progress(f"[{config.name}] {index}/{len(samples)} {sample.id}")
                specialties = specialties_for(config, sample)
                key = raw_key(config.name, sample.id)
                try:
                    if raw_cache and key in raw_cache:
                        raw = raw_cache[key]
                    else:
                        hotword = hotword_string(srv, config, sample)
                        raw = backend.transcribe(sample, hotword, srv.resolve_asr_profile(config.profile))
                        if raw_file:
                            raw_file.write(json.dumps({"config": config.name, "sample_id": sample.id, "raw_text": raw.text,
                                                       "audio_seconds": raw.audio_seconds, "infer_seconds": raw.infer_seconds,
                                                       "hotword_count": raw.hotword_count}, ensure_ascii=False) + "\n")
                            raw_file.flush()
                except BackendUnavailable as exc:
                    result.errors.append(f"{sample.id}: {exc}")
                    continue
                except Exception as exc:  # one bad file must not end a long run
                    result.errors.append(f"{sample.id}: {type(exc).__name__}: {exc}")
                    continue

                if config.postprocess:
                    final, _, _, notices = srv.postprocess_transcript(raw.text, "zh-CN", specialties)
                else:
                    final, notices = raw.text, []
                score = score_sample(sample.id, reference=sample.reference, spoken=sample.spoken, raw_text=raw.text,
                                     final_text=final, notices=len(notices), extra_entities=sample.entities,
                                     must_not=sample.must_not, lexicon=lexicon)
                row = {
                    "config": config.name, "id": sample.id, "specialty": sample.specialty, "source": sample.source,
                    "speaker": sample.speaker, "tags": sample.tags, "spoken": sample.spoken, "reference": sample.reference,
                    "raw": collapse_spaces(raw.text), "final": final,
                    "cer_content": score.cer_content.rate if score.cer_content else None,
                    "cer_strict": score.cer_strict.rate if score.cer_strict else None,
                    "asr_cer": score.asr_cer.rate if score.asr_cer else None,
                    "missed": [f"{e.type}:{e.text}" for e in score.entities if not e.exact],
                    "format_only": [e.text for e in score.entities if e.type == "quantity" and not e.exact and e.value_ok],
                    "flipped": [e.text for e in score.entities if e.flipped],
                    "must_not_hits": score.must_not_hits,
                    "notices": [n["source"] for n in notices],
                    "audio_seconds": raw.audio_seconds, "infer_seconds": raw.infer_seconds,
                }
                audio += raw.audio_seconds
                infer += raw.infer_seconds
                result.scores.append(score)
                result.rows.append(row)
                paired.append((row, score))

            result.summary = aggregate(result.scores)
            result.groups = {
                "specialty": _group(paired, lambda r: [r["specialty"] or "(none)"]),
                "source": _group(paired, lambda r: [r["source"]]),
                "tag": _group(paired, lambda r: r["tags"] or ["(none)"]),
            }
            result.rtf = (infer / audio) if audio > 0 and infer > 0 else None
            result.acceptance_valid = bool(result.rows) and all(r["source"] in ACCEPTANCE_SOURCES for r in result.rows)
    finally:
        if raw_file:
            raw_file.close()
    return results


def fingerprint(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]

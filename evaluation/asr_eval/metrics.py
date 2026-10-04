"""Metrics for dictated clinical text.

Everything here is a pure function of text, so it is unit-tested without any model.

Two reference texts can accompany a sample:
* `spoken`    - what the clinician actually said, with numerals as spoken (一百三十);
                used to judge the recognizer alone;
* `reference` - the text the record should contain (130mmHg);
                used to judge the whole pipeline (recognizer + normalisation).

Entities are extracted from `reference` automatically, so annotation work stays small:
quantities (number + unit), negations, medical terms from the hotword packs, and field
labels (主诉, 现病史 ...). A sample may add its own `entities` and `must_not` strings.
"""
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from server.clinical.numbers import CN_NUMBER_PATTERN, parse_cn_number

DATA_DIR = Path(__file__).resolve().parents[2] / "server" / "data"
PACK_DIR = DATA_DIR / "hotword_packs"
NORMALIZATION_DIR = DATA_DIR / "normalization"

FIELD_LABELS = ["主诉", "现病史", "既往史", "个人史", "婚育史", "家族史", "流行病学史", "体格检查", "查体",
                "专科检查", "辅助检查", "初步诊断", "诊断", "诊疗经过", "诊疗计划", "处理意见"]
NEGATION_MARKERS = ["否认", "未闻及", "未触及", "未见", "不伴", "没有", "无"]

_CJK = "　-〿一-鿿＀-￯"
_CJK_SPACE = re.compile(rf"(?<=[{_CJK}])\s+|\s+(?=[{_CJK}])")
_STRIP = re.compile(r"[\s，。,;；:：!！?？、…\"'“”‘’（）()\[\]【】]+")


def collapse_spaces(text: str) -> str:
    """Paraformer puts a space between every Chinese character; the service removes spaces next to
    Chinese text before anything else, and so must the evaluation (same rule as server/app.py)."""
    return _CJK_SPACE.sub("", (text or "").strip())


# ---------------------------------------------------------------------------- edit distance
def edit_operations(reference: str, hypothesis: str) -> tuple[int, int, int, int]:
    """Levenshtein distance with a breakdown: (edits, substitutions, deletions, insertions)."""
    n, m = len(reference), len(hypothesis)
    if n == 0:
        return m, 0, 0, m
    if m == 0:
        return n, 0, n, 0
    # dp[i][j] = (cost, subs, dels, inss)
    prev = [(j, 0, 0, j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [(i, 0, i, 0)]
        for j in range(1, m + 1):
            if reference[i - 1] == hypothesis[j - 1]:
                best = prev[j - 1]
            else:
                sub, dele, ins = prev[j - 1], prev[j], cur[j - 1]
                best = min(
                    (sub[0] + 1, sub[1] + 1, sub[2], sub[3]),
                    (dele[0] + 1, dele[1], dele[2] + 1, dele[3]),
                    (ins[0] + 1, ins[1], ins[2], ins[3] + 1),
                )
            cur.append(best)
        prev = cur
    return prev[m]


def strip_for_cer(text: str) -> str:
    """Remove whitespace and sentence punctuation; keep symbols that carry meaning (/ . - % ℃ °)."""
    return _STRIP.sub("", text or "").casefold()


# ---------------------------------------------------------------------------- canonical forms
def _unit_pairs() -> list[tuple[str, str]]:
    data = json.loads((NORMALIZATION_DIR / "units.json").read_text(encoding="utf-8-sig"))
    pairs = []
    for unit in data["units"]:
        for spoken in unit["spoken"]:
            if re.search(r"[一-鿿]", spoken):
                pairs.append((spoken, unit["symbol"]))
    return sorted(pairs, key=lambda item: len(item[0]), reverse=True)


_UNIT_PAIRS: list[tuple[str, str]] | None = None


def canonicalize(text: str) -> str:
    """A form in which spoken and written numbers/units compare equal.

    Chinese numerals become digits, 百分之N becomes N%, spoken units become symbols and
    all whitespace goes. It is used for the *content* error rate, so formatting
    differences (一百三十 vs 130, 毫克 vs mg) do not count as recognition errors;
    formatting is judged separately by the quantity metrics.
    """
    global _UNIT_PAIRS
    if _UNIT_PAIRS is None:
        _UNIT_PAIRS = _unit_pairs()
    value = collapse_spaces(text)
    value = re.sub(rf"百分之({CN_NUMBER_PATTERN}|\d+(?:\.\d+)?)", lambda m: f"{parse_cn_number(m.group(1)) or m.group(1)}%", value)
    value = re.sub(CN_NUMBER_PATTERN, lambda m: parse_cn_number(m.group(0)) or m.group(0), value)
    for spoken, symbol in _UNIT_PAIRS:
        value = value.replace(spoken, symbol)
    value = value.replace("摄氏度", "℃").replace("毫米汞柱", "mmHg")
    return strip_for_cer(value)


@dataclass
class CerResult:
    edits: int
    reference_length: int
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0

    @property
    def rate(self) -> float:
        return self.edits / self.reference_length if self.reference_length else 0.0


def cer(reference: str, hypothesis: str, *, canonical: bool) -> CerResult:
    prepare = canonicalize if canonical else strip_for_cer
    ref, hyp = prepare(reference), prepare(hypothesis)
    edits, subs, dels, inss = edit_operations(ref, hyp)
    return CerResult(edits, len(ref), subs, dels, inss)


# ---------------------------------------------------------------------------- entity extraction
def _quantity_regex() -> re.Pattern:
    data = json.loads((NORMALIZATION_DIR / "units.json").read_text(encoding="utf-8-sig"))
    symbols = {unit["symbol"] for unit in data["units"]} | {"%", "°"}
    alternatives = "|".join(re.escape(s) for s in sorted(symbols, key=len, reverse=True))
    number = r"\d+(?:\.\d+)?"
    # the look-behind must not treat Chinese characters as word characters (血压130/80mmHg)
    return re.compile(rf"(?<![A-Za-z0-9.])-?{number}(?:[-/×]{number}){{0,3}}\s?(?:{alternatives})")


_QUANTITY_RE: re.Pattern | None = None


def extract_quantities(text: str) -> list[str]:
    global _QUANTITY_RE
    if _QUANTITY_RE is None:
        _QUANTITY_RE = _quantity_regex()
    return [m.group(0) for m in _QUANTITY_RE.finditer(text or "")]


_NEGATION_RE = re.compile(rf"(?:{'|'.join(NEGATION_MARKERS)})(?P<rest>[一-鿿]{{1,4}})")
_POLARITY_RE = re.compile(r"(?P<head>[一-鿿]{1,4})(?P<polarity>阴性|阳性)")


def extract_negations(text: str) -> list[dict]:
    """Negated statements and test polarities, e.g. 无发热, 否认高血压, 浮髌试验阴性."""
    found = []
    for match in _NEGATION_RE.finditer(text or ""):
        marker = match.group(0)[: len(match.group(0)) - len(match.group("rest"))]
        found.append({"phrase": match.group(0), "marker": marker, "rest": match.group("rest")})
    for match in _POLARITY_RE.finditer(text or ""):
        found.append({"phrase": match.group(0), "marker": match.group("polarity"), "rest": match.group("head"),
                      "polarity": True})
    return found


def polarity_flipped(entity: dict, hypothesis: str) -> bool:
    """True when the hypothesis says the opposite: 无发热 -> 有发热, 阴性 -> 阳性."""
    hyp = strip_for_cer(hypothesis)
    if entity.get("polarity"):
        opposite = "阳性" if entity["marker"] == "阴性" else "阴性"
        return entity["rest"] + opposite in hyp and entity["phrase"] not in hyp
    return "有" + entity["rest"] in hyp and strip_for_cer(entity["phrase"]) not in hyp


def extract_field_labels(text: str) -> list[str]:
    """Field names that appear as headings (followed by a colon, or at the start of the text)."""
    labels = []
    for label in sorted(FIELD_LABELS, key=len, reverse=True):
        if re.search(rf"(?:^|[\n。；;])\s*{label}[：:]", text or "") or (text or "").startswith(label):
            if not any(label in longer for longer in labels):
                labels.append(label)
    return labels


@dataclass
class Lexicon:
    """Medical terms from the hotword packs with a coarse category (drug / pathogen / term)."""
    words: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, pack_dir: Path = PACK_DIR) -> "Lexicon":
        words: dict[str, str] = {}
        for path in sorted(pack_dir.glob("*.txt")):
            if path.stem == "user_custom":
                continue
            category = {"antimicrobials": "drug", "pathogens": "pathogen"}.get(path.stem, "term")
            section = category
            for raw in path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    if "药物" in line:
                        section = "drug"
                    elif "----" in line:
                        section = category
                    continue
                if len(line) >= 2 and not re.fullmatch(r"[\d.\-/%×^A-Za-z]+", line) and line not in words:
                    words[line] = section if section != "term" else category
        return cls(words)

    def terms_in(self, text: str) -> list[tuple[str, str]]:
        found = [(word, cat) for word, cat in self.words.items() if word in (text or "")]
        longest_first = sorted(found, key=lambda item: len(item[0]), reverse=True)
        kept: list[tuple[str, str]] = []
        for word, cat in longest_first:
            if not any(word in other for other, _ in kept):
                kept.append((word, cat))
        return kept


# ---------------------------------------------------------------------------- per-sample scoring
@dataclass
class EntityResult:
    type: str
    text: str
    exact: bool
    value_ok: bool = False
    flipped: bool = False


@dataclass
class SampleScore:
    sample_id: str
    cer_content: CerResult | None = None
    cer_strict: CerResult | None = None
    asr_cer: CerResult | None = None
    entities: list[EntityResult] = field(default_factory=list)
    must_not_hits: list[str] = field(default_factory=list)
    notices: int = 0
    digit_style: dict = field(default_factory=dict)


def digit_style(raw_text: str) -> dict:
    """How the recognizer wrote numbers and units: needed to decide where normalisation matters."""
    text = collapse_spaces(raw_text)
    chinese = [m.group(0) for m in re.finditer(CN_NUMBER_PATTERN, text) if parse_cn_number(m.group(0)) is not None
               and not re.fullmatch(r"[一二三四五六七八九十]", m.group(0))]
    arabic = re.findall(r"\d+(?:\.\d+)?", text)
    unit_symbols = len(re.findall(r"mmHg|mg|μg|mL|ml|kg|cm|mm|℃|%|次/分|mmol/L|g/L|L/min|cmH2O", text))
    spoken_units = len(re.findall(r"毫米汞柱|毫克|微克|毫升|千克|公斤|厘米|毫米|摄氏度|百分之|每分钟|毫摩尔|微摩尔|厘米水柱", text))
    return {"arabic_numbers": len(arabic), "chinese_numbers": len(chinese),
            "unit_symbols": unit_symbols, "spoken_units": spoken_units}


def score_sample(sample_id: str, *, reference: str | None, spoken: str | None, raw_text: str, final_text: str,
                 notices: int = 0, extra_entities: list[dict] | None = None, must_not: list[str] | None = None,
                 lexicon: Lexicon | None = None) -> SampleScore:
    raw_text = collapse_spaces(raw_text)
    score = SampleScore(sample_id=sample_id, notices=notices, digit_style=digit_style(raw_text))
    if spoken:
        score.asr_cer = cer(spoken, raw_text, canonical=True)
    if reference is None:
        return score
    score.cer_content = cer(reference, final_text, canonical=True)
    score.cer_strict = cer(reference, final_text, canonical=False)

    final_flat = strip_for_cer(final_text)
    for quantity in extract_quantities(reference):
        score.entities.append(EntityResult(
            "quantity", quantity, exact=quantity in final_text,
            value_ok=quantity.replace(" ", "") in final_text.replace(" ", "")))
    for entity in extract_negations(reference):
        score.entities.append(EntityResult(
            "negation", entity["phrase"], exact=strip_for_cer(entity["phrase"]) in final_flat,
            flipped=polarity_flipped(entity, final_text)))
    for label in extract_field_labels(reference):
        score.entities.append(EntityResult("field_command", label, exact=label in raw_text))
    if lexicon is not None:
        for word, category in lexicon.terms_in(reference):
            score.entities.append(EntityResult(category, word, exact=word in final_text))
    for item in extra_entities or []:
        score.entities.append(EntityResult(item.get("type", "custom"), item["text"], exact=item["text"] in final_text))
    score.must_not_hits = [text for text in (must_not or []) if text and text in final_text]
    return score


# ---------------------------------------------------------------------------- aggregation
def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


@dataclass
class Rate:
    successes: int
    total: int

    @property
    def value(self) -> float | None:
        return self.successes / self.total if self.total else None

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.successes, self.total)


def micro_cer(results: list[CerResult]) -> float | None:
    total = sum(r.reference_length for r in results)
    return sum(r.edits for r in results) / total if total else None


def aggregate(scores: list[SampleScore]) -> dict:
    """Summary numbers for one group of samples (micro-averaged: long samples weigh more)."""
    def rate(entity_type: str, attr: str = "exact") -> Rate:
        items = [e for s in scores for e in s.entities if e.type == entity_type]
        return Rate(sum(1 for e in items if getattr(e, attr)), len(items))

    quantities = [e for s in scores for e in s.entities if e.type == "quantity"]
    negations = [e for s in scores for e in s.entities if e.type == "negation"]
    result = {
        "samples": len(scores),
        "cer_content": micro_cer([s.cer_content for s in scores if s.cer_content]),
        "cer_strict": micro_cer([s.cer_strict for s in scores if s.cer_strict]),
        "asr_cer": micro_cer([s.asr_cer for s in scores if s.asr_cer]),
        "quantity_exact": rate("quantity"),
        "quantity_value": rate("quantity", "value_ok"),
        "negation_recall": rate("negation"),
        "negation_flips": sum(1 for e in negations if e.flipped),
        "field_command": rate("field_command"),
        "drug": rate("drug"),
        "pathogen": rate("pathogen"),
        "term": rate("term"),
        "must_not_hits": sum(len(s.must_not_hits) for s in scores),
        "number_notices": sum(s.notices for s in scores),
        "quantity_count": len(quantities),
        "digit_style": {key: sum(s.digit_style.get(key, 0) for s in scores)
                        for key in ("arabic_numbers", "chinese_numbers", "unit_symbols", "spoken_units")},
    }
    return result

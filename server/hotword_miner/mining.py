"""Find candidate hotwords in a corpus of cases.

Words are found statistically (no dictionary needed, so new drug names and local usage show up):
a string is a candidate when it occurs in at least `min_cases` *different* cases, its characters stick
together (cohesion, from pointwise mutual information) and it can be preceded and followed by many different
characters (boundary entropy), which is what separates a word from a fragment such as "实性结".

The level-by-level counting relies on one fact: a string cannot occur in more cases than any part of it.
So a string is only counted when both its (n-1)-character prefix and suffix already reached `min_cases`.
"""
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import privacy

HEADING = re.compile(
    r"^\s*[【\[]?(主诉|现病史|既往史|个人史|婚育史|月经史|家族史|过敏史|体格检查|查体|专科检查|专科情况|辅助检查|检查所见|影像学?表现|检查|诊断依据|鉴别诊断|入院诊断|出院诊断|诊断|诊疗经过|病程记录|病程|处理|治疗|医嘱)[】\]]?\s*[:：]")
UNASSIGNED = "未归类"
EDGE_STOP = set("的了和及与在于把被将对并而或但也都已其该此这那我你他她它之以")


@dataclass
class Settings:
    min_cases: int = 5          # a word must occur in at least this many different cases (privacy threshold)
    min_len: int = 2
    max_len: int = 10
    min_pmi: float = 3.0        # cohesion, natural log
    min_entropy: float = 0.5    # boundary entropy (the smaller of left and right), natural log
    common_freq: int = 1000     # words this common in jieba's dictionary are not worth a hotword
    dominance: float = 0.8      # drop "结节" when "实性结节" accounts for 80 % of its occurrences
    top: int = 2000


@dataclass
class Segment:
    text: str            # one run of Chinese characters
    left: str            # character just before it ("^" at line start), digits shown as "0"
    right: str


@dataclass
class Line:
    heading: str
    segments: list
    latin: list


@dataclass
class Result:
    candidates: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _neighbour(char: str) -> str:
    return "0" if char.isdigit() else char


def prepare_case(text: str, names: set[str]) -> list[Line]:
    heading = UNASSIGNED
    lines = []
    for raw in text.splitlines():
        match = HEADING.match(raw)
        if match:
            heading = match.group(1)
        raw = privacy.mask(raw, names)
        segments = [Segment(m.group(), _neighbour(raw[m.start() - 1]) if m.start() else "^",
                            _neighbour(raw[m.end()]) if m.end() < len(raw) else "$")
                    for m in privacy.CJK_RUN.finditer(raw)]
        latin = [t for t in privacy.LATIN_TOKEN.findall(raw) if privacy.acceptable_latin(t)]
        if segments or latin:
            lines.append(Line(heading, segments, latin))
    return lines


def _count_level(cases, n, allowed):
    total, df = Counter(), Counter()
    for case in cases:
        seen = set()
        for line in case:
            for seg in line.segments:
                text = seg.text
                for i in range(len(text) - n + 1):
                    gram = text[i:i + n]
                    if allowed is not None and (gram[:-1] not in allowed or gram[1:] not in allowed):
                        continue
                    total[gram] += 1
                    seen.add(gram)
        df.update(seen)
    return total, df


def _entropy(counter: Counter) -> float:
    size = sum(counter.values())
    return -sum(c / size * math.log(c / size) for c in counter.values()) if size else 0.0


def mine(cases: list[list[Line]], settings: Settings, known: set[str], names: set[str], tagger=None, jieba_freq=None) -> Result:
    result = Result()
    drops: Counter = Counter()
    positions: dict[int, int] = defaultdict(int)
    for case in cases:
        for line in case:
            for seg in line.segments:
                for n in range(1, settings.max_len + 1):
                    positions[n] += max(0, len(seg.text) - n + 1)

    # --- level by level, only strings whose prefix and suffix are frequent enough
    counts: dict[int, Counter] = {}
    passing: dict[int, dict[str, int]] = {}
    total, df = _count_level(cases, 1, None)
    counts[1] = total
    allowed = {g for g, d in df.items() if d >= settings.min_cases}
    passing[1] = {g: df[g] for g in allowed}
    for n in range(2, settings.max_len + 1):
        if not allowed:
            break
        total, df = _count_level(cases, n, allowed)
        allowed = {g for g, d in df.items() if d >= settings.min_cases}
        counts[n] = Counter({g: total[g] for g in allowed})
        passing[n] = {g: df[g] for g in allowed}

    # --- cohesion
    finalists: dict[str, float] = {}
    for n in range(settings.min_len, settings.max_len + 1):
        for gram, cases_n in passing.get(n, {}).items():
            if gram[0] in EDGE_STOP or gram[-1] in EDGE_STOP or len(set(gram)) == 1:
                drops["开头或结尾是虚词/重复字"] += 1
                continue
            p_whole = counts[n][gram] / positions[n]
            pmi = min(math.log(p_whole / ((counts[k][gram[:k]] / positions[k]) * (counts[n - k][gram[k:]] / positions[n - k])))
                      for k in range(1, n))
            if pmi < settings.min_pmi:
                drops["字与字结合不紧"] += 1
                continue
            finalists[gram] = pmi

    # --- boundary entropy and the heading each word mostly appears under (one more pass, finalists only)
    left, right = defaultdict(Counter), defaultdict(Counter)
    where = defaultdict(Counter)
    lengths = sorted({len(g) for g in finalists})
    for case in cases:
        for line in case:
            for seg in line.segments:
                text = seg.text
                for n in lengths:
                    for i in range(len(text) - n + 1):
                        gram = text[i:i + n]
                        if gram in finalists:
                            left[gram][text[i - 1] if i else seg.left] += 1
                            right[gram][text[i + n] if i + n < len(text) else seg.right] += 1
                            where[gram][line.heading] += 1
    entropy_ok = {}
    for gram in finalists:
        value = min(_entropy(left[gram]), _entropy(right[gram]))
        if value < settings.min_entropy:
            drops["边界不稳定（像词的片段）"] += 1
            continue
        entropy_ok[gram] = value

    # --- a part of a longer word that accounts for nearly all of its own occurrences is not a word of its own
    dominated = set()
    for longer in entropy_ok:
        for size in range(settings.min_len, len(longer)):
            for i in range(len(longer) - size + 1):
                part = longer[i:i + size]
                if part in entropy_ok and counts[len(longer)][longer] >= settings.dominance * counts[size][part]:
                    dominated.add(part)
    drops["是更长词的一部分"] += len(dominated)

    # a word plus one or two stray neighbouring characters ("示肺栓塞", "提示奥马珠单抗") is a collocation, not a word:
    # the shorter word was already accepted and the longer string covers only some of its occurrences
    survivors = set(entropy_ok) - dominated
    collocations = set()
    for gram in survivors:
        # one extra character always counts; two only when the rest is long enough to be a word of its own
        # (so 呼吸衰竭 is not judged as "呼吸" + two characters)
        for part in (gram[1:], gram[:-1]) + ((gram[2:], gram[:-2]) if len(gram) >= 6 else ()):
            if len(part) >= settings.min_len and part in survivors and counts[len(gram)][gram] < settings.dominance * counts[len(part)][part]:
                collocations.add(gram)
    drops["词加一个多余的字（搭配，不是词）"] += len(collocations)
    dominated |= collocations

    rows = []
    for gram, entropy in entropy_ok.items():
        if gram in dominated:
            continue
        reason = _reject(gram, known, names, tagger, jieba_freq, settings)
        if reason:
            drops[reason] += 1
            continue
        rows.append({"word": gram, "cases": passing[len(gram)][gram], "count": counts[len(gram)][gram],
                     "pmi": round(finalists[gram], 2), "entropy": round(entropy, 2),
                     "field": where[gram].most_common(1)[0][0], "kind": "汉字词",
                     "general": (jieba_freq or {}).get(gram, 0)})

    # --- Latin abbreviations (CT, COPD, PaO2 ...): counted by case, no cohesion test needed
    latin_cases, latin_total, forms = Counter(), Counter(), defaultdict(Counter)
    latin_where = defaultdict(Counter)
    for case in cases:
        seen = set()
        for line in case:
            for token in line.latin:
                key = token.lower()
                latin_total[key] += 1
                forms[key][token] += 1
                latin_where[key][line.heading] += 1
                seen.add(key)
        latin_cases.update(seen)
    for key, number in latin_cases.items():
        if number < settings.min_cases:
            continue
        word = forms[key].most_common(1)[0][0]
        if key in known:
            drops["词库里已有"] += 1
            continue
        rows.append({"word": word, "cases": number, "count": latin_total[key], "pmi": "", "entropy": "",
                     "field": latin_where[key].most_common(1)[0][0], "kind": "字母缩写", "general": ""})

    rows.sort(key=lambda r: (-r["cases"], -r["count"], r["word"]))
    result.candidates = rows[:settings.top]
    result.stats = {"cases": len(cases), "kept": len(result.candidates), "dropped_for_top": max(0, len(rows) - settings.top),
                    "drops": dict(drops)}
    return result


def _reject(word, known, names, tagger, jieba_freq, settings):
    if word.lower() in known:
        return "词库里已有"
    if any(name in word for name in names):
        return "含有从病例中识别出的姓名"
    if jieba_freq is not None and jieba_freq.get(word, 0) >= settings.common_freq:
        return "日常用语（识别本来就认得）"
    if tagger is not None:
        reason = privacy.looks_like_name(word, tagger)
        if reason:
            return reason
    return None

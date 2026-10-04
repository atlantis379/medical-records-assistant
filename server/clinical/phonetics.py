"""Sound-alike correction against a list of known terms (drug names, hotwords).

The recogniser often hears the right sounds and writes the wrong characters ("头包曲松" for 头孢曲松). A hotword list is
limited to a few hundred words, but this check can use every drug name of the national catalogue.

Two kinds of finding, and only one of them changes the text:

* **same pinyin** (ignoring tones) as exactly one known term of at least four characters, and the written form is not
  itself a known word: replaced (reported in the corrections list);
* everything else that sounds alike (shorter terms, similar pinyin with zh/z, ch/c, sh/s, n/l, ang/an, eng/en,
  ing/in treated as equal, or the same pinyin as several terms): only *suggested*; the doctor decides.

Nothing here knows what a sentence means, so it is deliberately cautious: terms must be written with Chinese
characters only, a window that is already a known term or a common word is left alone, and nothing is replaced
when two different terms fit.
"""
import re
from dataclasses import dataclass, field

try:                                    # pypinyin is a small pure-Python package (server/requirements.txt)
    from pypinyin import Style, lazy_pinyin
except ImportError:                     # without it the layer is simply off
    lazy_pinyin = None

CJK_RUN = re.compile(r"[一-鿿]{2,}")
CJK_WORD = re.compile(r"[一-鿿]{2,12}")
AUTO_MIN_LENGTH = 4                     # shorter windows are only ever suggested: two or three characters match ordinary text too easily
SUGGEST_MIN_LENGTH = 3
COMMON_FREQ = 500                       # jieba dictionary frequency from which a window counts as an ordinary word


def available() -> bool:
    return lazy_pinyin is not None


def syllables(text: str) -> list[str]:
    return lazy_pinyin(text, style=Style.NORMAL, errors=lambda chars: list(chars))


def blur(syllable: str) -> str:
    """The sound-alike class of one syllable (southern accents merge these)."""
    for long, short in (("zh", "z"), ("ch", "c"), ("sh", "s")):
        if syllable.startswith(long):
            syllable = short + syllable[len(long):]
            break
    if syllable.startswith("l"):
        syllable = "n" + syllable[1:]
    for long, short in (("ang", "an"), ("eng", "en"), ("ing", "in")):
        if syllable.endswith(long):
            syllable = syllable[: -len(long)] + short
            break
    return syllable


def _common(window: str) -> bool:
    try:
        import jieba
        jieba.setLogLevel(60)
        if not jieba.dt.initialized:
            jieba.initialize()
        return jieba.dt.FREQ.get(window, 0) >= COMMON_FREQ
    except ImportError:
        return False


@dataclass
class Index:
    words: set = field(default_factory=set)
    exact: dict = field(default_factory=dict)       # syllables -> {word}
    similar: dict = field(default_factory=dict)     # blurred syllables -> {word}
    lengths: list = field(default_factory=list)

    @classmethod
    def build(cls, words) -> "Index":
        index = cls()
        for word in words:
            if not CJK_WORD.fullmatch(word):
                continue
            index.words.add(word)
            syl = tuple(syllables(word))
            index.exact.setdefault(syl, set()).add(word)
            index.similar.setdefault(tuple(blur(s) for s in syl), set()).add(word)
        index.lengths = sorted({len(w) for w in index.words}, reverse=True)
        return index


def scan(text: str, index: Index) -> tuple[list[dict], list[dict]]:
    """(replacements, suggestions) for `text`. Each item: from, to (or options), start. Positions refer to `text`."""
    if not available() or not index.words:
        return [], []
    replacements, suggestions = [], []
    for run in CJK_RUN.finditer(text):
        part, base = run.group(), run.start()
        syl = syllables(part)
        taken = [False] * len(part)
        for length in index.lengths:
            if length > len(part):
                continue
            for i in range(len(part) - length + 1):
                if any(taken[i:i + length]):
                    continue
                window = part[i:i + length]
                if window in index.words:                     # already a known term: leave it, and what is inside it
                    taken[i:i + length] = [True] * length
                    continue
                if _common(window):
                    continue
                key = tuple(syl[i:i + length])
                same = sorted(index.exact.get(key, set()) - {window})
                if length < SUGGEST_MIN_LENGTH:
                    continue
                if len(same) == 1 and length >= AUTO_MIN_LENGTH:
                    replacements.append({"from": window, "to": same[0], "start": base + i})
                    taken[i:i + length] = [True] * length
                    continue
                if same:                                      # several terms sound exactly alike: the doctor chooses
                    suggestions.append({"from": window, "options": same[:3], "start": base + i, "reason": "同音多词"})
                    taken[i:i + length] = [True] * length
                    continue
                if length >= SUGGEST_MIN_LENGTH:
                    near = sorted(index.similar.get(tuple(blur(s) for s in key), set()) - {window})
                    if near:
                        suggestions.append({"from": window, "options": near[:3], "start": base + i, "reason": "近音"})
                        taken[i:i + length] = [True] * length
    return replacements, suggestions


def apply(text: str, replacements: list[dict]) -> str:
    """Replace from the end so that earlier positions stay valid."""
    for item in sorted(replacements, key=lambda r: -r["start"]):
        text = text[: item["start"]] + item["to"] + text[item["start"] + len(item["from"]):]
    return text

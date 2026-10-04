"""From recognised lines to reading text: drop the phone's own screen furniture, join wrapped lines into paragraphs."""
import re
import statistics

PHONE_TOP = 0.14            # a screenshot's status bar and viewer toolbar sit in the top part ...
PHONE_BOTTOM = 0.94         # ... and the home indicator or navigation in the very bottom
CHROME = [
    re.compile(r"^\d{1,2}[:：]\d{2}$"),                                  # clock
    re.compile(r"^(of|第)\s?\d+(页)?$", re.I),                            # "1 of 2"
    re.compile(r"^[<>«»‹›×xXAa+\-–—=…·.\s]{1,3}$"),                      # back arrows, close, translate, more
    re.compile(r"^\d{1,3}%$"),                                            # battery
    re.compile(r".*(\.{2,}|…)$"),                                        # a title the viewer cut short
    re.compile(r"^(4G|5G|WiFi|Wi-Fi)$", re.I),
]
SENTENCE_END = "。！？!?"
LIST_MARK = re.compile(r"^\s*(\d{1,2}[\.．、)]|[（(]\d{1,2}[）)]|[（(]?[一二三四五六七八九十]+[）)、]|[①②③④⑤⑥⑦⑧⑨⑩])")


def drop_screen_furniture(lines: list[dict], height: int) -> tuple[list[dict], int]:
    """Only lines in the top or bottom strip that look like phone chrome are dropped; the picture stays on screen for the doctor."""
    kept, removed = [], 0
    for line in lines:
        centre = (line["y"] + line["h"] / 2) / max(height, 1)
        edge = centre < PHONE_TOP or centre > PHONE_BOTTOM
        if edge and any(pattern.match(line["text"]) for pattern in CHROME):
            removed += 1
            continue
        kept.append(line)
    return kept, removed


def paragraphs(lines: list[dict]) -> list[str]:
    """Join lines that are one paragraph wrapped by the page.

    A new paragraph starts before a list item, after a short heading that ends with a colon, and after a finished
    sentence when the next line is indented.
    """
    if not lines:
        return []
    base = statistics.median(line["x"] for line in lines)
    height = statistics.median(line["h"] for line in lines) or 1
    result: list[str] = []
    current = ""
    previous = None
    for line in lines:
        text = line["text"]
        if previous is None:
            current = text
        else:
            last = previous["text"].rstrip()[-1:]
            indented = line["x"] > base + 0.9 * height
            heading = last in "：:" and len(previous["text"]) <= 14
            if LIST_MARK.match(text) or heading or (last in SENTENCE_END and indented):
                result.append(current)
                current = text
            else:
                current += text
        previous = line
    result.append(current)
    return result


CHECK = re.compile(
    r"(?<![A-Za-z\d.])\d+(?:\.\d+)?\s*(?:×\s*10\^?\d+/L|mmHg|mmol/L|g/L|mg/L|μmol/L|U/L|HU|mm|cm|ml|mL|kg|g|mg|%|次/分|岁)|[阴阳]性|（[+＋\-－]{1,3}）|\([+\-]{1,3}\)")


def check_items(text: str, limit: int = 60) -> list[str]:
    """Measurements (a number with its unit) and positive / negative findings, for the doctor to compare with the picture."""
    seen, items = set(), []
    for match in CHECK.finditer(text):
        item = match.group().strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
        if len(items) >= limit:
            break
    return items

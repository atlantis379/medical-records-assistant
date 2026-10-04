"""A laboratory sheet (血常规 and the like) from recognised cells to rows: item, result, unit, reference range, flag.

The recogniser returns text boxes. Rows are found by their vertical position and columns by the header row (序号, 英文缩写,
项目名称, 结果, 单位, 参考区间, 测试方法), so nothing here depends on how many columns a particular hospital prints.

Two things the recogniser gets wrong on these sheets are repaired or checked here:

* the up and down arrows next to out-of-range results are often lost (the red ones above all). The flag is therefore worked
  out again from the result and the reference range, and a difference from the printed arrow is reported;
* superscripts disappear ("10^9/L" becomes "109/L"): repaired in the unit column only.
"""
import re

HEADERS = {
    "no": ("序号",), "abbr": ("英文缩写", "缩写", "代号"), "name": ("项目名称", "项目", "检验项目", "名称"),
    "result": ("结果", "检验结果", "测定值"), "unit": ("单位",), "range": ("参考区间", "参考范围", "参考值"), "method": ("测试方法", "方法"),
}
ARROWS = {"↑": "↑", "↓": "↓", "▲": "↑", "▼": "↓", "⬆": "↑", "⬇": "↓", "↗": "↑", "↘": "↓"}
NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
RANGE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)\s*[-–—~～]+\s*([-+]?\d+(?:\.\d+)?)\s*$")
UPPER = re.compile(r"^\s*[<≤＜]\s*=?\s*(\d+(?:\.\d+)?)\s*$")
LOWER = re.compile(r"^\s*[>≥＞]\s*=?\s*(\d+(?:\.\d+)?)\s*$")


def repair_unit(text: str) -> str:
    """109/L -> 10^9/L, 1012/L -> 10^12/L, 10°9/L and 10`9/L too. Other units are returned as read."""
    value = text.strip().replace(" ", "")
    match = re.fullmatch(r"10[`°^'’·*]?(\d{1,2})/([LlμuU]\w*)", value)
    if match and match.group(1) in ("3", "6", "9", "12", "15"):
        return f"10^{match.group(1)}/{match.group(2)}"
    match = re.fullmatch(r"1(0)?(\d{1,2})/([L])", value)
    if match and match.group(2) in ("9", "12"):
        return f"10^{match.group(2)}/L"
    if re.fullmatch(r"10(9|12)/L", value):
        return f"10^{value[2:value.index('/')]}/L"
    return value


def group_rows(lines: list[dict]) -> list[dict]:
    """Cells on the same text line, top to bottom: [{"centre": y, "cells": [...]}]."""
    rows: list[dict] = []
    for line in sorted(lines, key=lambda l: l["y"] + l["h"] / 2):
        centre = line["y"] + line["h"] / 2
        if rows and abs(rows[-1]["centre"] - centre) < max(line["h"], 14) * 0.6:
            rows[-1]["cells"].append(line)
        else:
            rows.append({"centre": centre, "cells": [line]})
    return rows


def header_columns(lines: list[dict]) -> tuple[dict[str, float], float] | None:
    """({column: x of its header}, y of the header row), or None when no text line is a table header."""
    best = None
    for row in group_rows(lines):
        found: dict[str, float] = {}
        for cell in row["cells"]:
            text = cell["text"].replace(" ", "")
            for column, names in HEADERS.items():
                if text in names and column not in found:
                    found[column] = cell["x"]
        if {"name", "result"} <= set(found) and len(found) >= 3 and (best is None or len(found) > len(best[0])):
            best = (found, row["centre"])
    return best


def assign(columns: dict[str, float], x: float) -> str:
    ordered = sorted(columns.items(), key=lambda item: item[1])
    for i, (name, left) in enumerate(ordered):
        right = ordered[i + 1][1] if i + 1 < len(ordered) else float("inf")
        low = (ordered[i - 1][1] + left) / 2 if i else float("-inf")
        high = (left + right) / 2
        if low <= x < high:
            return name
    return ordered[-1][0]


def strip_arrow(result: str) -> tuple[str, str]:
    flag = ""
    for glyph, arrow in ARROWS.items():
        if glyph in result:
            flag = arrow
            result = result.replace(glyph, "")
    match = re.search(r"\s*([HL])\s*$", result)                    # some sheets print H / L instead of arrows
    if match and NUMBER.search(result):
        flag = "↑" if match.group(1) == "H" else "↓"
        result = result[: match.start()]
    return result.strip(), flag


def derived_flag(result: str, range_text: str) -> str | None:
    """"↑", "↓", "" (inside the range) or None when it cannot be worked out."""
    number = NUMBER.search(result.replace(",", ""))
    if not number:
        return None
    value = float(number.group())
    reference = range_text.replace(" ", "")
    match = RANGE.match(reference)
    if match:
        low, high = float(match.group(1)), float(match.group(2))
        if low == high == 0:
            return "↑" if value > 0 else ""
        return "↓" if value < low else "↑" if value > high else ""
    match = UPPER.match(reference)
    if match:
        return "↑" if value > float(match.group(1)) else ""
    match = LOWER.match(reference)
    if match:
        return "↓" if value < float(match.group(1)) else ""
    return None


def parse(lines: list[dict]) -> dict | None:
    """{"columns": [...], "rows": [...]} for a laboratory table, or None when the picture has no such table."""
    header = header_columns(lines)
    if header is None:
        return None
    columns, header_y = header
    body = [line for line in lines if line["y"] > header_y + 4]
    rows = group_rows(body)
    parsed = []
    for row in rows:
        cells: dict[str, str] = {}
        for cell in sorted(row["cells"], key=lambda c: c["x"]):
            column = assign(columns, cell["x"])
            cells[column] = (cells.get(column, "") + " " + cell["text"]).strip() if column in cells else cell["text"]
        name = cells.get("name", "")
        result, arrow = strip_arrow(cells.get("result", ""))
        if not name or not re.search(r"[一-鿿A-Za-z]", name) or not (result or cells.get("range")):
            continue
        unit = repair_unit(cells.get("unit", ""))
        range_text = re.sub(r"[-–—]{2,}", "-", cells.get("range", "")).replace("—", "-")
        worked_out = derived_flag(result, range_text)
        note = ""
        if not result:
            note = "没有识别出结果，请对照原图"
            flag = ""
        elif worked_out is None:
            flag = arrow
        else:
            flag = worked_out
            if worked_out != arrow:
                note = (f"按参考范围应为“{worked_out or '正常'}”，图片上的箭头{'没有识别出来' if not arrow else '是“' + arrow + '”'}，请对照原图"
                        if worked_out else f"图片上识别出“{arrow}”，但结果在参考范围内，请对照原图")
        parsed.append({"no": cells.get("no", ""), "abbr": cells.get("abbr", ""), "name": name.lstrip("*＊"), "required": name.startswith(("*", "＊")),
                       "result": result, "flag": flag, "printed_flag": arrow, "unit": unit, "range": range_text,
                       "method": cells.get("method", ""), "note": note})
    if len(parsed) < 2:
        return None
    return {"columns": sorted(columns, key=columns.get), "rows": parsed}


def line_for(row: dict) -> str:
    """One item as it is written into a record: 淋巴细胞计数 0.77↓ 10^9/L（参考 1.1-3.2）."""
    head = f"{row['name']} {row['result']}{row['flag']}".strip()
    tail = " ".join(part for part in (row["unit"], f"（参考 {row['range']}）" if row["range"] else "") if part)
    return f"{head} {tail}".strip()

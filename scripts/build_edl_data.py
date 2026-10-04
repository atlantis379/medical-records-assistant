"""Reads the national essential medicines catalogue (国家基本药物目录, PDF) and writes its drug names with the
catalogue's own classification: server/data/reference/essential_drugs_2026.json

The PDF is an official publication (data source tier A, docs/DATA_SOURCE_POLICY.md). The classification in it is
by pharmacological class (chemical drugs and biologicals) and by function (patent medicines), not by hospital
department; the mapping to departments is a separate, reviewable file (essential_drugs_departments.json).

Needs `pdftotext` (poppler) on the PATH:  python scripts/build_edl_data.py <catalogue.pdf>
The names come from the catalogue's own stroke index (the complete list); each one is then looked up in the body
to find its class. A name that cannot be placed is reported, never guessed.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "server" / "data" / "reference" / "essential_drugs_2026.json"

CN_NUM = "一二三四五六七八九十"
LEVEL1 = re.compile(rf"^\s*([{CN_NUM}]+)、\s*(.+?)\s*$")
LEVEL2 = re.compile(rf"^\s*（([{CN_NUM}]+)）\s*(.+?)\s*$")
STROKE_HEADING = re.compile(r"^\s*[一二三四五六七八九十]+\s*画\s*$")


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text)


def layout_text(pdf: Path) -> list[str]:
    result = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=True)
    return result.stdout.decode("utf-8").splitlines()


def lines_matching(lines, pattern):
    return [i for i, line in enumerate(lines) if re.fullmatch(pattern, line.strip())]


def index_names(lines, start, stop) -> set[str]:
    """Names of the catalogue's stroke index: the cells of its two-column table, without the page numbers."""
    names: set[str] = set()
    for line in lines[start:stop]:
        if "索引" in line or "第一部分" in line or "第二部分" in line or re.fullmatch(r"\s*-\s*\d+\s*-\s*", line):
            continue
        for cell in re.split(r"\s{3,}|\t", line.strip()):
            cell = re.sub(r"[\s\d,，、]+$", "", cell.strip()).strip()
            if not cell or STROKE_HEADING.match(cell) or re.fullmatch(r"[一二三四五六七八九十]+\s*画", squash(cell)):
                continue
            if re.fullmatch(r"[A-Z]", cell):
                continue
            name = squash(cell)
            if name.startswith("（") or name.count("）") > name.count("（"):      # the tail of a name that wrapped onto the next line
                continue
            if len(name) >= 2 and re.search(r"[一-鿿]", name):
                names.add(name)
    return names


def body_names(line: str) -> list[str]:
    """Candidate names in a body line: the cells that start in the name column (after an optional serial number)."""
    text = re.sub(r"^\s*\d{1,3}(?=\s)", "", line)
    return [squash(cell) for cell in re.split(r"\s{2,}", text.strip()) if cell.strip()]


def classify(lines, start, stop, names: set[str]):
    """name -> [(category, subcategory)] using the headings above each line that carries the name."""
    found: dict[str, list] = {}
    # a body cell may carry only the base name ("五苓散"); the index name then adds the dosage forms in brackets
    lookup = {name: name for name in names}
    for name in sorted(names, key=len, reverse=True):
        base = re.sub(r"（[^）]*）?$", "", name)
        if base and base != name:
            lookup.setdefault(base, name)
    level1 = level2 = ""
    for line in lines[start:stop]:
        m1, m2 = LEVEL1.match(line), LEVEL2.match(line)
        if m1 and not re.search(r"\d\s*$", line) and len(line) - len(line.lstrip()) < 40:
            level1, level2 = squash(m1.group(2)), ""
            continue
        if m2 and not re.search(r"\d\s*$", line):
            level2 = squash(m2.group(1 + 1))
            continue
        cells = body_names(line)
        # part 2 puts the serial number, the function and the name in one run of single spaces ("80 滋阴降火 知柏地黄丸")
        raw = re.sub(r"^\s*\d{1,3}(?=\s)", "", line).strip()
        cells = cells[:3] + [squash(t) for t in re.split(r"\s+", raw)[:4]]
        for cell in cells:                                  # the name column comes first; dosage text follows
            for candidate in (cell, re.sub(r"（[^）]*）?$", "", cell)):
                if candidate in lookup and level1:
                    found.setdefault(lookup[candidate], []).append((level1, level2))
                    break
    return found


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    lines = layout_text(Path(argv[1]))
    stroke = lines_matching(lines, "中文笔画索引")
    pinyin = lines_matching(lines, "中文拼音索引")
    if len(stroke) < 2 or len(pinyin) < 2:
        raise SystemExit("cannot find the two stroke indexes; is this the 2026 catalogue?")
    body1 = next(i for i in lines_matching(lines, r"序号\s+品种名称\s+剂型、规格\s+备注"))
    body2 = next(i for i in range(len(lines)) if re.match(r"^序号\s+功能\s+药品名称", lines[i]))
    names1 = index_names(lines, stroke[0], pinyin[0])
    names2 = index_names(lines, stroke[1], pinyin[1])
    found1 = classify(lines, body1 - 12, body2 - 12, names1)   # the first class heading stands above the table header
    found2 = classify(lines, body2 - 12, stroke[0], names2)
    result = {"source": "国家基本药物目录（2026年版），中华人民共和国国家卫生健康委员会", "tier": "A",
              "classification": {"part1": "按临床药理学分类（目录原文）", "part2": "按功能分类（目录原文）"}, "drugs": [], "unplaced": []}
    for part, names, found in (("part1", names1, found1), ("part2", names2, found2)):
        missing = sorted(names - set(found))
        print(f"{part}: index {len(names)} names, placed {len(found)}, not placed {len(missing)}: {missing[:12]}")
        result["unplaced"] += [{"name": n, "part": part} for n in missing]
        for name in sorted(found):
            places = found[name]
            result["drugs"].append({"name": name, "part": part, "category": places[0][0], "subcategory": places[0][1],
                                    "also_in": sorted({f"{a}/{b}" for a, b in places[1:] if (a, b) != places[0]})})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("written", OUT, len(result["drugs"]))


if __name__ == "__main__":
    main(sys.argv)

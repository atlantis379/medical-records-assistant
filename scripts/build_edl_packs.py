"""Builds one draft hotword pack per department from the essential medicines catalogue data.

Input : server/data/reference/essential_drugs_2026.json            (scripts/build_edl_data.py, from the PDF)
        server/data/reference/essential_drugs_departments.json     (category -> departments, to be reviewed)
Output: server/data/hotword_packs/edl_<department>.txt + .manifest.json   (status draft: off until a physician reviews)

Only drug names are written, in the form clinicians say them (the catalogue's bracketed dosage forms are dropped,
"五苓散（胶囊、片）" becomes "五苓散"). Run again after the mapping has been reviewed or changed.
"""
import datetime
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "server" / "data" / "reference"
PACKS = ROOT / "server" / "data" / "hotword_packs"
SHARED = "*"


def clean_name(name: str) -> str | None:
    base = re.sub(r"（[^）]*）?$", "", name).strip()
    base = re.sub(r"[（(].*$", "", base).strip()
    if len(base) < 2 or not re.search(r"[一-鿿]", base) or re.search(r"[）)、]", base):
        return None
    return base


def departments_for(drug: dict, mapping: list[dict]) -> list[str]:
    for row in mapping:
        if row["part"] != drug["part"] or row["category"] != drug["category"]:
            continue
        if row.get("subcategory") and row["subcategory"] != drug["subcategory"]:
            continue
        return row["departments"]
    raise SystemExit(f"no department for {drug['part']} / {drug['category']} / {drug['subcategory']}: extend essential_drugs_departments.json")


def main():
    data = json.loads((REF / "essential_drugs_2026.json").read_text(encoding="utf-8"))
    mapping_file = json.loads((REF / "essential_drugs_departments.json").read_text(encoding="utf-8"))
    labels = {d["id"]: d for d in mapping_file["departments"]}
    labels[SHARED] = {"id": SHARED, "label": "各科通用", "label_en": "Shared"}
    words: dict[str, list[str]] = {}
    for drug in data["drugs"]:
        name = clean_name(drug["name"])
        if name is None:
            continue
        for department in departments_for(drug, mapping_file["map"]):
            bucket = words.setdefault(department, [])
            if name not in bucket:
                bucket.append(name)
    today = datetime.date.today().isoformat()
    for department, items in sorted(words.items()):
        info = labels[department]
        pack_id = "edl_shared" if department == SHARED else f"edl_{department}"
        label = f"{info['label']}基本药物词库"
        (PACKS / f"{pack_id}.txt").write_text(
            f"# {label}（草稿，待医生审核；审核前不得默认启用）\n"
            "# 来源：国家基本药物目录（2026年版）。只含药品名称，不含剂量、规格和用法。\n"
            "# 类别到科室的对应由项目整理（server/data/reference/essential_drugs_departments.json），须由该科医生审核。\n\n"
            + "\n".join(items) + "\n", encoding="utf-8")
        (PACKS / f"{pack_id}.manifest.json").write_text(json.dumps({
            "id": pack_id,
            "specialty": department,
            "label": label,
            "label_en": f"{info['label_en']} essential medicines",
            "filename": f"{pack_id}.txt",
            "language": "zh-CN",
            "version": "2026-draft",
            "status": "draft",
            "compiled_on": today,
            "compile_method": "scripts/build_edl_data.py 从目录 PDF 提取药品名称及其类别，scripts/build_edl_packs.py 按类别到科室的对应生成；对应关系由项目整理，未经医生审核。",
            "sources": [{
                "tier": "A",
                "name": "国家基本药物目录（2026年版）",
                "url": None,
                "used_for": "药品名称及其所属类别（官方公开文件）",
                "page_read": True,
            }],
            "reviewers": [],
            "reviewed_on": None,
            "review_notes": "",
            "known_gaps": [
                "目录只含基本药物，不含本科室常用的其他药品。",
                "按类别归入科室是项目的整理，一种药可能被多个科室使用，须医生确认是否保留。",
                "目录不含疾病名称；疾病、症状词需要另外的来源。",
            ],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{pack_id}: {len(items)} names")


if __name__ == "__main__":
    main()

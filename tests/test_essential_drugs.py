"""The national essential medicines catalogue as data: every category is mapped to departments, the packs built
from it are drafts that contain drug names only, and the departments exist in the product."""
import json
import re
import sys
import unittest
from pathlib import Path

from server import app as srv

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_edl_packs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "server" / "data" / "reference"
DATA = json.loads((REF / "essential_drugs_2026.json").read_text(encoding="utf-8"))
MAPPING = json.loads((REF / "essential_drugs_departments.json").read_text(encoding="utf-8"))
PACKS = ROOT / "server" / "data" / "hotword_packs"


def departments_for(drug):
    for row in MAPPING["map"]:
        if row["part"] == drug["part"] and row["category"] == drug["category"] and (not row.get("subcategory") or row["subcategory"] == drug["subcategory"]):
            return row["departments"]
    return None


class Catalogue(unittest.TestCase):
    def test_the_extraction_covers_almost_all_of_the_catalogue(self):
        parts = {p: sum(1 for d in DATA["drugs"] if d["part"] == p) for p in ("part1", "part2")}
        self.assertGreaterEqual(parts["part1"], 460)           # the catalogue lists 476 chemical drugs and biologicals
        self.assertGreaterEqual(parts["part2"], 310)           # and 318 patent medicines
        self.assertLessEqual(len(DATA["unplaced"]), 25)        # names that could not be placed are listed, never guessed
        self.assertEqual(DATA["tier"], "A")

    def test_every_drug_category_is_mapped_to_departments(self):
        unmapped = {(d["part"], d["category"], d["subcategory"]) for d in DATA["drugs"] if departments_for(d) is None}
        self.assertEqual(unmapped, set())

    def test_the_mapping_only_names_known_departments(self):
        known = {d["id"] for d in MAPPING["departments"]} | {"*"}
        for row in MAPPING["map"]:
            self.assertTrue(set(row["departments"]) <= known, row)
        self.assertEqual(MAPPING["status"], "draft")
        self.assertEqual(MAPPING["reviewers"], [])

    def test_the_product_offers_every_department_of_the_mapping(self):
        self.assertEqual([d["id"] for d in MAPPING["departments"]], srv.SPECIALTY_IDS)
        for department in MAPPING["departments"]:
            item = next(s for s in srv.SPECIALTIES if s["id"] == department["id"])
            self.assertEqual((item["label"], item["label_en"]), (department["label"], department["label_en"]))

    def test_drug_names_are_clean(self):
        for drug in DATA["drugs"]:
            self.assertIsNotNone(build_edl_packs.clean_name(drug["name"]), drug["name"])       # no wrapped fragments such as "片）"
            self.assertEqual(drug["name"], drug["name"].strip())
            self.assertNotIn(" ", drug["name"])
            self.assertNotRegex(drug["name"], r"[�?]")


class Packs(unittest.TestCase):
    def edl_manifests(self):
        return sorted(PACKS.glob("edl_*.manifest.json"))

    def test_there_is_a_pack_for_every_department_that_has_drugs(self):
        wanted = {dep for d in DATA["drugs"] for dep in (departments_for(d) or [])}
        built = {json.loads(p.read_text(encoding="utf-8"))["specialty"] for p in self.edl_manifests()}
        self.assertEqual(built, wanted)

    def test_packs_are_drafts_from_a_tier_a_source_and_stay_off_by_default(self):
        for path in self.edl_manifests():
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((manifest["status"], manifest["reviewers"]), ("draft", []), path.name)
            self.assertEqual(manifest["sources"][0]["tier"], "A")
            pack = next(p for p in srv.all_packs() if p["id"] == manifest["id"])
            self.assertEqual(pack["status"], "draft")
            self.assertFalse(srv.pack_is_active(pack, [manifest["specialty"]], False), "a draft must stay off by default")

    def test_pack_words_come_from_the_catalogue_and_carry_no_doses(self):
        catalogue = {build_edl_packs.clean_name(d["name"]) for d in DATA["drugs"]}
        for path in self.edl_manifests():
            words = srv.read_words_from_file(PACKS / json.loads(path.read_text(encoding="utf-8"))["filename"])
            self.assertTrue(words, path.name)
            for word in words:
                self.assertIn(word, catalogue, f"{path.name}: {word}")
                self.assertNotRegex(word, r"\d+\s*(mg|g|ml|IU|μg|万单位)", word)

    def test_a_drug_is_in_the_pack_of_each_department_its_category_maps_to(self):
        words = {p.name: set(srv.read_words_from_file(PACKS / p.name.replace(".manifest.json", ".txt"))) for p in self.edl_manifests()}
        self.assertIn("头孢曲松", words["edl_infectious_disease.manifest.json"])
        self.assertIn("布地奈德", words["edl_respiratory_critical_care.manifest.json"])
        self.assertIn("布洛芬", words["edl_orthopedics.manifest.json"])
        self.assertIn("硝苯地平", words["edl_cardiology.manifest.json"])
        self.assertNotIn("头孢曲松", words["edl_cardiology.manifest.json"])


if __name__ == "__main__":
    unittest.main()

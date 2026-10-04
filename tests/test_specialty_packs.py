"""Integrity tests for specialty hotword packs and their manifests.

A pack in 'draft' status must never be enabled by default; this is the
safety net for the physician-review requirement in docs/DATA_SOURCE_POLICY.md.
"""
import json
import unittest
from pathlib import Path

from server import app as srv

PACK_DIR = Path(srv.HOTWORD_PACK_DIR)
MANIFESTS = sorted(PACK_DIR.glob("*.manifest.json"))
REQUIRED_FIELDS = {"id", "specialty", "label", "label_en", "filename", "language", "version",
                   "status", "compiled_on", "compile_method", "sources", "reviewers", "reviewed_on"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class SpecialtyManifests(unittest.TestCase):
    def test_expected_first_specialties_exist(self):
        ids = {load(path)["id"] for path in MANIFESTS}
        self.assertTrue({"respiratory_critical_care", "orthopedics"} <= ids)

    def test_manifest_has_required_fields(self):
        for path in MANIFESTS:
            self.assertFalse(REQUIRED_FIELDS - set(load(path)), path.name)

    def test_manifest_matches_file_name_and_pack_file(self):
        for path in MANIFESTS:
            manifest = load(path)
            self.assertEqual(path.name, f"{manifest['id']}.manifest.json")
            self.assertTrue((PACK_DIR / manifest["filename"]).exists(), path.name)

    def test_status_is_known_and_reviewed_packs_name_a_reviewer(self):
        for path in MANIFESTS:
            manifest = load(path)
            self.assertIn(manifest["status"], {"draft", "reviewed"}, path.name)
            if manifest["status"] == "reviewed":
                self.assertTrue(manifest["reviewers"], f"{path.name}: reviewed pack lists no reviewer")
                self.assertTrue(manifest["reviewed_on"], f"{path.name}: reviewed pack has no review date")

    def test_every_source_is_tiered_and_says_whether_it_was_read(self):
        for path in MANIFESTS:
            for source in load(path)["sources"]:
                self.assertIn(source["tier"], {"A", "B"}, path.name)
                self.assertIn("page_read", source, path.name)
                self.assertTrue(source["name"] and source["used_for"], path.name)

    def test_draft_packs_are_not_enabled_in_the_runtime_pack_list(self):
        enabled_ids = {pack["id"] for pack in srv.HOTWORD_PACKS if pack.get("enabled", True)}
        for path in MANIFESTS:
            manifest = load(path)
            if manifest["status"] == "draft":
                self.assertNotIn(manifest["id"], enabled_ids, f"draft pack {manifest['id']} must not be active")


class SpecialtyWordLists(unittest.TestCase):
    def words(self, manifest):
        return srv.read_words_from_file(PACK_DIR / manifest["filename"])

    def test_word_lists_are_clean(self):
        for path in MANIFESTS:
            manifest = load(path)
            text = (PACK_DIR / manifest["filename"]).read_text(encoding="utf-8")
            self.assertNotIn("�", text, path.name)
            self.assertNotIn("???", text, path.name)
            words = self.words(manifest)
            # the hand-compiled specialty packs are large; the essential-medicines packs hold only the drugs of one department
            self.assertGreater(len(words), 5 if path.name.startswith("edl_") else 100, path.name)
            self.assertLessEqual(len(words), srv.MAX_HOTWORDS, path.name)
            for word in words:
                self.assertLessEqual(len(word), 80, word)
                self.assertEqual(word, word.strip())

    def test_no_duplicates_within_a_pack(self):
        for path in MANIFESTS:
            lines = [line.strip() for line in (PACK_DIR / load(path)["filename"]).read_text(encoding="utf-8").splitlines()
                     if line.strip() and not line.startswith("#")]
            dupes = {w for w in lines if lines.count(w) > 1}
            self.assertEqual(dupes, set(), path.name)

    def test_packs_do_not_carry_doses_or_reference_ranges(self):
        # Hotword lists are vocabulary only; numeric thresholds or doses would
        # look like clinical guidance. Roman-numeral style names are fine.
        import re
        pattern = re.compile(r"\d+(\.\d+)?\s*(mg|g|ml|mL|μg|mmHg|IU|%|次/分)")
        for path in MANIFESTS:
            for word in self.words(load(path)):
                self.assertIsNone(pattern.search(word), f"{path.name}: {word}")

    def test_brand_names_are_not_listed_as_drugs(self):
        brands = {"钙尔奇", "芬必得", "西乐葆", "扶他林", "拜瑞妥", "万艾可"}
        for path in MANIFESTS:
            self.assertEqual(brands & set(self.words(load(path))), set(), path.name)


if __name__ == "__main__":
    unittest.main()

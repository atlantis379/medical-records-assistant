"""Tests for hotword selection and the integrity of bundled data files."""
import json
import unittest
from pathlib import Path

from server import app as srv


class HotwordSelection(unittest.TestCase):
    def test_respects_profile_limits(self):
        for profile in srv.ASR_PROFILES.values():
            words = srv.active_hotwords(profile)
            self.assertLessEqual(len(words), profile["hotword_limit"], profile["id"])
            self.assertLessEqual(sum(len(w) + 1 for w in words), profile["hotword_char_limit"], profile["id"])

    def test_no_case_insensitive_duplicates(self):
        words = srv.active_hotwords(srv.ASR_PROFILES["accurate"])
        folded = [w.casefold() for w in words]
        self.assertEqual(len(folded), len(set(folded)))

    def test_larger_profiles_never_select_fewer_words(self):
        fast = len(srv.active_hotwords(srv.ASR_PROFILES["fast"]))
        balanced = len(srv.active_hotwords(srv.ASR_PROFILES["balanced"]))
        accurate = len(srv.active_hotwords(srv.ASR_PROFILES["accurate"]))
        self.assertLessEqual(fast, balanced)
        self.assertLessEqual(balanced, accurate)

    def test_user_custom_words_have_highest_priority(self):
        custom = srv.read_custom_hotwords()
        if not custom:
            self.skipTest("no user custom hotwords bundled")
        selected = srv.active_hotwords(srv.ASR_PROFILES["fast"])
        # the longest custom word sorts first within the top priority tier
        self.assertIn(max(custom, key=len), selected)

    def test_legacy_department_argument_still_works(self):
        # `department` predates specialties; it now means "a single specialty".
        self.assertEqual(srv.active_hotwords(department="infectious_disease"),
                         srv.active_hotwords(specialties=["infectious_disease"]))
        # unknown departments (the extension sends "general" for English) get shared packs only
        self.assertEqual(srv.active_hotwords(department="general"), srv.active_hotwords(specialties=[]))

    def test_resolve_profile_falls_back_to_balanced(self):
        self.assertEqual(srv.resolve_asr_profile("nonsense")["id"], "balanced")
        self.assertEqual(srv.resolve_asr_profile("FAST")["id"], "fast")


class HotwordInputCleaning(unittest.TestCase):
    def test_strips_and_dedupes(self):
        self.assertEqual(srv.clean_hotword_values(["  美罗培南 ", "美罗培南", "", "# note"]), ["美罗培南"])

    def test_rejects_overlong_word(self):
        with self.assertRaises(Exception):
            srv.clean_hotword_values(["长" * 81])


class BundledData(unittest.TestCase):
    def test_every_pack_file_exists_and_is_nonempty(self):
        for pack in srv.HOTWORD_PACKS:
            path = srv.pack_path(pack)
            self.assertTrue(path.exists(), pack["id"])
            if pack["built_in"]:  # the user's own list may legitimately be empty
                self.assertTrue(srv.read_words_from_file(path), pack["id"])

    def test_pack_ids_are_unique(self):
        ids = [pack["id"] for pack in srv.HOTWORD_PACKS]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_pack_has_a_priority(self):
        # priority is a hard-coded table today; a new pack missing from it
        # silently falls to the lowest tier.
        priority_ids = {"user_custom", "respiratory_history", "medical_history", "clinical_metrics",
                        "antimicrobials", "pathogens", "infectious_disease", "general_medical"}
        self.assertEqual({pack["id"] for pack in srv.HOTWORD_PACKS}, priority_ids)

    def test_hotword_files_are_utf8_without_replacement_chars(self):
        for path in srv.HOTWORD_PACK_DIR.glob("*.txt"):
            self.assertNotIn("�", path.read_text(encoding="utf-8"), path.name)

    def test_no_word_is_encoding_damaged(self):
        # Literal '?' runs are what a bad encoding round-trip leaves behind (commit b0d2dd0
        # damaged 35 lines). They would be sent to the recognizer as hotwords.
        for path in list(srv.HOTWORD_PACK_DIR.glob("*.txt")) + [srv.HOTWORD_FILE]:
            bad = [line for line in path.read_text(encoding="utf-8").splitlines() if "?" in line or "？？" in line]
            self.assertEqual(bad, [], path.name)

    def test_correction_rule_files_are_valid_json(self):
        for path in Path(srv.CORRECTION_RULE_DIR).glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            self.assertIsInstance(payload.get("rules", payload), list, path.name)

    def test_correction_rules_are_well_formed(self):
        rules = srv.read_correction_rules()
        self.assertTrue(rules)
        for rule in rules:
            self.assertTrue(rule["from"] and rule["to"])
            self.assertNotEqual(rule["from"], rule["to"])

    def test_no_conflicting_correction_rules(self):
        targets = {}
        for rule in srv.read_correction_rules():
            targets.setdefault(rule["from"], set()).add(rule["to"])
        conflicts = {k: v for k, v in targets.items() if len(v) > 1}
        self.assertEqual(conflicts, {})

    def test_rule_target_does_not_contain_its_source(self):
        # e.g. "曲霉" -> "曲霉菌" turned the correct "曲霉菌" into "曲霉菌菌".
        offenders = [r for r in srv.read_correction_rules() if r["from"] in r["to"]]
        self.assertEqual(offenders, [])

    def test_correct_terms_pass_through_unchanged(self):
        for term in ["曲霉菌感染", "肺曲霉病", "美罗培南", "肺炎克雷伯菌"]:
            self.assertEqual(srv.apply_correction_rules(term)[0], term)

    def test_rules_do_not_rewrite_their_own_targets(self):
        # applying the rules twice must give the same result
        for rule in srv.read_correction_rules():
            once, _ = srv.apply_correction_rules(rule["from"])
            twice, _ = srv.apply_correction_rules(once)
            self.assertEqual(once, twice, rule["from"])

    def test_docs_are_not_corrupted(self):
        root = Path(srv.APP_DIR).parent
        paths = list((root / "docs").glob("*.md")) + list((root / "packaging").rglob("*.md"))
        paths += [root / "README.md", root / "THIRD_PARTY_NOTICES.md", srv.HOTWORD_PACK_DIR / "SOURCES.md"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertFalse("???" in text, f"{path.name} contains literal '???' (encoding damage)")


if __name__ == "__main__":
    unittest.main()

"""Per-specialty hotword selection, draft gating and specialty-scoped corrections."""
import io
import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as srv

client = TestClient(srv.app, headers={"Origin": "http://testserver"})
BUILT_IN_IDS = {pack["id"] for pack in srv.HOTWORD_PACKS}
SPECIALTY_ONLY_WORD = "股骨颈骨折"      # orthopedics draft pack only
RESPIRATORY_ONLY_WORD = "俯卧位通气"    # respiratory_critical_care draft pack only


def active_pack_ids(specialties, include_draft=None):
    selected = srv.resolve_specialties(specialties)
    return {pack["id"] for pack in srv.all_packs() if srv.pack_is_active(pack, selected, include_draft)}


class SpecialtySelection(unittest.TestCase):
    def test_default_selection_keeps_the_previous_infectious_disease_behaviour(self):
        self.assertEqual(srv.DEFAULT_SPECIALTIES, ["infectious_disease"])
        self.assertEqual(active_pack_ids(None), BUILT_IN_IDS)  # all eight packs, as before
        self.assertEqual(srv.active_hotwords(srv.ASR_PROFILES["balanced"]),
                         srv.active_hotwords(srv.ASR_PROFILES["balanced"], specialties=["infectious_disease"]))

    def test_orthopedics_does_not_get_infectious_disease_packs(self):
        ids = active_pack_ids(["orthopedics"])
        self.assertNotIn("infectious_disease", ids)
        self.assertNotIn("pathogens", ids)
        self.assertNotIn("respiratory_history", ids)
        self.assertLessEqual({"general_medical", "clinical_metrics", "medical_history", "user_custom", "antimicrobials"}, ids)

    def test_respiratory_critical_care_gets_respiratory_history(self):
        ids = active_pack_ids(["respiratory_critical_care"])
        self.assertIn("respiratory_history", ids)
        self.assertNotIn("infectious_disease", ids)

    def test_multiple_specialties_union_their_packs(self):
        both = active_pack_ids(["orthopedics", "infectious_disease"])
        self.assertTrue(active_pack_ids(["orthopedics"]) <= both)
        self.assertTrue(active_pack_ids(["infectious_disease"]) <= both)

    def test_explicit_empty_selection_keeps_only_shared_packs(self):
        ids = active_pack_ids([])
        self.assertEqual(ids, {"user_custom", "medical_history", "clinical_metrics", "general_medical"})

    def test_unknown_specialties_are_ignored(self):
        self.assertEqual(srv.normalize_specialties("orthopedics, nonsense,orthopedics"), ["orthopedics"])
        self.assertEqual(srv.normalize_specialties(["x"]), [])
        self.assertEqual(srv.normalize_specialties(""), [])
        self.assertEqual(srv.normalize_specialties(None), srv.DEFAULT_SPECIALTIES)


class DraftGating(unittest.TestCase):
    def test_specialty_packs_ship_as_drafts(self):
        specialty_packs = [pack for pack in srv.all_packs() if pack["id"] in {"orthopedics", "respiratory_critical_care"}]
        self.assertEqual(len(specialty_packs), 2)
        for pack in specialty_packs:
            self.assertEqual(pack["status"], "draft")

    def test_drafts_are_inactive_by_default_and_opt_in(self):
        self.assertNotIn("orthopedics", active_pack_ids(["orthopedics"], include_draft=False))
        self.assertIn("orthopedics", active_pack_ids(["orthopedics"], include_draft=True))

    def test_draft_words_never_reach_the_recognizer_without_opt_in(self):
        accurate = srv.ASR_PROFILES["accurate"]
        self.assertNotIn(SPECIALTY_ONLY_WORD, srv.active_hotwords(accurate, specialties=["orthopedics"], include_draft=False))
        self.assertIn(SPECIALTY_ONLY_WORD, srv.active_hotwords(accurate, specialties=["orthopedics"], include_draft=True))

    def test_draft_words_only_for_the_chosen_specialty(self):
        accurate = srv.ASR_PROFILES["accurate"]
        words = srv.active_hotwords(accurate, specialties=["respiratory_critical_care"], include_draft=True)
        self.assertIn(RESPIRATORY_ONLY_WORD, words)
        self.assertNotIn(SPECIALTY_ONLY_WORD, words)

    def test_manifest_claiming_reviewed_without_reviewer_is_still_a_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "x.txt").write_text("词一\n", encoding="utf-8")
            (tmp_path / "x.manifest.json").write_text(json.dumps(
                {"id": "x", "specialty": "orthopedics", "filename": "x.txt", "status": "reviewed", "reviewers": []}), encoding="utf-8")
            with mock.patch.object(srv, "HOTWORD_PACK_DIR", tmp_path):
                packs = srv.load_specialty_packs()
        self.assertEqual([pack["status"] for pack in packs], ["draft"])

    def test_reviewed_manifest_with_reviewer_is_active_without_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "x.txt").write_text("词一\n", encoding="utf-8")
            (tmp_path / "x.manifest.json").write_text(json.dumps(
                {"id": "x", "specialty": "orthopedics", "filename": "x.txt", "status": "reviewed", "reviewers": ["某医生"]}), encoding="utf-8")
            with mock.patch.object(srv, "HOTWORD_PACK_DIR", tmp_path):
                pack = srv.load_specialty_packs()[0]
        self.assertEqual(pack["status"], "reviewed")
        self.assertTrue(srv.pack_is_active(pack, ["orthopedics"], include_draft=False))

    def test_manifest_cannot_point_outside_the_pack_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "bad.manifest.json").write_text(json.dumps(
                {"id": "bad", "specialty": "orthopedics", "filename": "../secret.txt"}), encoding="utf-8")
            (tmp_path / "broken.manifest.json").write_text("{not json", encoding="utf-8")
            with mock.patch.object(srv, "HOTWORD_PACK_DIR", tmp_path):
                self.assertEqual(srv.load_specialty_packs(), [])


class SpecialtyScopedCorrections(unittest.TestCase):
    def apply_with_rules(self, rules, text, specialties):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "rules.json").write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(srv, "CORRECTION_RULE_DIR", Path(tmp)):
                return srv.apply_correction_rules(text, "zh-CN", specialties)[0]

    def test_untagged_rules_apply_to_everyone(self):
        rules = {"rules": [{"from": "甲词", "to": "乙词"}]}
        for specialties in (["orthopedics"], ["infectious_disease"], []):
            self.assertEqual(self.apply_with_rules(rules, "这是甲词", specialties), "这是乙词。".rstrip("。") if False else "这是乙词")

    def test_file_level_tag_restricts_rules(self):
        rules = {"specialties": ["orthopedics"], "rules": [{"from": "甲词", "to": "乙词"}]}
        self.assertEqual(self.apply_with_rules(rules, "这是甲词", ["orthopedics"]), "这是乙词")
        self.assertEqual(self.apply_with_rules(rules, "这是甲词", ["infectious_disease"]), "这是甲词")
        self.assertEqual(self.apply_with_rules(rules, "这是甲词", []), "这是甲词")

    def test_rule_level_tag_overrides_file_level(self):
        rules = {"specialties": ["orthopedics"], "rules": [{"from": "甲词", "to": "乙词", "specialties": ["infectious_disease"]}]}
        self.assertEqual(self.apply_with_rules(rules, "这是甲词", ["infectious_disease"]), "这是乙词")
        self.assertEqual(self.apply_with_rules(rules, "这是甲词", ["orthopedics"]), "这是甲词")

    def test_bundled_rules_are_unchanged_for_every_specialty(self):
        for specialties in (["orthopedics"], ["respiratory_critical_care"], ["infectious_disease"]):
            self.assertEqual(srv.apply_correction_rules("梅罗培南", "zh-CN", specialties)[0], "美罗培南")


def make_wav(seconds=1.0, rate=16000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate)))
        wf.writeframes(frames)
    return buffer.getvalue()


def words_only_in_infectious_packs():
    """Words that no other pack nor the user list contains, so absence/presence is unambiguous."""
    by_id = {pack["id"]: set(srv.read_pack_words(pack)) for pack in srv.HOTWORD_PACKS if pack["id"] != "user_custom"}
    infectious = by_id["infectious_disease"] | by_id["pathogens"] | by_id["respiratory_history"]
    elsewhere = set(srv.read_custom_hotwords())
    for pack_id in ("general_medical", "clinical_metrics", "medical_history", "antimicrobials"):
        elsewhere |= by_id[pack_id]
    for pack in srv.load_specialty_packs():  # overlap with a specialty pack (e.g. 骨髓炎) is legitimate
        elsewhere |= set(srv.read_pack_words(pack))
    return infectious - elsewhere


class FakeRecognizer:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return [{"text": "患者股骨颈骨折"}]


class TranscribeUsesSelectedSpecialties(unittest.TestCase):
    def transcribe(self, **form):
        recognizer = FakeRecognizer()
        with mock.patch.object(srv, "get_model", return_value=recognizer):
            response = client.post("/transcribe", files={"file": ("a.wav", make_wav(), "audio/wav")},
                                   data={"profile": "accurate", **form})
        self.assertEqual(response.status_code, 200, response.text)
        return recognizer.calls[0].get("hotword", "").split()

    def test_chosen_specialty_and_opt_in_reach_the_model(self):
        words = set(self.transcribe(specialties="orthopedics", include_draft="true"))
        self.assertIn(SPECIALTY_ONLY_WORD, words)
        self.assertEqual(words & words_only_in_infectious_packs(), set())

    def test_drafts_are_not_sent_without_opt_in(self):
        self.assertNotIn(SPECIALTY_ONLY_WORD, self.transcribe(specialties="orthopedics"))

    def test_old_clients_sending_department_still_get_infectious_disease_words(self):
        words = set(self.transcribe(department="infectious_disease"))
        self.assertTrue(words & words_only_in_infectious_packs())

    def test_requests_without_any_selection_use_the_default(self):
        words = set(self.transcribe())
        self.assertTrue(words & words_only_in_infectious_packs())


class SpecialtyEndpoints(unittest.TestCase):
    def test_specialties_endpoint(self):
        body = client.get("/specialties").json()
        ids = [item["id"] for item in body["specialties"]]
        self.assertEqual(ids, srv.SPECIALTY_IDS)
        by_id = {item["id"]: item for item in body["specialties"]}
        self.assertEqual(by_id["orthopedics"]["draft_pack_count"], 2)         # the specialty pack and the essential-medicines pack
        self.assertEqual(by_id["orthopedics"]["reviewed_pack_count"], by_id["orthopedics"]["pack_count"] - 2)
        self.assertEqual(body["default"], srv.DEFAULT_SPECIALTIES)

    def test_pack_listing_reflects_selection_and_opt_in(self):
        def active(**params):
            body = client.get("/hotword-packs", params=params).json()
            return {pack["id"] for pack in body["packs"] if pack["active"]}, body
        ids, body = active(specialties="orthopedics")
        self.assertNotIn("orthopedics", ids)
        self.assertEqual(body["specialties"], ["orthopedics"])
        ids_with_draft, body_with_draft = active(specialties="orthopedics", include_draft="true")
        self.assertIn("orthopedics", ids_with_draft)
        self.assertGreater(body_with_draft["total_count"], body["total_count"])

    def test_editable_hotword_list_is_the_custom_list_only(self):
        body = client.get("/hotwords").json()
        self.assertEqual(body["words"], srv.read_custom_hotwords())
        self.assertLess(body["count"], len(srv.read_hotwords()))

    def test_health_reports_default_specialties(self):
        self.assertEqual(client.get("/health").json()["default_specialties"], srv.DEFAULT_SPECIALTIES)


if __name__ == "__main__":
    unittest.main()

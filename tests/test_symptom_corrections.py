"""Homophone errors in symptom words reported by a doctor: 咯血 heard as 卡血, 血 as 雪."""
import unittest

from server import app as srv


def fix(text):
    return srv.postprocess_transcript(text, "zh-CN", ["respiratory_critical_care"])[0]


class SymptomCorrections(unittest.TestCase):
    def test_the_reported_homophones_are_corrected(self):
        self.assertIn("无咯血", fix("无卡血迹"))
        self.assertIn("痰中带血", fix("痰中带雪"))
        self.assertIn("痰中带血", fix("痰盘中带雪"))
        self.assertIn("咯血", fix("咳嗽伴卡雪"))

    def test_correct_text_is_left_alone(self):
        for text in ("无咯血，痰中带血。", "咳嗽咳痰一月余", "无夜间盗汗", "胸部CT提示肺部感染"):
            self.assertEqual(fix(text).rstrip("。"), text.rstrip("。"), text)

    def test_the_rules_apply_to_every_specialty(self):
        for specialties in (["orthopedics"], ["infectious_disease"], []):
            self.assertIn("咯血", srv.postprocess_transcript("卡血", "zh-CN", specialties)[0])

    def test_the_terms_are_in_the_hotword_list_of_the_respiratory_history_pack(self):
        words = srv.read_words_from_file(srv.HOTWORD_PACK_DIR / "respiratory_history.txt")
        for word in ("痰中带血", "盗汗", "夜间盗汗", "无明显诱因", "咳血"):
            self.assertIn(word, words)


if __name__ == "__main__":
    unittest.main()

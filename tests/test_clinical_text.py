"""Regression tests for clinical text normalization and post-processing.

These pin the *current, reviewed* behaviour of server/app.py so later
refactors cannot silently change what a clinician sees. Cases that expose
known defects, when there are any, are marked expectedFailure with the desired result.
"""
import unittest

from server import app as srv


class NormalizeClinicalText(unittest.TestCase):
    def check(self, source, expected):
        self.assertEqual(srv.normalize_clinical_text(source), expected)

    def test_vital_signs(self):
        self.check("体温三十八点五摄氏度", "体温38.5℃")
        self.check("体温三十九度五", "体温39.5℃")
        self.check("心率一百零二次每分钟", "心率102次/分")
        self.check("血氧饱和度百分之九十五", "血氧饱和度95%")

    def test_blood_pressure_ranges(self):
        self.check("血压一百三十到一百四十，八十到九十毫米汞柱", "血压130-140/80-90mmHg")
        self.check("一百三十到一百四十八十到九十毫米汞柱", "130-140/80-90mmHg")

    def test_lab_values_and_units(self):
        self.check("C反应蛋白五十二毫克每升", "C反应蛋白 52 mg/L")
        self.check("降钙素原零点五纳克每毫升", "降钙素原 0.5 ng/mL")
        self.check("白细胞计数十二点三乘以十的九次方每升", "白细胞计数 12.3×10^9/L")
        self.check("肌酐八十八微摩尔每升", "肌酐 88 μmol/L")
        self.check("D二聚体一点二毫克每升", "D-二聚体 1.2 mg/L")
        self.check("白蛋白三十五克每升", "白蛋白 35 g/L")
        self.check("INR一点二", "INR 1.2")
        self.check("血糖六点八", "血糖 6.8")

    def test_comparison_prefix(self):
        self.check("CRP 大于一百", "CRP >100")

    def test_dosing_frequency_abbreviations(self):
        self.assertTrue(srv.normalize_clinical_text("每日一次").endswith("qd"))
        self.assertTrue(srv.normalize_clinical_text("每日两次").endswith("bid"))
        self.assertTrue(srv.normalize_clinical_text("每八小时一次").endswith("q8h"))

    def test_spoken_punctuation_commands(self):
        self.check("患者无发热句号无胸痛逗号换行咯血", "患者无发热。无胸痛，\n咯血")

    def test_negation_words_are_never_dropped(self):
        for text in ["患者无发热", "否认高血压病史", "未见明显异常", "无胸痛咯血"]:
            out = srv.normalize_clinical_text(text)
            for word in ("无", "否认", "未"):
                if word in text:
                    self.assertIn(word, out)

    def test_idempotent_on_normalized_output(self):
        for text in ["体温38.5℃，血压130/80mmHg", "CRP 52 mg/L", "心率102次/分"]:
            self.assertEqual(srv.normalize_clinical_text(text), text)

    def test_spo2_keeps_its_space_and_is_capitalised(self):
        self.assertEqual(srv.normalize_clinical_text("spo2 92%"), "SpO2 92%")

    def test_spoken_blood_pressure_without_separator(self):
        self.assertEqual(srv.normalize_clinical_text("血压一百二十八十毫米汞柱"), "血压120/80mmHg")


class Numbers(unittest.TestCase):
    def test_parse_chinese_integer(self):
        for text, value in {"十二": 12, "二十五": 25, "一百零二": 102, "一百三": 130, "一百": 100}.items():
            self.assertEqual(srv.parse_chinese_integer(text), value, text)

    def test_spoken_number_to_digits(self):
        self.assertEqual(srv.spoken_number_to_digits("三十八点五"), "38.5")
        self.assertEqual(srv.spoken_number_to_digits("零点五"), "0.5")
        self.assertEqual(srv.spoken_number_to_digits("三十九"), "39")


class CorrectionRules(unittest.TestCase):
    def test_term_corrections_report_what_changed(self):
        text, applied = srv.apply_correction_rules("梅罗培南治疗肺炎克雷伯杆菌感染")
        self.assertEqual(text, "美罗培南治疗肺炎克雷伯菌感染")
        self.assertEqual({item["from"] for item in applied}, {"梅罗培南", "肺炎克雷伯杆菌"})
        self.assertTrue(all(item["count"] >= 1 for item in applied))

    def test_latin_abbreviation_uses_word_boundaries(self):
        text, _ = srv.apply_correction_rules("pct升高")
        self.assertEqual(text, "PCT升高")

    def test_no_change_reports_nothing(self):
        text, applied = srv.apply_correction_rules("患者发热三天")
        self.assertEqual((text, applied), ("患者发热三天", []))

    def test_empty_input(self):
        self.assertEqual(srv.apply_correction_rules(""), ("", []))

    def test_section_headings_are_punctuated(self):
        text, _ = srv.apply_correction_rules("主诉反复咳嗽咳痰3年。加重")
        self.assertEqual(text, "主诉：反复咳嗽、咳痰3年，加重")

    def test_pinned_false_positive_of_hardcoded_cleanup(self):
        # Documents a known over-reach of cleanup_clinical_asr_artifacts:
        # it rewrites unrelated text. Do not treat this as desired behaviour;
        # it is pinned so that the planned move of these rules into data
        # files is a visible, deliberate change.
        text, _ = srv.apply_correction_rules("正常体重下降")
        self.assertEqual(text, "正常，体重下降")


class Punctuation(unittest.TestCase):
    def test_terminal_punctuation(self):
        self.assertEqual(srv.ensure_terminal_punctuation("患者发热"), "患者发热。")
        self.assertEqual(srv.ensure_terminal_punctuation("患者发热。"), "患者发热。")
        self.assertEqual(srv.ensure_terminal_punctuation(""), "")

    def test_pause_punctuation_thresholds(self):
        self.assertEqual(srv.apply_pause_punctuation("患者发热", 300), "患者发热")
        self.assertEqual(srv.apply_pause_punctuation("患者发热", 700), "患者发热，")
        self.assertEqual(srv.apply_pause_punctuation("患者发热", 1500), "患者发热。")
        self.assertEqual(srv.apply_pause_punctuation("患者发热，", 1500), "患者发热。")
        self.assertEqual(srv.apply_pause_punctuation("患者发热。", 1500), "患者发热。")

    def test_filler_only_stream_text_is_dropped(self):
        self.assertEqual(srv.meaningful_stream_text("嗯"), "")
        self.assertEqual(srv.meaningful_stream_text("，"), "")


class English(unittest.TestCase):
    def test_spoken_punctuation(self):
        self.assertEqual(srv.normalize_english_text("patient has fever comma no cough period"), "patient has fever, no cough.")


if __name__ == "__main__":
    unittest.main()

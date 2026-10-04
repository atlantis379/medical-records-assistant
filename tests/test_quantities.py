"""Number + unit rules (docs/CLINICAL_NUMBER_UNIT_RULES.md).

Table-driven: every row is `spoken input -> expected text`. Decisions covered:
D1 attach/space style, D2 convert only next to a known unit or measure name,
D3 ambiguous readings stay unchanged and raise a notice.
"""
import json
import tempfile
import unittest
from pathlib import Path

from server import app as srv
from server.clinical import quantities
from server.clinical.numbers import looks_approximate, parse_cn_number

CONVERTED = [
    # --- blood pressure and other pressures -------------------------------------------------
    ("一百三十毫米汞柱", "130mmHg"),                                   # a single value stays single
    ("血压一百三十毫米汞柱", "血压130mmHg"),
    ("血压一百三十八十毫米汞柱", "血压130/80mmHg"),
    ("血压一百三十比八十", "血压130/80mmHg"),
    ("血压一百三十，八十毫米汞柱", "血压130/80mmHg"),
    ("血压一百三十八十", "血压130/80mmHg"),
    ("血压一百二十八十毫米汞柱", "血压120/80mmHg"),                   # 128|10 is not a possible reading
    ("血压130/80毫米汞柱", "血压130/80mmHg"),
    ("BP 120/80 mmHg", "BP 120/80mmHg"),
    ("一百三十到一百四十八十到九十毫米汞柱", "130-140/80-90mmHg"),
    ("血压一百三十到一百四十，八十到九十毫米汞柱", "血压130-140/80-90mmHg"),
    ("一百六一百毫米汞柱", "160/100mmHg"),
    ("收缩压一百三十舒张压八十", "收缩压130舒张压80"),
    ("二氧化碳分压五十毫米汞柱", "二氧化碳分压 50mmHg"),
    ("PaO2 60 mmHg", "PaO2 60mmHg"),
    ("呼气末正压五厘米水柱", "呼气末正压 5 cmH2O"),
    # --- vital signs ---------------------------------------------------------------------
    ("心率每分钟九十次", "心率90次/分"),
    ("呼吸二十次每分", "呼吸20次/分"),
    ("心率九十", "心率90"),
    ("体温三十八度五", "体温38.5℃"),
    ("血氧饱和度九十五", "血氧饱和度95%"),
    ("血氧九十二", "血氧92%"),
    ("吸入氧浓度百分之四十", "吸入氧浓度 40%"),
    ("FEV1占预计值百分之六十五", "FEV1占预计值65%"),
    ("氧饱和度百分之九十", "氧饱和度90%"),
    # --- respiratory / ventilator -----------------------------------------------------
    ("潮气量四百五十毫升", "潮气量 450 mL"),
    ("氧流量每分钟三升", "氧流量 3 L/min"),
    ("PEEP五", "PEEP 5"),
    ("氧合指数二百五十", "氧合指数 250"),
    ("第一秒用力呼气容积二点三升", "第一秒用力呼气容积 2.3 L"),
    ("尿量每小时五十毫升", "尿量 50 mL/h"),
    # --- laboratory ------------------------------------------------------------------------
    ("血钾三点五毫摩尔每升", "血钾 3.5 mmol/L"),
    ("肌酐一百二十微摩尔每升", "肌酐 120 μmol/L"),
    ("血红蛋白九十克每升", "血红蛋白 90 g/L"),
    ("白细胞十二点三乘十的九次方每升", "白细胞 12.3×10^9/L"),
    ("D二聚体两千", "D-二聚体 2000"),
    ("血糖十二点五毫摩尔每升", "血糖 12.5 mmol/L"),
    # --- body measures, sizes, bones ---------------------------------------------------------
    ("体重六十五公斤", "体重 65 kg"),
    ("体重六十五千克身高一百七十厘米", "体重 65 kg身高 170 cm"),
    ("体重指数二十四点五", "体重指数 24.5"),
    ("骨密度T值负二点五", "骨密度T值 -2.5"),
    ("结节直径八毫米", "结节直径 8 mm"),
    ("肿块大小三乘二厘米", "肿块大小 3×2 cm"),
    ("关节活动度屈曲九十度", "关节活动度屈曲90°"),
    ("膝关节屈曲一百二十度伸直零度", "膝关节屈曲120°伸直0°"),
    ("腰椎间盘突出症，直腿抬高试验七十度阳性", "腰椎间盘突出症，直腿抬高试验70°阳性"),
    # --- doses and frequencies -------------------------------------------------------------
    ("氨溴索三十毫克每日三次", "氨溴索 30 mg tid"),
    ("美罗培南一克每八小时一次", "美罗培南 1 g q8h"),
    ("布洛芬缓释胶囊零点三克每日两次", "布洛芬缓释胶囊 0.3 g bid"),
    ("二十毫克", "20 mg"),
    ("一百零五毫克", "105 mg"),
    ("一百五毫克", "150 mg"),
    ("每日三次口服", "tid口服"),
]

LEFT_AS_SPOKEN = [
    # D2: not next to a known unit or measure name
    "肌力四级", "肌力四加级", "VAS评分五分", "左侧肢体肌力三级", "三度房室传导阻滞", "心功能三级",
    # approximations and fixed words keep their Chinese numerals (GB/T 15835)
    "三四天", "一两周发热", "十二指肠溃疡", "二尖瓣关闭不全",
    # time and counting words are not processed
    "既往高血压五年", "低血压三天", "咳嗽三天", "体重三个月", "尿量三天",
    # a colloquial height is not guessed
    "身高一米七",
    # 克 inside a drug or organism name is not a unit
    "一克雷伯菌", "三升高",
]


APPROXIMATE_WITH_UNIT = [
    # the number stays as spoken (GB/T 15835); the unit is still written in symbol form
    ("几十毫克", "几十 mg"), ("三四十毫克", "三四十 mg"), ("一两毫克", "一两 mg"),
]


class QuantityTable(unittest.TestCase):
    def test_converted(self):
        for spoken, expected in CONVERTED:
            with self.subTest(spoken=spoken):
                self.assertEqual(srv.normalize_clinical_text(spoken), expected)

    def test_approximate_numbers_keep_their_chinese_numerals(self):
        for spoken, expected in APPROXIMATE_WITH_UNIT:
            with self.subTest(spoken=spoken):
                self.assertEqual(srv.normalize_clinical_text(spoken), expected)

    def test_left_as_spoken(self):
        for spoken in LEFT_AS_SPOKEN:
            with self.subTest(spoken=spoken):
                self.assertEqual(srv.normalize_clinical_text(spoken), spoken)

    def test_results_are_stable_when_normalised_again(self):
        for spoken, expected in CONVERTED:
            with self.subTest(spoken=spoken):
                self.assertEqual(srv.normalize_clinical_text(expected), expected)


class UnitStyle(unittest.TestCase):
    """D1: ℃ % 次/分 mmHg hug the number; every other unit is separated by one space."""

    def test_attached_units(self):
        for text in ["38.5℃", "95%", "90次/分", "130mmHg", "12.3×10^9/L"]:
            self.assertEqual(srv.normalize_clinical_text(text), text)

    def test_spaced_units_get_one_space(self):
        for spoken, expected in [("三十毫克", "30 mg"), ("30mg", "30 mg"), ("五十二毫克每升", "52 mg/L"),
                                 ("30 mg", "30 mg"), ("3.5毫摩尔每升", "3.5 mmol/L")]:
            self.assertEqual(srv.normalize_clinical_text(spoken), expected)

    def test_a_dose_after_a_drug_name_is_separated(self):
        self.assertEqual(srv.normalize_clinical_text("给予头孢曲松二克每日一次"), "给予头孢曲松 2 g qd")

    def test_frequency_is_not_glued_to_the_unit(self):
        self.assertNotIn("mgtid", srv.normalize_clinical_text("三十毫克每日三次"))


class Ambiguity(unittest.TestCase):
    """D3: unclear readings are left unchanged and reported."""

    def notes(self, text):
        return srv.normalize_clinical_text_with_notes(text)

    def test_unresolvable_blood_pressure_is_reported_and_not_guessed(self):
        text, notes = self.notes("体温37，一百三十八十毫米汞柱")
        self.assertIn("一百三十八十", text)
        self.assertEqual([n["code"] for n in notes], ["bp_unresolved"])
        self.assertIn("一百三十八十", notes[0]["source"])

    def test_three_blood_pressure_numbers_are_not_forced_into_two(self):
        text, notes = self.notes("血压一百三十八十九十毫米汞柱")
        self.assertIn("一百三十八十九十", text)
        self.assertTrue(notes)

    def test_malformed_number_before_a_unit_is_reported(self):
        text, notes = self.notes("一百百毫克")
        self.assertIn("一百百", text)
        self.assertEqual([n["code"] for n in notes], ["number_unparsed"])

    def test_malformed_number_after_a_measure_name_is_reported(self):
        text, notes = self.notes("血氧九五")
        self.assertIn("九五", text)
        self.assertTrue(notes)

    def test_approximations_are_not_reported(self):
        for text in ["三四十毫克", "几十毫克", "一两毫克"]:
            self.assertEqual(self.notes(text)[1], [], text)

    def test_clear_input_has_no_notices(self):
        for spoken, _ in CONVERTED:
            self.assertEqual(self.notes(spoken)[1], [], spoken)


class BloodPressureSolver(unittest.TestCase):
    def solve(self, span):
        return quantities.solve_blood_pressure(span, quantities.get_registry())

    def test_unique_split_is_accepted(self):
        self.assertEqual(self.solve("一百三十八十"), ("ok", "130/80"))
        self.assertEqual(self.solve("一百二十八十"), ("ok", "120/80"))
        self.assertEqual(self.solve("一百四十九十"), ("ok", "140/90"))
        self.assertEqual(self.solve("13080"), ("ok", "130/80"))

    def test_single_value_is_not_split(self):
        self.assertEqual(self.solve("一百三十"), ("single", "130"))      # not 100/30
        self.assertEqual(self.solve("130"), ("single", "130"))

    def test_explicit_separators(self):
        self.assertEqual(self.solve("130/80"), ("ok", "130/80"))
        self.assertEqual(self.solve("一百三十比八十"), ("ok", "130/80"))
        self.assertEqual(self.solve("130-140/80-90"), ("ok", "130-140/80-90"))

    def test_diastolic_must_be_lower(self):
        self.assertEqual(self.solve("八十/一百三十")[0], "fail")

    def test_ranges_use_the_configured_bounds(self):
        reg = quantities.get_registry()
        self.assertEqual(reg.bp_systolic, (50, 300))
        self.assertEqual(reg.bp_diastolic, (20, 200))


class ChineseNumbers(unittest.TestCase):
    def test_conversions(self):
        table = {"十": "10", "十二": "12", "二十五": "25", "一百零二": "102", "一百二": "120", "一百三十": "130",
                 "两百五": "250", "两千": "2000", "一千零五": "1005", "三千五": "3500", "一万五": "15000",
                 "零点五": "0.5", "三十八点五": "38.5", "负二点五": "-2.5", "一零二": "102", "两万五千": "25000"}
        for spoken, expected in table.items():
            self.assertEqual(parse_cn_number(spoken), expected, spoken)

    def test_refusals(self):
        for spoken in ["三四", "一两", "七八十", "九五", "百", "十十", "一百百", "点五", "二点", "零五", "几十"]:
            self.assertIsNone(parse_cn_number(spoken), spoken)

    def test_approximation_detector(self):
        self.assertTrue(looks_approximate("三四十"))
        self.assertTrue(looks_approximate("几十"))
        self.assertFalse(looks_approximate("三十八点五"))
        self.assertFalse(looks_approximate("一百百"))


class DataDrivenRules(unittest.TestCase):
    def test_every_unit_has_a_known_style_and_a_spoken_form(self):
        data = json.loads((quantities.DATA_DIR / "units.json").read_text(encoding="utf-8"))
        for unit in data["units"]:
            self.assertIn(unit["style"], {"attach", "space"}, unit["symbol"])
            self.assertTrue(unit["spoken"], unit["symbol"])

    def test_attached_units_are_exactly_the_decided_set(self):
        data = json.loads((quantities.DATA_DIR / "units.json").read_text(encoding="utf-8"))
        attached = {unit["symbol"] for unit in data["units"] if unit["style"] == "attach"}
        self.assertEqual(attached, {"mmHg", "℃", "次/分", "×10^9/L", "×10^12/L"})

    def test_no_spoken_form_is_claimed_by_two_units(self):
        data = json.loads((quantities.DATA_DIR / "units.json").read_text(encoding="utf-8"))
        seen = {}
        for unit in data["units"]:
            for spoken in unit["spoken"]:
                key = spoken.casefold()
                if key in seen:
                    self.assertEqual(seen[key], unit["symbol"], f"{spoken!r} is claimed by {seen[key]} and {unit['symbol']}")
                seen[key] = unit["symbol"]

    def test_measure_names_do_not_overlap_the_legacy_lab_list(self):
        data = json.loads((quantities.DATA_DIR / "measures.json").read_text(encoding="utf-8"))
        legacy = set(srv.CLINICAL_METRIC_NAMES)
        self.assertEqual({item["name"] for item in data["names"]} & legacy, set())

    def test_a_new_unit_needs_only_a_data_change(self):
        source = json.loads((quantities.DATA_DIR / "units.json").read_text(encoding="utf-8"))
        source["units"].append({"symbol": "mEq/L", "style": "space", "spoken": ["毫当量每升"]})
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "units.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
            (tmp_path / "measures.json").write_text((quantities.DATA_DIR / "measures.json").read_text(encoding="utf-8"), encoding="utf-8")
            registry = quantities.Registry(tmp_path)
            self.assertEqual(quantities.normalize_quantities("血钾四毫当量每升", registry)[0], "血钾 4 mEq/L")
            self.assertEqual(quantities.normalize_quantities("血钾四毫当量每升", quantities.get_registry())[0], "血钾四毫当量每升")



class NoticesReachTheClient(unittest.TestCase):
    """The service reports unresolved numbers in /transcribe and in the streaming final message."""

    def setUp(self):
        from fastapi.testclient import TestClient
        self.client = TestClient(srv.app, headers={"Origin": "http://testserver"})

    def wav(self):
        import io
        import math
        import struct
        import wave
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(16000)))
        return buffer.getvalue()

    def transcribe(self, spoken):
        from unittest import mock

        class Fake:
            def generate(self, **kwargs):
                return [{"text": spoken}]

        with mock.patch.object(srv, "get_model", return_value=Fake()):
            response = self.client.post("/transcribe", files={"file": ("a.wav", self.wav(), "audio/wav")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_batch_response_carries_notices_for_ambiguous_numbers(self):
        body = self.transcribe("体温37，一百三十八十毫米汞柱")
        self.assertEqual([n["code"] for n in body["number_notices"]], ["bp_unresolved"])
        self.assertIn("一百三十八十", body["text"])  # left as spoken, never guessed

    def test_batch_response_is_clean_for_clear_speech(self):
        body = self.transcribe("血压一百三十八十毫米汞柱")
        self.assertEqual(body["number_notices"], [])
        self.assertEqual(body["text"], "血压130/80mmHg。")

    def test_streaming_final_message_carries_notices(self):
        from unittest import mock

        class FakeStreaming:
            def __init__(self):
                self.calls = 0

            def generate(self, **kwargs):
                self.calls += 1
                return [{"text": "体温37，一百三十八十毫米汞柱"}] if self.calls == 1 else []

        import numpy as np
        samples = (np.sin(np.arange(3200) / 20) * 8000).astype("<i2").tobytes()
        with mock.patch.object(srv, "get_streaming_model", return_value=FakeStreaming()):
            with self.client.websocket_connect("/ws/transcribe", headers={"Origin": "http://testserver"}) as ws:
                ws.send_json({"specialties": "infectious_disease", "language": "zh-CN", "profile": "fast"})
                kinds = []
                while "ready" not in kinds:
                    kinds.append(ws.receive_json()["type"])
                ws.send_bytes(samples)
                ws.send_json({"type": "end"})
                final = None
                for _ in range(10):
                    message = ws.receive_json()
                    if message["type"] == "final":
                        final = message
                        break
        self.assertIsNotNone(final)
        self.assertEqual([n["code"] for n in final["number_notices"]], ["bp_unresolved"])
        self.assertIn("一百三十八十", final["text"])


if __name__ == "__main__":
    unittest.main()

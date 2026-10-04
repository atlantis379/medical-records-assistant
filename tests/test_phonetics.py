"""Sound-alike correction: replaces only long, unambiguous terms; everything else is a suggestion; ordinary text is untouched."""
import unittest
from unittest import mock

from server import app as srv
from server.clinical import phonetics

SPEC = ["respiratory_critical_care"]

ORDINARY = [
    "双肺呼吸音粗，可闻及少量湿啰音，未闻及干啰音及哮鸣音。",
    "专科检查，左髋部压痛，叩击痛阳性，左下肢外旋畸形，纵向叩击痛阳性。",
    "主诉右膝关节疼痛三天伴活动受限无发热无红肿现病史患者三天前爬山后出现右膝疼痛",
    "既往体健否认高血压糖尿病冠心病史否认肝炎结核等传染病史否认外伤手术史否认药物食物过敏史",
    "查体神志清楚精神可双肺呼吸音清未闻及干湿啰音心率八十次每分律齐腹软无压痛",
    "辅助检查血常规白细胞十二点五血红蛋白一百二十肝功能正常肾功能肌酐八十",
    "诊断社区获得性肺炎高血压病二型糖尿病予抗感染平喘化痰对症治疗",
    "腰椎间盘突出症L4/5棘突间压痛直腿抬高试验阳性双下肢肌力五级",
    "患者主因咳嗽咳痰一月余入院患者自述一月前无明显诱因出现咳嗽咳痰伴胸闷气促活动后喘息感呼吸困难无畏寒发热无夜间盗汗",
    "咽痛咳嗽三天体温三十八度二查体咽充血扁桃体二度肿大予对乙酰氨基酚",
    "腹痛腹泻两天大便呈黄色稀水样无黏液脓血口服蒙脱石散和左氧氟沙星",
]


def fix(text):
    return srv.postprocess_transcript(text, "zh-CN", SPEC, phonetic=True)


@unittest.skipUnless(phonetics.available(), "pypinyin is not installed")
class Replacements(unittest.TestCase):
    def test_long_terms_with_the_same_pinyin_are_replaced_and_reported(self):
        text, _, corrections, _ = fix("患者给予头包曲松静脉滴注，服用阿奇梅素")
        self.assertIn("头孢曲松", text)
        self.assertIn("阿奇霉素", text)
        found = {(c["from"], c["to"]) for c in corrections if c["category"] == "phonetic"}
        self.assertEqual(found, {("头包曲松", "头孢曲松"), ("阿奇梅素", "阿奇霉素")})

    def test_short_terms_are_only_suggested(self):
        text = fix("吃了点安茶碱")[0]
        self.assertIn("安茶碱", text)                                           # not changed
        suggestions = srv.phonetic_suggestions(text)
        self.assertEqual([(s["from"], s["options"]) for s in suggestions], [("安茶碱", ["氨茶碱"])])

    def test_similar_sounds_are_only_suggested(self):
        text = fix("口服布诺芬片")[0]
        self.assertIn("布诺芬", text)
        self.assertEqual(srv.phonetic_suggestions(text)[0]["options"], ["布洛芬"])

    def test_it_is_off_unless_asked_for(self):
        self.assertIn("头包曲松", srv.postprocess_transcript("头包曲松", "zh-CN", SPEC)[0])

    def test_ordinary_clinical_text_is_left_alone(self):
        for sentence in ORDINARY:
            text = fix(sentence)[0]
            plain = srv.postprocess_transcript(sentence, "zh-CN", SPEC)[0]
            self.assertEqual(text, plain, sentence)
            self.assertEqual(srv.phonetic_suggestions(text), [], sentence)

    def test_terms_already_written_correctly_are_kept(self):
        text = fix("给予头孢曲松和阿莫西林克拉维酸钾")[0]
        self.assertIn("头孢曲松", text)
        self.assertIn("阿莫西林克拉维酸钾", text)
        self.assertEqual(srv.phonetic_suggestions(text), [])


@unittest.skipUnless(phonetics.available(), "pypinyin is not installed")
class Ambiguity(unittest.TestCase):
    def test_two_terms_that_sound_alike_are_never_chosen_for_the_doctor(self):
        index = phonetics.Index.build({"阿莫西林", "阿莫昔林"})
        replacements, suggestions = phonetics.scan("给予阿默西林", index)
        self.assertEqual(replacements, [])
        self.assertEqual(suggestions[0]["options"], ["阿莫昔林", "阿莫西林"])

    def test_a_window_that_is_an_ordinary_word_is_not_touched(self):
        index = phonetics.Index.build({"头孢曲松"})
        self.assertEqual(phonetics.scan("今天天气很好", index), ([], []))

    def test_overlapping_findings_do_not_collide(self):
        index = phonetics.Index.build({"头孢曲松", "头孢他啶"})
        replacements, _ = phonetics.scan("头包曲松和头包他定", index)
        self.assertEqual(phonetics.apply("头包曲松和头包他定", replacements), "头孢曲松和头孢他啶")

    def test_without_pypinyin_nothing_happens(self):
        with mock.patch.object(phonetics, "lazy_pinyin", None):
            self.assertEqual(phonetics.scan("头包曲松", phonetics.Index()), ([], []))
            self.assertFalse(phonetics.available())


@unittest.skipUnless(phonetics.available(), "pypinyin is not installed")
class Lexicon(unittest.TestCase):
    def test_the_catalogue_is_the_main_source(self):
        words = srv.phonetic_index().words
        for drug in ("头孢曲松", "硝苯地平", "布洛芬", "氨茶碱"):
            self.assertIn(drug, words)

    def test_draft_pack_words_are_not_used(self):
        index = srv.phonetic_index()
        draft_only = set()
        for pack in srv.all_packs():
            if pack.get("status") == "draft":
                draft_only |= set(srv.read_pack_words(pack))
        released = set()
        for pack in srv.all_packs():
            if pack.get("status", "released") != "draft":
                released |= set(srv.read_custom_hotwords() if pack["id"] == "user_custom" else srv.read_pack_words(pack))
        released |= set(srv.essential_drug_names())
        only_in_drafts = {w for w in draft_only - released if phonetics.CJK_WORD.fullmatch(w)}
        self.assertTrue(only_in_drafts, "the test needs words that exist only in draft packs")
        self.assertFalse(only_in_drafts & index.words)

    def test_the_doctors_own_hotwords_are_included(self):
        with mock.patch.object(srv, "read_custom_hotwords", lambda: ["奇特新药名"]):
            self.assertIn("奇特新药名", srv.phonetic_index().words)

    def test_the_index_is_cached_until_something_changes(self):
        first = srv.phonetic_index()
        self.assertIs(srv.phonetic_index(), first)


@unittest.skipUnless(phonetics.available(), "pypinyin is not installed")
class Endpoint(unittest.TestCase):
    def test_transcribe_applies_the_replacements_and_returns_the_suggestions(self):
        from fastapi.testclient import TestClient
        from tests.test_models_offline import tone_wav

        class Fake:
            def generate(self, **kwargs):
                return [{"text": "口服布诺芬片给予头包曲松"}]

        with mock.patch.object(srv, "get_model", lambda: Fake()):
            reply = TestClient(srv.app, headers={"Origin": "http://testserver"}).post(
                "/transcribe", data={"language": "zh-CN"}, files={"file": ("a.wav", tone_wav(), "audio/wav")})
        self.assertEqual(reply.status_code, 200, reply.text)
        body = reply.json()
        self.assertIn("头孢曲松", body["text"])
        self.assertIn("布诺芬", body["text"])                                       # only suggested
        self.assertEqual([(s["from"], s["options"]) for s in body["phonetic_suggestions"]], [("布诺芬", ["布洛芬"])])
        self.assertIn(("头包曲松", "头孢曲松"), {(c["from"], c["to"]) for c in body["corrections"] if c["category"] == "phonetic"})
        self.assertIn("头包曲松", body["raw_text"])                                  # the raw text is kept for comparison


class Page(unittest.TestCase):
    def test_the_page_has_the_dropdown_the_suggestions_and_the_add_button(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "extension"
        html, script = (root / "editor.html").read_text(encoding="utf-8"), (root / "editor.js").read_text(encoding="utf-8")
        for needed in ('id="addHotwordButton"', 'id="phoneticBox"', 'id="phoneticList"', 'id="specialtyList"'):
            self.assertIn(needed, html)
        for needed in ('select.id = "specialtySelect"', "registerPhoneticSuggestions", "applyPhoneticSuggestion", "addSelectedWordToHotwords",
                       "phonetic_suggestions"):
            self.assertIn(needed, script)
        self.assertNotIn('input.type = "checkbox"; input.value = item.id', script)       # the checkbox list is gone


if __name__ == "__main__":
    unittest.main()

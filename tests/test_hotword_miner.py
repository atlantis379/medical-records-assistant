"""Hotword extraction from the hospital's own cases (web dialog + local service). Synthetic cases only.

Cases stay on this computer and in memory; only words and counts come back; the pack contains words only.
Nothing here starts a browser: the service is called through FastAPI's test client.
"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

try:
    import jieba  # noqa: F401
    HAVE_JIEBA = True
except ImportError:
    HAVE_JIEBA = False

from fastapi.testclient import TestClient

from server import app as srv
from server import miner_api
from server.hotword_miner import output, pipeline, privacy, readers
from tests import hotword_miner_corpus as corpus

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


class Readers(unittest.TestCase):
    def test_docx_text_and_table_cells_are_read(self):
        self.assertEqual(readers.read_docx(corpus.docx_bytes("主诉：咳嗽\n诊断：肺栓塞")), "主诉：咳嗽\n诊断：肺栓塞")

    def test_gbk_text_files_are_read(self):
        self.assertEqual(readers.read_txt("主诉：咳嗽".encode("gb18030")), "主诉：咳嗽")

    def test_unusable_files_say_why(self):
        for name, raw, word in (("old.doc", b"\xd0\xcf", "docx"), ("broken.docx", b"not a zip", "docx"), ("a.pdf", b"%PDF", "txt"),
                                ("~$lock.docx", b"x", "临时")):
            with self.assertRaises(ValueError) as caught:
                readers.read_upload(name, raw)
            self.assertIn(word, str(caught.exception), name)

    def test_one_file_with_many_cases_can_be_split_without_a_pattern_language(self):
        text = "姓名：甲\n咳嗽\n姓名：乙\n发热\n姓名：丙\n胸痛"
        self.assertEqual(len(readers.split_cases(text, "姓名")), 3)
        self.assertEqual(len(readers.split_cases(text, None)), 1)
        self.assertEqual(len(readers.split_cases("a(b\n", "(.*+")), 1)       # regex characters are plain text


class Privacy(unittest.TestCase):
    def test_names_after_labels_are_found_but_ordinary_text_is_not(self):
        names = privacy.harvest_names("姓名：张建国\n患者李秀英因咳嗽就诊\n主治医师：王明华\n患者因咳嗽3天就诊\n患者男，65岁")
        self.assertTrue({"张建国", "李秀英", "王明华"} <= names)
        self.assertFalse({"因咳嗽", "男"} & names)

    def test_numbers_phones_and_ids_are_masked(self):
        masked = privacy.mask("电话13812345678，身份证110101199001011234，邮箱a@b.com", set())
        self.assertNotRegex(masked, r"\d{6,}")
        self.assertNotIn("@", masked)

    def test_record_numbers_are_not_latin_words(self):
        self.assertTrue(all(privacy.acceptable_latin(t) for t in ("CT", "COPD", "PaO2", "CA125", "HbA1c")))
        self.assertFalse(any(privacy.acceptable_latin(t) for t in ("A123456", "X", "B20230101")))

    @unittest.skipUnless(HAVE_JIEBA, "needs jieba")
    def test_place_and_organisation_names_are_dropped_but_drug_and_disease_names_are_kept(self):
        import jieba.posseg as posseg
        self.assertIsNotNone(privacy.looks_like_name("兰坪县", posseg.cut))
        self.assertIsNotNone(privacy.looks_like_name("兰坪县人民医院", posseg.cut))
        for word in ("奥马珠单抗", "马凡综合征", "肺间质纤维化"):
            self.assertIsNone(privacy.looks_like_name(word, posseg.cut), word)


class Words(unittest.TestCase):
    def test_words_that_look_like_identifiers_are_refused(self):
        for word in ("13812345678", "A123456", "张 三", "a", "x" * 31, "肺<栓塞"):
            self.assertIsNotNone(output.problem_with(word), word)
        for word in ("肺栓塞", "CT", "PaO2", "CA125"):
            self.assertIsNone(output.problem_with(word), word)


@unittest.skipUnless(HAVE_JIEBA, "needs jieba")
class Service(unittest.TestCase):
    """The upload → scan → build flow through the real routes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.packs = Path(cls.tmp.name) / "packs"
        cls.packs.mkdir()
        cls.patches = [mock.patch.object(srv, "HOTWORD_PACK_DIR", cls.packs),
                       mock.patch.object(srv, "known_hotword_words", lambda: set())]
        for patch in cls.patches:
            patch.start()
        cls.client = TestClient(srv.app, headers={"Origin": "http://testserver"})
        cls.cases = corpus.make_corpus(80)
        cls.files = corpus.case_files(cls.cases) + [("legacy.doc", b"\xd0\xcf\x11\xe0"), ("broken.docx", b"not a zip"), ("notes.pdf", b"%PDF")]

    @classmethod
    def tearDownClass(cls):
        for patch in cls.patches:
            patch.stop()
        pipeline.store.clear()
        cls.tmp.cleanup()

    def setUp(self):
        for path in self.packs.iterdir():
            path.unlink()

    def upload(self, files=None, session=None, batch=30):
        files = self.files if files is None else files
        result = None
        for i in range(0, len(files), batch):
            data = {"session": session} if session else {}
            response = self.client.post("/hotword-miner/upload", data=data, files=[("files", (n, b)) for n, b in files[i:i + batch]])
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            session = result["session"]
        return result

    def scan(self, session, **options):
        return self.client.post("/hotword-miner/scan", json={"session": session, **options})

    def scanned(self, **options):
        session = self.upload()["session"]
        response = self.scan(session, **options)
        self.assertEqual(response.status_code, 200, response.text)
        return session, response.json()

    # ------------------------------------------------------------------ upload
    def test_upload_reports_what_it_could_not_read(self):
        result = self.upload()
        self.assertEqual(result["files"], 80)
        reasons = {item["name"]: item["reason"] for item in result["skipped"]}
        self.assertIn("docx", reasons["legacy.doc"])
        self.assertIn("broken.docx", reasons)
        self.assertEqual(result["skipped_count"], 3)

    def test_too_many_files_in_one_request_are_refused(self):
        response = self.client.post("/hotword-miner/upload", files=[("files", (f"{i}.txt", b"x")) for i in range(miner_api.MAX_REQUEST_FILES + 1)])
        self.assertEqual(response.status_code, 413)

    def test_a_large_case_file_is_not_spooled_to_a_temporary_file_on_disk(self):
        big = ("主诉：咳嗽咳痰\n" * 200_000).encode("utf-8")           # about 3 MB
        self.assertGreater(len(big), 2 * 1024 * 1024)
        with mock.patch("tempfile.SpooledTemporaryFile.rollover", side_effect=AssertionError("rolled over to disk")):
            response = self.client.post("/hotword-miner/upload", files=[("files", ("big.txt", big))])
        self.assertEqual(response.status_code, 200, response.text)

    def test_the_total_size_is_limited(self):
        with mock.patch.object(pipeline, "MAX_SESSION_BYTES", 100):
            response = self.client.post("/hotword-miner/upload", files=[("files", ("a.txt", "咳嗽".encode() * 100))])
        self.assertEqual(response.status_code, 413)
        self.assertFalse(pipeline.store.active(), "a refused session must not stay in memory")

    # ------------------------------------------------------------------ scan
    def test_terms_in_many_cases_are_found_and_nothing_identifying_is_returned(self):
        _, result = self.scanned()
        words = {row["word"] for row in result["candidates"]}
        self.assertEqual([t for t in corpus.TERMS if t not in words], [], sorted(words))
        blob = json.dumps(result, ensure_ascii=False)
        for name in corpus.NAMES:
            self.assertNotIn(name, blob)
        self.assertNotIn(corpus.HOSPITAL_PLACE[:3], blob)
        self.assertNotRegex(blob, r"\d{6,}")
        self.assertGreater(result["names_removed"], 0)

    def test_a_word_in_too_few_cases_is_never_listed(self):
        _, result = self.scanned()
        words = {row["word"] for row in result["candidates"]}
        self.assertNotIn(corpus.RARE, words)
        self.assertNotIn(corpus.BELOW, words)               # in 4 cases, the threshold is 5
        self.assertFalse([w for w in words if corpus.RARE[:5] in w or corpus.BELOW[:3] in w])

    def test_the_threshold_can_be_lowered_but_not_below_three(self):
        session = self.upload()["session"]
        self.assertEqual(self.scan(session, min_cases=2).status_code, 400)
        self.assertEqual(self.scan(session, min_cases=0).status_code, 422)
        lowered = self.scan(session, min_cases=3).json()
        self.assertIn(corpus.BELOW, {row["word"] for row in lowered["candidates"]})

    def test_no_sentence_of_a_case_is_returned(self):
        _, result = self.scanned()
        blob = json.dumps(result, ensure_ascii=False)
        self.assertLessEqual(max(len(row["word"]) for row in result["candidates"]), 10)
        for case in self.cases[:10]:
            for line in case.splitlines():
                self.assertNotIn(line[:12], blob)

    def test_nothing_is_written_to_disk_by_upload_and_scan(self):
        before = set(Path(self.tmp.name).rglob("*"))
        self.scanned()
        self.assertEqual(set(Path(self.tmp.name).rglob("*")), before)
        self.assertEqual(list(self.packs.iterdir()), [])

    def test_words_already_in_the_packs_are_not_offered(self):
        session = self.upload()["session"]
        with mock.patch.object(srv, "known_hotword_words", lambda: {"肺栓塞", "ct"}):
            words = {row["word"] for row in self.scan(session).json()["candidates"]}
        self.assertNotIn("肺栓塞", words)
        self.assertNotIn("CT", words)
        self.assertIn("胸腔积液", words)

    def test_too_few_cases_stop_with_a_message(self):
        session = self.upload(corpus.case_files(self.cases[:2]))["session"]
        response = self.scan(session)
        self.assertEqual(response.status_code, 400)
        self.assertIn("少于门槛", response.json()["detail"])

    def test_rows_carry_the_counts_and_the_heading(self):
        _, result = self.scanned()
        row = next(r for r in result["candidates"] if r["word"] == "奥马珠单抗")
        self.assertGreaterEqual(row["cases"], 5)
        self.assertGreaterEqual(row["count"], row["cases"])
        self.assertIn(row["field"], {"辅助检查", "诊断", "处理"})

    # ------------------------------------------------------------------ sessions
    def test_unknown_cleared_and_expired_sessions_are_gone(self):
        self.assertEqual(self.scan("nope").status_code, 404)
        session = self.upload()["session"]
        self.assertEqual(self.client.post("/hotword-miner/clear", json={"session": session}).json(), {"cleared": True})
        self.assertEqual(self.scan(session).status_code, 404)
        session = self.upload()["session"]
        with mock.patch.object(pipeline.time, "time", return_value=time.time() + pipeline.SESSION_TTL + 5):
            self.assertEqual(self.scan(session).status_code, 404)

    def test_a_new_upload_wipes_the_previous_cases(self):
        first = self.upload()["session"]
        second = self.upload(corpus.case_files(self.cases[:10]))["session"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.scan(first).status_code, 404)

    def test_a_new_batch_invalidates_an_earlier_result(self):
        session, _ = self.scanned()
        self.upload(corpus.case_files(self.cases[:5]), session=session)
        self.assertIsNone(pipeline.store.get(session).scan)

    # ------------------------------------------------------------------ build
    def build(self, session, words, **extra):
        body = {"session": session, "words": words, "specialty": "respiratory_critical_care", "label": "本院呼吸科词库", **extra}
        return self.client.post("/hotword-miner/build", json=body)

    def test_only_chosen_words_enter_the_pack_and_it_starts_as_a_draft(self):
        session, _ = self.scanned()
        response = self.build(session, ["肺间质纤维化", "磨玻璃影", "肺间质纤维化"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "hospital_respiratory_critical_care")
        self.assertEqual((response.json()["count"], response.json()["status"]), (2, "draft"))
        lines = (self.packs / "hospital_respiratory_critical_care.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual([l for l in lines if l and not l.startswith("#")], ["肺间质纤维化", "磨玻璃影"])
        manifest = json.loads((self.packs / "hospital_respiratory_critical_care.manifest.json").read_text(encoding="utf-8"))
        self.assertFalse({"id", "specialty", "label", "label_en", "filename", "language", "version", "status", "compiled_on",
                          "compile_method", "sources", "reviewers", "reviewed_on"} - set(manifest))
        pack = next(p for p in srv.all_packs() if p["id"] == "hospital_respiratory_critical_care")
        self.assertEqual(pack["status"], "draft")
        self.assertFalse(srv.pack_is_active(pack, ["respiratory_critical_care"], False), "a draft must stay off by default")
        self.assertTrue(srv.pack_is_active(pack, ["respiratory_critical_care"], True))
        self.assertIn("磨玻璃影", srv.read_pack_words(pack))

    def test_a_named_reviewer_makes_it_a_reviewed_pack(self):
        session, _ = self.scanned()
        response = self.build(session, ["磨玻璃影"], reviewers=["某医生"], pack_id="ward_a")
        self.assertEqual(response.json()["status"], "reviewed")
        pack = next(p for p in srv.all_packs() if p["id"] == "hospital_ward_a")
        self.assertTrue(srv.pack_is_active(pack, ["respiratory_critical_care"], False))

    def test_words_that_were_not_found_in_the_scan_are_refused(self):
        session, _ = self.scanned()
        for bad in ("没有出现过的词", corpus.RARE):
            response = self.build(session, ["磨玻璃影", bad])
            self.assertEqual(response.status_code, 400, bad)
        self.assertEqual(list(self.packs.iterdir()), [], "nothing is written when any word is refused")

    def test_build_input_is_checked(self):
        session, _ = self.scanned()
        self.assertEqual(self.build(session, ["磨玻璃影"], specialty="nonsense").status_code, 400)
        self.assertEqual(self.build(session, []).status_code, 400)
        self.assertEqual(self.build(session, ["磨玻璃影"], pack_id="../evil").status_code, 400)
        self.assertEqual(self.build(session, ["磨玻璃影"], pack_id="Has Space").status_code, 400)
        self.assertEqual(self.build("nope", ["磨玻璃影"]).status_code, 404)
        self.assertEqual(list(self.packs.iterdir()), [])

    def test_the_label_stays_on_one_line(self):
        session, _ = self.scanned()
        self.build(session, ["磨玻璃影"], label="词库\n# 插入的行")
        text = (self.packs / "hospital_respiratory_critical_care.txt").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[0].count("#"), 2)         # the opening "#" and the label's own text
        self.assertNotIn("\n# 插入的行", text)

    def test_building_again_replaces_the_pack_and_it_can_be_deleted(self):
        session, _ = self.scanned()
        self.build(session, ["磨玻璃影"])
        self.build(session, ["肺动脉高压"])
        pack = next(p for p in srv.all_packs() if p["id"] == "hospital_respiratory_critical_care")
        self.assertEqual(srv.read_pack_words(pack), ["肺动脉高压"])
        deleted = self.client.post("/hotword-miner/packs/delete", json={"pack_id": "hospital_respiratory_critical_care"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(list(self.packs.iterdir()), [])

    def test_only_packs_made_here_can_be_deleted(self):
        for pack_id in ("general_medical", "hospital_../x", "orthopedics", "hospital_"):
            response = self.client.post("/hotword-miner/packs/delete", json={"pack_id": pack_id})
            self.assertIn(response.status_code, (400, 404), pack_id)
        self.assertTrue((Path(srv.__file__).parent / "data" / "hotword_packs" / "general_medical.txt").exists())

    # ------------------------------------------------------------------ who may call it
    def test_foreign_pages_cannot_use_any_miner_route(self):
        foreign = TestClient(srv.app, headers={"Origin": "https://evil.example"})
        for path in ("upload", "scan", "clear", "build", "packs/delete"):
            self.assertEqual(foreign.post(f"/hotword-miner/{path}", json={}).status_code, 403, path)
        noorigin = TestClient(srv.app)
        self.assertEqual(noorigin.post("/hotword-miner/clear", json={}).status_code, 403)


class Page(unittest.TestCase):
    def test_the_dialog_is_wired_into_the_editor_page(self):
        html = (ROOT / "extension" / "editor.html").read_text(encoding="utf-8")
        for needed in ('id="openMinerButton"', 'id="minerModal"', 'src="miner_logic.js"', 'src="miner.js"'):
            self.assertIn(needed, html)
        self.assertLess(html.index('src="editor.js"'), html.index('src="miner.js"'))
        self.assertLess(html.index('src="miner_logic.js"'), html.index('src="miner.js"'))
        script = (ROOT / "extension" / "miner.js").read_text(encoding="utf-8")
        for element_id in set(__import__("re").findall(r'\$m\("#(\w+)"\)', script)):
            self.assertIn(f'id="{element_id}"', html, element_id)

    def test_no_bat_file_is_part_of_this_feature(self):
        self.assertFalse((ROOT / "hotword_miner.bat").exists())
        self.assertNotIn("hotword_miner.bat", (ROOT / "scripts" / "build_beta_offline_package.ps1").read_text(encoding="utf-8"))

    @unittest.skipUnless(NODE, "node is not installed")
    def test_javascript_unit_tests(self):
        result = subprocess.run([NODE, "--test", str(ROOT / "tests" / "js" / "miner_logic.test.js")], capture_output=True,
                                text=True, encoding="utf-8", cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stdout[-3000:] + result.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()

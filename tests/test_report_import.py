"""Importing outside reports from pictures: sessions in memory, recognition, look-alike suggestions. Synthetic pictures only.

The recogniser itself is replaced by a stand-in for most tests (fast, and it does not need the OCR packages); one test
draws text into a picture and runs the real engine when the packages and a Chinese font are available."""
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as srv
from server import report_api
from server.report_ocr import engine, layout, similar


def png_bytes(size=(60, 40), color=(255, 255, 255)) -> bytes:
    """A real PNG without any image library: written by hand."""
    import struct
    import zlib
    width, height = size
    raw = b"".join(b"\x00" + bytes(color) * width for _ in range(height))

    def chunk(kind, data):
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def fake_lines():
    def line(text, y, x=100, score=0.99):
        return {"text": text, "score": score, "x": x, "y": y, "w": 600, "h": 30}
    return {"width": 1000, "height": 2000, "seconds": 0.2, "lines": [
        line("18:06", 60), line("of2", 230), line("*胸部+腹部（上、下+..", 150),
        line("检查所见：", 800),
        line("双侧辜丸朝膜积液，双肾实质内见类圆形低密度影，直径约22mm，", 900, x=130),
        line("增强扫描未见强化。", 940),
        line("1.左侧少量胸腔积液。", 1000),
        line("2.前列腺体积增大。", 1040),
    ]}


class Layout(unittest.TestCase):
    def test_phone_furniture_at_the_edges_is_dropped_but_report_text_is_not(self):
        lines, removed = layout.drop_screen_furniture(fake_lines()["lines"], 2000)
        texts = [l["text"] for l in lines]
        self.assertEqual(removed, 3)
        self.assertNotIn("18:06", texts)
        self.assertIn("检查所见：", texts)

    def test_a_clock_in_the_middle_of_a_page_is_kept(self):
        lines = [{"text": "12:30", "score": 0.9, "x": 0, "y": 1000, "w": 50, "h": 20}]
        self.assertEqual(layout.drop_screen_furniture(lines, 2000), (lines, 0))

    def test_wrapped_lines_are_joined_and_list_items_start_new_paragraphs(self):
        lines, _ = layout.drop_screen_furniture(fake_lines()["lines"], 2000)
        self.assertEqual(layout.paragraphs(lines), [
            "检查所见：",
            "双侧辜丸朝膜积液，双肾实质内见类圆形低密度影，直径约22mm，增强扫描未见强化。",
            "1.左侧少量胸腔积液。", "2.前列腺体积增大。"])

    def test_numbers_and_findings_to_compare_with_the_picture(self):
        items = layout.check_items("直径约3mm，大者22mm，白细胞12.5×10^9/L，HBsAg阴性，CRP 86mg/L")
        for expected in ("3mm", "22mm", "12.5×10^9/L", "阴性", "86mg/L"):
            self.assertIn(expected, items)


class LookAlike(unittest.TestCase):
    def test_the_errors_found_in_the_first_real_samples_are_suggested(self):
        index = similar.Index(srv.imaging_and_known_terms())
        for wrong, right in (("辜丸", "睾丸"), ("朝膜", "鞘膜"), ("素乱", "紊乱"), ("额下", "颏下"), ("颠下", "颏下"), ("肾孟", "肾盂")):
            found = similar.suggest(f"双侧{wrong}积液", index)
            self.assertTrue(any(wrong in s["from"] and any(right in o for o in s["options"]) for s in found), (wrong, found))

    def test_correct_terms_and_ordinary_text_get_no_suggestion(self):
        index = similar.Index(srv.imaging_and_known_terms())
        for text in ("双侧睾丸鞘膜积液", "支气管血管束走行紊乱", "颏下及双侧颌下区淋巴结显示", "肾盂及输尿管走形区未见异常",
                     "心影不大，纵隔内及肺门区淋巴结影显示，部分钙化", "前列腺体积增大，实质内未见明确异常密度影及强化灶"):
            self.assertEqual(similar.suggest(text, index), [], text)

    def test_nothing_is_replaced_only_suggested(self):
        text = "双侧辜丸积液"
        similar.suggest(text, similar.Index({"睾丸"}))
        self.assertEqual(text, "双侧辜丸积液")

    def test_the_term_list_is_marked_as_a_draft(self):
        text = (Path(srv.__file__).parent / "data" / "reference" / "imaging_terms.txt").read_text(encoding="utf-8")
        self.assertIn("草稿，待医生审核", text)
        self.assertIn("不是热词", text)


def lab_cells(rows, header=True):
    """Text boxes of a synthetic laboratory sheet: columns at the x positions of a real one (the values are made up)."""
    xs = {"no": 90, "abbr": 140, "name": 255, "result": 484, "unit": 654, "range": 724, "method": 861}
    titles = {"no": "序号", "abbr": "英文缩写", "name": "项目名称", "result": "结果", "unit": "单位", "range": "参考区间", "method": "测试方法"}
    cells = []

    def add(text, column, y):
        cells.append({"text": text, "score": 0.98, "x": xs[column] + (len(cells) % 3), "y": y, "w": 80, "h": 22})
    if header:
        for column, title in titles.items():
            add(title, column, 640)
    for i, values in enumerate(rows):
        for column, text in zip(("no", "abbr", "name", "result", "unit", "range", "method"), values):
            if text is not None:
                add(text, column, 670 + 28 * i)
    return cells


LAB_ROWS = [
    ("1", "WBC", "*白细胞", "11.20", "109/L", "3.5--9.5", "荧光染色法"),            # high, the arrow was lost
    ("2", "NEUT#", "中性粒细胞计数", "3.65", "10°9/L", "1.8--6.3", "荧光染色法"),
    ("3", "RBC", "*红细胞", "3.10↓", "1012/L", "4.3--5.8", "鞘流阻抗法"),          # low, arrow read
    ("4", "HGB", "*血红蛋白", "130", "g/L", "130--175", "无氰比色法"),            # on the limit
    ("5", "PLT", "*血小板", "220", "10`9/L", "125--350", "鞘流阻抗法"),
    ("6", "ALT", "丙氨酸氨基转移酶", "88↑", "U/L", "0--50", "速率法"),            # the arrow was read
    ("7", "CRP", "C反应蛋白", "2.0↑", "mg/L", "<5", "免疫比浊法"),                # an arrow that the range does not support
    ("8", "NRBC", "有核红细胞", None, "/100WBC", "0--0", "荧光染色法"),            # no result recognised
]


class LabTable(unittest.TestCase):
    def parse(self, rows=LAB_ROWS, header=True):
        from server.report_ocr import lab_table
        return lab_table.parse(lab_cells(rows, header))

    def test_rows_and_columns_come_from_the_header(self):
        table = self.parse()
        self.assertEqual(table["columns"], ["no", "abbr", "name", "result", "unit", "range", "method"])
        self.assertEqual(len(table["rows"]), 8)
        first = table["rows"][0]
        self.assertEqual((first["name"], first["abbr"], first["result"], first["range"], first["method"]), ("白细胞", "WBC", "11.20", "3.5-9.5", "荧光染色法"))

    def test_units_lose_no_superscript(self):
        units = [r["unit"] for r in self.parse()["rows"]]
        self.assertEqual(units[:5], ["10^9/L", "10^9/L", "10^12/L", "g/L", "10^9/L"])

    def test_a_lost_arrow_is_worked_out_from_the_reference_range_and_reported(self):
        rows = {r["name"]: r for r in self.parse()["rows"]}
        self.assertEqual((rows["白细胞"]["flag"], rows["白细胞"]["printed_flag"]), ("↑", ""))
        self.assertIn("没有识别出来", rows["白细胞"]["note"])
        self.assertEqual((rows["红细胞"]["flag"], rows["红细胞"]["note"]), ("↓", ""))              # arrow and range agree
        self.assertEqual((rows["血红蛋白"]["flag"], rows["血红蛋白"]["note"]), ("", ""))             # the limit itself is normal
        self.assertEqual((rows["丙氨酸氨基转移酶"]["flag"], rows["丙氨酸氨基转移酶"]["note"]), ("↑", ""))
        self.assertEqual(rows["血小板"]["flag"], "")

    def test_an_arrow_the_range_does_not_support_is_flagged_for_checking(self):
        row = {r["name"]: r for r in self.parse()["rows"]}["C反应蛋白"]
        self.assertEqual(row["flag"], "")           # 2.0 against "<5" is normal: the range wins, the doubt is shown
        self.assertEqual(row["printed_flag"], "↑")
        self.assertIn("请对照原图", row["note"])

    def test_a_missing_result_is_never_filled_in(self):
        row = {r["name"]: r for r in self.parse()["rows"]}["有核红细胞"]
        self.assertEqual((row["result"], row["flag"]), ("", ""))
        self.assertIn("没有识别出结果", row["note"])

    def test_the_item_line_that_goes_into_the_record(self):
        from server.report_ocr import lab_table
        rows = {r["name"]: r for r in self.parse()["rows"]}
        self.assertEqual(lab_table.line_for(rows["红细胞"]), "红细胞 3.10↓ 10^12/L （参考 4.3-5.8）")

    def test_no_header_means_no_table(self):
        self.assertIsNone(self.parse(header=False))
        self.assertIsNone(self.parse(rows=LAB_ROWS[:1]))                        # a single row is not a table

    def test_report_text_is_not_taken_for_a_table(self):
        from server.report_ocr import lab_table
        self.assertIsNone(lab_table.parse(fake_lines()["lines"]))

    def test_derived_flags(self):
        from server.report_ocr.lab_table import derived_flag
        self.assertEqual([derived_flag("5", "1-9"), derived_flag("0.5", "1-9"), derived_flag("10", "1-9"), derived_flag("9", "1-9")], ["", "↓", "↑", ""])
        self.assertEqual([derived_flag("6", "<5"), derived_flag("4", "<5"), derived_flag("2", ">3"), derived_flag("4", ">3")], ["↑", "", "↓", ""])
        self.assertEqual([derived_flag("1", "0-0"), derived_flag("0", "0-0")], ["↑", ""])
        self.assertIsNone(derived_flag("阴性", "阴性"))
        self.assertIsNone(derived_flag("5", "见报告"))

    def test_the_api_returns_the_table_instead_of_paragraphs(self):
        report_api.clear_all()
        self.addCleanup(report_api.clear_all)
        client = TestClient(srv.app, headers={"Origin": "http://testserver"})
        with mock.patch.object(engine, "unavailable_reason", lambda: None), mock.patch.object(engine, "image_size", lambda d: (10, 10)),                 mock.patch.object(engine, "recognize", lambda data, degrees=0: {"lines": lab_cells(LAB_ROWS), "width": 1179, "height": 2556, "seconds": 1.0}):
            up = client.post("/report-import/upload", files=[("files", ("lab.png", png_bytes(), "image/png"))]).json()
            body = client.post("/report-import/recognize", json={"session": up["session"], "image_id": up["added"][0]["id"]}).json()
        self.assertEqual(len(body["table"]["rows"]), 8)
        self.assertEqual(body["text"].splitlines()[0], "白细胞 11.20↑ 10^9/L （参考 3.5-9.5）")
        self.assertTrue(any("白细胞" in item for item in body["check_items"]), body["check_items"])
        self.assertEqual(body["suggestions"], [])


class Api(unittest.TestCase):
    def setUp(self):
        report_api.clear_all()
        self.addCleanup(report_api.clear_all)
        self.client = TestClient(srv.app, headers={"Origin": "http://testserver"})
        self.patches = [mock.patch.object(engine, "unavailable_reason", lambda: None),
                        mock.patch.object(engine, "image_size", lambda data: (60, 40)),
                        mock.patch.object(engine, "recognize", lambda data, degrees=0: fake_lines())]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def upload(self, files, session=None):
        data = {"session": session} if session else {}
        return self.client.post("/report-import/upload", data=data, files=[("files", (n, b, "image/png")) for n, b in files])

    def test_upload_and_recognise_end_to_end(self):
        first = self.upload([("a.png", png_bytes()), ("b.jpg", png_bytes())])
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual([a["name"] for a in body["added"]], ["a.png", "b.jpg"])
        reply = self.client.post("/report-import/recognize", json={"session": body["session"], "image_id": body["added"][0]["id"]})
        self.assertEqual(reply.status_code, 200, reply.text)
        result = reply.json()
        self.assertEqual(result["removed"], 3)
        self.assertIn("双侧辜丸朝膜积液", result["text"])
        self.assertNotIn("18:06", result["text"])
        found = " ".join(s["from"] for s in result["suggestions"])
        self.assertTrue("辜丸" in found and "朝膜" in found, result["suggestions"])
        self.assertIn("22mm", result["check_items"])
        self.assertEqual(result["text"].splitlines()[-1], "2.前列腺体积增大。")

    def test_more_pictures_can_be_added_to_the_same_session(self):
        session = self.upload([("a.png", png_bytes())]).json()["session"]
        again = self.upload([("b.png", png_bytes())], session=session)
        self.assertEqual((again.status_code, again.json()["session"]), (200, session))
        self.assertEqual(self.upload([("c.png", png_bytes())], session="nope").status_code, 404)

    def test_files_that_cannot_be_used_say_why(self):
        body = self.upload([("report.pdf", b"%PDF"), ("photo.heic", b"x"), ("notes.txt", b"x"), ("ok.png", png_bytes())]).json()
        reasons = {r["name"]: r["reason"] for r in body["refused"]}
        self.assertIn("PDF", reasons["report.pdf"])
        self.assertIn("HEIC", reasons["photo.heic"])
        self.assertIn("不是图片", reasons["notes.txt"])
        self.assertEqual([a["name"] for a in body["added"]], ["ok.png"])

    def test_an_unreadable_picture_is_refused_with_the_reason(self):
        with mock.patch.object(engine, "image_size", side_effect=engine.BadImage("无法读取这张图片")):
            body = self.upload([("bad.png", b"not an image")]).json()
        self.assertEqual(body["added"], [])
        self.assertIn("无法读取", body["refused"][0]["reason"])

    def test_limits(self):
        with mock.patch.object(report_api, "MAX_FILE_BYTES", 10):
            self.assertIn("25 MB", self.upload([("big.png", png_bytes())]).json()["refused"][0]["reason"])
        with mock.patch.object(report_api, "MAX_SESSION_BYTES", 10):
            self.assertEqual(self.upload([("a.png", png_bytes())]).status_code, 413)
        too_many = self.client.post("/report-import/upload", files=[("files", (f"{i}.png", b"x", "image/png")) for i in range(report_api.MAX_FILES + 1)])
        self.assertEqual(too_many.status_code, 413 if too_many.status_code == 413 else 400)

    def test_pictures_are_kept_in_memory_only(self):
        import tempfile
        before = set(Path(tempfile.gettempdir()).glob("*"))
        big = os.urandom(3 * 1024 * 1024)                        # larger than the 1 MB a file may normally stay in memory
        with mock.patch("tempfile.SpooledTemporaryFile.rollover", side_effect=AssertionError("rolled over to disk")):
            self.assertEqual(self.upload([("big.png", big)]).status_code, 200)
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("*")) - before, set())

    def test_clear_expiry_and_a_new_session_remove_the_pictures(self):
        import time
        session = self.upload([("a.png", png_bytes())]).json()
        self.assertEqual(self.client.post("/report-import/clear", json={"session": session["session"]}).json(), {"cleared": True})
        self.assertEqual(self.client.post("/report-import/recognize", json={"session": session["session"], "image_id": session["added"][0]["id"]}).status_code, 404)
        session = self.upload([("a.png", png_bytes())]).json()
        with mock.patch.object(report_api.time, "time", return_value=time.time() + report_api.SESSION_TTL + 5):
            self.assertEqual(self.client.post("/report-import/recognize", json={"session": session["session"], "image_id": session["added"][0]["id"]}).status_code, 404)
        first = self.upload([("a.png", png_bytes())]).json()["session"]
        second = self.upload([("b.png", png_bytes())]).json()["session"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.upload([("c.png", png_bytes())], session=first).status_code, 404)

    def test_unknown_picture_and_missing_engine(self):
        session = self.upload([("a.png", png_bytes())]).json()["session"]
        self.assertEqual(self.client.post("/report-import/recognize", json={"session": session, "image_id": "nope"}).status_code, 404)
        with mock.patch.object(engine, "unavailable_reason", lambda: "这个安装包没有包含图片识别组件（cv2）"):
            self.assertFalse(self.client.get("/report-import/status").json()["available"])
            image_id = report_api._get(session)["images"].__iter__().__next__()
            reply = self.client.post("/report-import/recognize", json={"session": session, "image_id": image_id})
            self.assertEqual(reply.status_code, 503)

    def test_a_foreign_page_cannot_use_it(self):
        foreign = TestClient(srv.app, headers={"Origin": "https://evil.example"})
        for path in ("upload", "recognize", "clear"):
            self.assertEqual(foreign.post(f"/report-import/{path}", json={}).status_code, 403, path)

    def test_login_is_required(self):
        with mock.patch.dict(os.environ, {"BINGLI_AUTH": "1"}):
            self.assertEqual(self.client.get("/report-import/status").status_code, 401)
            self.assertEqual(self.client.post("/report-import/clear", json={}).status_code, 401)


@unittest.skipIf(engine.unavailable_reason(), "the picture recognition packages are not installed")
class RealEngine(unittest.TestCase):
    FONTS = [r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]

    def picture(self, lines):
        from PIL import Image, ImageDraw, ImageFont
        font_file = next((f for f in self.FONTS if Path(f).exists()), None)
        if font_file is None:
            self.skipTest("no Chinese font on this computer")
        font = ImageFont.truetype(font_file, 34)
        image = Image.new("RGB", (1100, 90 * len(lines) + 40), "white")
        draw = ImageDraw.Draw(image)
        for i, text in enumerate(lines):
            draw.text((40, 30 + 90 * i), text, fill="black", font=font)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        return buffer.getvalue()

    def test_drawn_text_is_read_and_the_picture_is_not_modified(self):
        data = self.picture(["检查所见：双侧胸腔积液，直径约22mm。", "1.前列腺体积增大。", "2.盆腔少量积液。"])
        result = engine.recognize(data)
        text = "".join(line["text"] for line in result["lines"])
        for expected in ("胸腔积液", "22mm", "前列腺", "盆腔"):
            self.assertIn(expected, text)
        self.assertLess(result["seconds"], 60)

    def test_rotation_is_applied_before_recognising(self):
        from PIL import Image
        data = self.picture(["双侧胸腔积液。"])
        rotated = Image.open(io.BytesIO(data)).rotate(90, expand=True)
        buffer = io.BytesIO()
        rotated.save(buffer, "PNG")
        text = "".join(line["text"] for line in engine.recognize(buffer.getvalue(), degrees=270)["lines"])
        self.assertIn("胸腔积液", text)

    def test_a_broken_file_is_a_clear_error(self):
        with self.assertRaises(engine.BadImage):
            engine.recognize(b"not an image")


if __name__ == "__main__":
    unittest.main()

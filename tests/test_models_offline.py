"""An offline package never downloads models. A model missing from the package (the English one is not shipped)
is refused at once with a clear message; before this, English dictation started an 887 MB download into the
package folder (or hung where there is no internet) while the doctor waited."""
import io
import math
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from server import app as srv

EN_LEAF = "speech_paraformer-large-vad-punc_asr_nat-en-16k-common-vocab10020"


def tone_wav(seconds=1.5) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(int(16000 * seconds))))
    return buffer.getvalue()


class OfflineModels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patcher in (mock.patch.object(srv, "model_cache_roots", lambda: [self.root]),
                        mock.patch.dict(os.environ, {"MODELSCOPE_OFFLINE": "1"}),
                        mock.patch.object(srv, "english_model", None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self, relative: str):
        path = self.root / relative
        path.mkdir(parents=True)
        (path / "model.pt").write_bytes(b"x")

    def test_a_missing_model_is_refused_offline_and_allowed_online(self):
        with self.assertRaises(srv.ModelUnavailable) as caught:
            srv.ensure_model_available("paraformer-en", "英文识别模型")
        self.assertIn("没有随这个安装包提供", str(caught.exception))
        with mock.patch.dict(os.environ, {"MODELSCOPE_OFFLINE": "0"}):
            srv.ensure_model_available("paraformer-en", "英文识别模型")          # a developer's computer may download

    def test_a_model_in_either_folder_layout_is_found(self):
        self.assertFalse(srv.model_available("paraformer-en"))
        self.install(f"hub/models/iic/{EN_LEAF}")
        self.assertTrue(srv.model_available("paraformer-en"))
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        self.root = Path(other.name)
        with mock.patch.object(srv, "model_cache_roots", lambda: [self.root]):
            self.install(f"hub/models/iic--{EN_LEAF}")
            self.assertTrue(srv.model_available("paraformer-en"))

    def test_an_empty_folder_does_not_count_as_a_model(self):
        (self.root / "hub" / "models" / "iic" / EN_LEAF).mkdir(parents=True)
        self.assertFalse(srv.model_available("paraformer-en"))

    def test_the_english_model_is_never_built_when_it_is_missing(self):
        with mock.patch.object(srv, "build_asr_model", side_effect=AssertionError("must not be built")):
            with self.assertRaises(srv.ModelUnavailable):
                srv.get_english_model()

    def test_the_chinese_models_are_checked_too(self):
        for call in (srv.get_model, srv.get_streaming_model):
            with mock.patch.object(srv, "model", None), mock.patch.object(srv, "streaming_model", None), \
                    mock.patch("funasr.AutoModel", side_effect=AssertionError("must not be built")):
                with self.assertRaises(srv.ModelUnavailable):
                    call()

    def test_english_transcription_answers_503_with_the_reason_and_writes_nothing(self):
        client = TestClient(srv.app, headers={"Origin": "http://testserver"})
        response = client.post("/transcribe", data={"language": "en-US"}, files={"file": ("a.wav", tone_wav(), "audio/wav")})
        self.assertEqual(response.status_code, 503, response.text)
        self.assertIn("英文识别模型没有随这个安装包提供", response.json()["detail"])
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_health_says_whether_english_dictation_works(self):
        client = TestClient(srv.app, headers={"Origin": "http://testserver"})
        self.assertFalse(client.get("/health").json()["english_dictation_available"])
        self.install(f"hub/models/iic/{EN_LEAF}")
        self.assertTrue(client.get("/health").json()["english_dictation_available"])
        with mock.patch.dict(os.environ, {"MODELSCOPE_OFFLINE": "0"}):
            self.assertTrue(client.get("/health").json()["english_dictation_available"])

    def test_the_page_switches_back_to_chinese_when_english_is_not_available(self):
        script = (Path(__file__).resolve().parents[1] / "extension" / "editor.js").read_text(encoding="utf-8")
        self.assertIn("verifyEnglishDictation", script)
        self.assertIn("english_dictation_available", script)

    def test_the_english_interface_is_not_offered(self):
        root = Path(__file__).resolve().parents[1] / "extension"
        html = (root / "editor.html").read_text(encoding="utf-8")
        self.assertIn('id="uiLanguageLabel" hidden', html)
        self.assertIn('id="dictationLanguageLabel" hidden', html)
        script = (root / "editor.js").read_text(encoding="utf-8")
        self.assertIn('els.uiLanguageSelect.value = "zh-CN";', script)       # a saved English choice is ignored

    def test_the_english_choice_is_hidden_when_the_model_is_not_in_the_package(self):
        script = (Path(__file__).resolve().parents[1] / "extension" / "editor.js").read_text(encoding="utf-8")
        self.assertIn("hideEnglishDictationIfUnavailable", script)
        self.assertIn("option.hidden = !englishDictationOffered", script)
        self.assertIn('=== "en-US" && englishDictationOffered ? "en-US" : "zh-CN"', script)     # a saved English choice falls back to Chinese


if __name__ == "__main__":
    unittest.main()

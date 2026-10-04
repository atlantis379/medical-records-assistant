"""Models are loaded in the background after start-up so the first dictation does not wait for them."""
import asyncio
import os
import unittest
from unittest import mock

from server import app as srv


class Preload(unittest.TestCase):
    def run_preload(self, env, streaming=None, batch=None):
        calls = []

        def fake_streaming():
            calls.append("streaming")
            if streaming:
                raise streaming

        def fake_batch():
            calls.append("batch")
            if batch:
                raise batch

        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(srv, "get_streaming_model", fake_streaming), \
                mock.patch.object(srv, "get_model", fake_batch), \
                mock.patch.object(srv, "streaming_model_error", None), \
                mock.patch.object(srv, "batch_model_error", None):
            asyncio.run(srv.preload_models())
            return calls, srv.streaming_model_error, srv.batch_model_error

    def test_streaming_first_then_batch(self):
        calls, _, _ = self.run_preload({"ASR_PRELOAD_STREAMING": "1", "ASR_PRELOAD_BATCH": "1"})
        self.assertEqual(calls, ["streaming", "batch"])

    def test_batch_preload_is_off_by_default(self):
        env = {"ASR_PRELOAD_STREAMING": "1"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("ASR_PRELOAD_BATCH", None)
            calls, _, _ = self.run_preload(env)
        self.assertEqual(calls, ["streaming"])

    def test_either_can_be_switched_off(self):
        self.assertEqual(self.run_preload({"ASR_PRELOAD_STREAMING": "0", "ASR_PRELOAD_BATCH": "1"})[0], ["batch"])
        self.assertEqual(self.run_preload({"ASR_PRELOAD_STREAMING": "0", "ASR_PRELOAD_BATCH": "0"})[0], [])

    def test_a_failing_model_does_not_stop_the_other_and_is_reported(self):
        calls, streaming_error, batch_error = self.run_preload(
            {"ASR_PRELOAD_STREAMING": "1", "ASR_PRELOAD_BATCH": "1"}, streaming=RuntimeError("no streaming model"))
        self.assertEqual(calls, ["streaming", "batch"])
        self.assertEqual(streaming_error, "no streaming model")
        self.assertIsNone(batch_error)
        _, _, batch_error = self.run_preload({"ASR_PRELOAD_STREAMING": "0", "ASR_PRELOAD_BATCH": "1"}, batch=RuntimeError("no batch model"))
        self.assertEqual(batch_error, "no batch model")

    def test_health_reports_the_batch_model_error(self):
        from fastapi.testclient import TestClient
        body = TestClient(srv.app, headers={"Origin": "http://testserver"}).get("/health").json()
        self.assertIn("batch_model_error", body)

    def test_the_launcher_and_the_visible_start_script_turn_batch_preload_on(self):
        from pathlib import Path
        root = Path(srv.APP_DIR).parent
        self.assertIn('env["ASR_PRELOAD_BATCH"]', (root / "packaging" / "windows" / "launcher" / "BingliLauncher.cs").read_text(encoding="utf-8"))
        self.assertIn("set ASR_PRELOAD_BATCH=1", (root / "scripts" / "build_beta_offline_package.ps1").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

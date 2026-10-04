"""Read-only smoke tests for the local HTTP API (no model is loaded)."""
import unittest

from fastapi.testclient import TestClient

from server import app as srv
from server import security

# the browser extension always sends an Origin; the service requires one for POST
EXTENSION_ORIGIN = f"chrome-extension://{sorted(security.pinned_extension_ids())[0]}"   # the shipped, fixed ID
client = TestClient(srv.app, headers={"Origin": "http://testserver"})


class ApiSmoke(unittest.TestCase):
    def test_health(self):
        body = client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], srv.app.version)

    def test_asr_config_lists_three_profiles(self):
        body = client.get("/asr/config").json()
        self.assertEqual({p["id"] for p in body["profiles"]}, {"fast", "balanced", "accurate"})

    def test_license_status_is_free_tier_by_default(self):
        body = client.get("/license/status").json()
        self.assertEqual(body["status"], "active")
        self.assertTrue(body["offline"])

    def test_hotword_pack_listing(self):
        body = client.get("/hotword-packs").json()
        self.assertEqual(len(body["packs"]), len(srv.all_packs()))
        self.assertGreater(len(srv.all_packs()), len(srv.HOTWORD_PACKS))  # specialty packs are listed too

    def test_correction_rules_endpoint(self):
        body = client.get("/correction-rules").json()
        self.assertEqual(body["count"], len(body["rules"]))

    def test_transcribe_rejects_non_wav(self):
        response = client.post("/transcribe", files={"file": ("a.mp3", b"x" * 2048, "audio/mpeg")})
        self.assertEqual(response.status_code, 400)

    def test_transcribe_rejects_tiny_audio(self):
        response = client.post("/transcribe", files={"file": ("a.wav", b"x" * 10, "audio/wav")})
        self.assertEqual(response.status_code, 400)

    def test_cors_allows_extension_origin_only(self):
        allowed = client.get("/health", headers={"Origin": EXTENSION_ORIGIN})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"), EXTENSION_ORIGIN)
        blocked = client.get("/health", headers={"Origin": "https://evil.example"})
        self.assertIsNone(blocked.headers.get("access-control-allow-origin"))


if __name__ == "__main__":
    unittest.main()

"""Sağlayıcı seçiminin HTTP yetki sınırını, sürüm çakışmasını ve yeniden açılışta korunmasını doğrular."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from radar.sunucu import uygulama

ORIGIN = "http://127.0.0.1:8781"


class SaglayiciHttpTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "personal.sqlite3"
        self.model = Mock(side_effect=AssertionError("Ayar okumak model çağrısı başlatamaz."))
        for target in ("radar.zeka.codex_hazir", "radar.derin_arastirma.claude_hazir"):
            patcher = patch(target, return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def open(self):
        return TestClient(uygulama(self.path, sources=[], seed=False, ai_provider=self.model,
                                  credential_provider=lambda: None), base_url=ORIGIN)

    def payload(self, **values):
        return {"deep_provider": "codex", "codex_model": None, "output_language": "en", "expected_version": 0, **values}

    def headers(self, client):
        return {"Origin": ORIGIN, "X-Radar-CSRF": client.get("/api/platform").json()["csrf_token"]}

    def test_ayarlar_model_cagirmadan_saklanir_ve_eski_surumu_reddeder(self):
        with self.open() as client:
            initial = client.get("/api/providers").json()
            self.assertEqual(initial["deep_provider"], "claude")
            headers = self.headers(client)
            saved = client.post("/api/providers", json=self.payload(), headers=headers)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["version"], 1)
            self.assertEqual(client.post("/api/providers", json=self.payload(), headers=headers).status_code, 409)
        with self.open() as client:
            saved = client.get("/api/providers").json()
            self.assertEqual((saved["deep_provider"], saved["output_language"], saved["version"]), ("codex", "en", 1))
        self.model.assert_not_called()

    def test_csrf_origin_ve_bilinmeyen_alanlar_korunur(self):
        with self.open() as client:
            headers = self.headers(client)
            self.assertEqual(client.post("/api/providers", json=self.payload()).status_code, 403)
            self.assertEqual(client.post("/api/providers", json=self.payload(),
                                        headers={**headers, "Origin": "https://example.org"}).status_code, 403)
            self.assertEqual(client.post("/api/providers", json=self.payload(api_key="synthetic-only"), headers=headers).status_code, 422)
            self.assertEqual(client.post("/api/providers", json=self.payload(output_language="unknown"), headers=headers).status_code, 422)
            self.assertEqual(client.get("/api/providers").json()["version"], 0)
        self.model.assert_not_called()


if __name__ == "__main__":
    unittest.main()

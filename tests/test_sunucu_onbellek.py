"""Yerel sunucuda önbellek sınırı (mühendislik raporu H8/G24): kimliksiz, kişisel veri taşımayan okuma uçları güçlü ETag
ve 304 ile yeniden doğrulanır; durum, kayıt ve dışa aktarım uçları no-store kalır."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from fastapi.testclient import TestClient

from radar.sunucu import uygulama

ADRES = "http://127.0.0.1:8781"


class YerelOnbellek(unittest.TestCase):
    def setUp(self):
        gecici = tempfile.TemporaryDirectory()
        self.addCleanup(gecici.cleanup)
        collector = Mock(return_value={"items": [], "etag": None, "modified": None, "not_modified": False})
        self.app = uygulama(Path(gecici.name) / "radar.sqlite3", sources=[], seed=False, collector=collector)
        self.client = TestClient(self.app, base_url=ADRES)
        self.client.__enter__()
        self.addCleanup(self.kapat)

    def kapat(self):
        self.app.state.collection.kapan()
        self.client.__exit__(None, None, None)

    def test_kamu_okumalari_etag_ve_304(self):
        for yol in ("/guide?language=en&document=mcp", "/api/topics"):
            with self.subTest(yol=yol):
                ilk = self.client.get(yol)
                self.assertEqual(ilk.status_code, 200)
                self.assertEqual(ilk.headers["cache-control"], "public, max-age=0, must-revalidate")
                etag = ilk.headers["etag"]
                self.assertRegex(etag, r'^"[0-9a-f]{64}"$')       # güçlü ETag (W/ yok)
                ikinci = self.client.get(yol, headers={"If-None-Match": etag})
                self.assertEqual(ikinci.status_code, 304)
                self.assertEqual(ikinci.content, b"")
                self.assertEqual(ikinci.headers["etag"], etag)
                self.assertEqual(self.client.get(yol, headers={"If-None-Match": '"baska"'}).status_code, 200)
                self.assertEqual(ilk.headers["x-content-type-options"], "nosniff")

    def test_durgan_dosya_yeniden_dogrulanir(self):
        ilk = self.client.get("/static/app.js")
        self.assertEqual(ilk.status_code, 200)
        self.assertEqual(ilk.headers["cache-control"], "public, max-age=0, must-revalidate")
        ikinci = self.client.get("/static/app.js", headers={"If-None-Match": ilk.headers["etag"]})
        self.assertEqual(ikinci.status_code, 304)
        self.assertEqual(ikinci.headers["cache-control"], "public, max-age=0, must-revalidate")

    def test_ozel_ve_durum_uclari_no_store(self):
        for yol in ("/", "/api/state", "/api/health", "/api/personal", "/api/export", "/api/backup"):
            with self.subTest(yol=yol):
                yanit = self.client.get(yol)
                self.assertEqual(yanit.headers["cache-control"], "no-store")
                if yol.startswith("/api/"):
                    self.assertNotIn("etag", yanit.headers)
        self.assertEqual(self.client.get("/static/yok.js").headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()

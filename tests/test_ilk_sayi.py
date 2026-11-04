"""Türkçe ilk sayının yeni kurulumlarda doğru kaynakla eşleştiğini ve bayat içerikte durduğunu doğrular."""

import tempfile
import unittest
from pathlib import Path

from radar.baslangic import ilk_sayiyi_ekle
from radar.depo import Depo


class IlkSayiTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.depo = Depo(Path(self.gecici.name) / "db.sqlite3", [
            {"id": "kaynak", "name": "Kaynak", "topic": "ai", "interval_hours": 24, "benefit": "İlgili"}])
        self.raw = {"external_id": "1", "url": "https://example.org/release", "title": "Release",
                    "excerpt": "Specific release content", "language": "en", "evidence_type": "surum"}
        self.depo.kaydet("kaynak", [self.raw])
        with self.depo.baglanti() as connection:
            row = connection.execute("SELECT * FROM items").fetchone()
        self.card = {"id": "baska-kurulumun-kimligi", "source_revision": 9, "url": self.raw["url"],
                     "source_hash": row["content_hash"], "title_tr": "Sürüm", "summary_tr": "Kaynaklı özet",
                     "benefit_tr": "Somut yararı", "limitation": "Yerelde denenmedi"}

    def test_yeni_kimlik_ve_farkli_revizyon_sayisi_icerigi_bozmaz(self):
        self.assertEqual(ilk_sayiyi_ekle(self.depo, [self.card]), {"added": 1, "skipped": []})
        item = self.depo.gorunum()["items"][0]
        self.assertEqual(item["title"], "Sürüm")
        self.assertEqual(item["summary_status"], "editor_ozeti")
        self.assertEqual(item["source_title"], "Release")

    def test_degisik_icerik_ve_eksik_kaynak_yanlis_ozet_almaz(self):
        self.depo.kaydet("kaynak", [{**self.raw, "excerpt": "The source corrected its statement"}])
        result = ilk_sayiyi_ekle(self.depo, [self.card, {**self.card, "url": "https://example.org/missing"}])
        self.assertEqual(result["added"], 0)
        self.assertEqual(len(result["skipped"]), 2)
        self.assertNotEqual(self.depo.gorunum()["items"][0]["summary_status"], "editor_ozeti")

    def test_tekrar_cagri_not_tarihini_ve_onceligini_yenilemez(self):
        ilk_sayiyi_ekle(self.depo, [self.card])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE editorial SET created_at='2026-01-01T00:00:00+00:00'")
        result = ilk_sayiyi_ekle(self.depo, [self.card])
        self.assertEqual(result["added"], 0)
        self.assertEqual(self.depo.gorunum()["items"][0]["editorial_date"], "2026-01-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()

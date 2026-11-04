"""İnceleme notlarının çakışmadan ve kaynak revizyonunu kaybetmeden saklandığını doğrular."""

import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from radar.calisma_masasi import CalismaMasasi, SurumCatismasi
from radar.depo import Depo


class IncelemeTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.source = {"id": "kaynak", "name": "Kaynak", "topic": "ai", "enabled": True,
                       "interval_hours": 24, "benefit": "İlgili bir gelişme"}
        self.depo = Depo(Path(self.gecici.name) / "veri.sqlite3", [self.source])
        self.haber = {"external_id": "1", "url": "https://example.org/haber", "title": "Original title",
                      "excerpt": "Kaynağın kısa açıklaması", "language": "en", "evidence_type": "duyuru"}
        self.depo.kaydet("kaynak", [self.haber])
        self.item = self.depo.gorunum()["items"][0]
        self.job = self.depo.arastirma_sec(self.item["id"])
        self.masa = CalismaMasasi(self.depo)

    def yaz(self, **kwargs):
        values = {"note": "Kişisel incelemenin kaynaklı sonucu burada.", "status": "bekliyor",
                  "expected_version": 0, "source_revision": 1}
        values.update(kwargs)
        return self.masa.kaydet(self.job["id"], **values)

    def test_kalici_sonuc_surumleri_durum_ve_disa_aktarim(self):
        first = self.yaz()
        self.assertEqual(first["version"], 1)
        self.yaz(note="İnceleme tamamlandı; ilk iddia bağımsız doğrulanmadı.", status="tamamlandi", expected_version=1)
        reopened = self.masa.oku(self.job["id"])
        self.assertEqual(reopened["status"], "tamamlandi")
        self.assertEqual(len(reopened["notes"]), 2)
        self.assertEqual(reopened["owner"], "Kullanıcı")
        saved = Depo(self.depo.path, [self.source]).disa_aktar()
        self.assertEqual(len(saved["research_notes"]), 2)
        self.assertEqual(saved["jobs"][0]["result"], reopened["note"])

    def test_ayni_not_yeniden_gonderilince_surum_cogaltilmaz(self):
        self.yaz()
        again = self.yaz(expected_version=1)
        self.assertEqual(again["version"], 1)
        self.assertEqual(len(again["notes"]), 1)

    def test_iki_pencere_yarisinda_sadece_biri_yazar(self):
        def dene(note):
            try:
                return self.yaz(note=note)["version"]
            except SurumCatismasi:
                return "cakisma"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(dene, ["Birinci pencerenin inceleme notu.", "İkinci pencerenin inceleme notu."]))
        self.assertCountEqual(results, [1, "cakisma"])
        self.assertEqual(len(self.masa.oku(self.job["id"])["notes"]), 1)

    def test_kaynak_degisince_eski_not_bayraklanir_ve_eski_istem_reddedilir(self):
        self.yaz()
        self.depo.kaydet("kaynak", [{**self.haber, "excerpt": "Kaynak açıklamasını düzeltti."}])
        self.assertTrue(self.masa.oku(self.job["id"])["source_changed"])
        report = self.masa.dosya(self.job["id"])
        self.assertIn("UYARI: Kaynak bu nottan sonra değişti", report)
        self.assertIn("Notun değerlendirdiği kaynak revizyonu: 1", report)
        self.assertIn("Kaynak revizyonu: 2", report)
        with self.assertRaises(SurumCatismasi):
            self.yaz(expected_version=1, status="tamamlandi")
        latest = self.yaz(expected_version=1, source_revision=2, status="tamamlandi")
        self.assertFalse(latest["source_changed"])
        self.assertEqual(latest["notes"][0]["source_revision"], 2)

    def test_bos_sonuc_tamamlanamaz_ama_iptal_ve_yeniden_acma_mumkun(self):
        with self.assertRaises(ValueError):
            self.yaz(note="", status="tamamlandi")
        cancelled = self.yaz(note="", status="iptal")
        self.assertEqual(cancelled["status"], "iptal")
        reopened = self.yaz(note="", status="bekliyor", expected_version=1)
        self.assertEqual(reopened["status"], "bekliyor")
        self.assertEqual(len(reopened["notes"]), 2)

    def test_inceleme_dosyasi_kaynakli_ve_yapilmamis_arastirmayi_iddia_etmez(self):
        self.depo.editor_karti_yaz({"id": self.item["id"], "source_revision": 1,
                                   "title_tr": "Türkçe inceleme başlığı", "summary_tr": "Kaynaklı özet.",
                                   "benefit_tr": "İncelemeye değer.", "limitation": "Deney yapılmadı."})
        text = self.masa.dosya(self.job["id"])
        self.assertIn("Türkçe inceleme başlığı", text)
        self.assertIn("https://example.org/haber", text)
        self.assertIn("yapılmış otomatik araştırma değildir", text)
        self.assertIn("Kaynak revizyonu: 1", text)

    def test_gecersiz_istekler_notu_degistirmez(self):
        for changes in ({"status": "calistir"}, {"note": "a" * 2001}):
            with self.assertRaises(ValueError):
                self.yaz(**changes)
        with self.assertRaises(KeyError):
            self.masa.oku("yok")
        self.assertEqual(self.masa.oku(self.job["id"])["version"], 0)


if __name__ == "__main__":
    unittest.main()

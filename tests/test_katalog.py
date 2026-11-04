"""Büyük havuzun tamamında Türkçe arama ve tekrar etmeyen sınırlı sayfaları doğrular."""

import tempfile
import unittest
from pathlib import Path

from radar.depo import Depo
from radar.katalog import kartlari_listele


class KatalogTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.depo = Depo(Path(self.gecici.name) / "radar.sqlite3", [
            {"id": "kaynak", "name": "Kaynak", "topic": "ai", "interval_hours": 24, "benefit": "İlgili"}])

    def ekle(self, number, **kwargs):
        item = {"external_id": str(number), "url": f"https://example.org/{number}",
                "title": f"Kayıt {number:04}", "excerpt": "Açıklama", "language": "tr", "evidence_type": "duyuru"}
        item.update(kwargs)
        self.depo.kaydet("kaynak", [item])
        return item

    def test_500_sonrasindaki_kart_da_aranabilir_ve_sayfalar_tekildir(self):
        self.depo.kaydet("kaynak", [{"external_id": str(i), "url": f"https://example.org/{i}",
                                   "title": f"Sıra {i:04}", "excerpt": "Açıklama", "evidence_type": "duyuru"}
                                  for i in range(523)])
        # Başlangıç görünümünden çıkarılan kayıt, tam aramada hâlâ bulunmalıdır.
        visible = {item["id"] for item in self.depo.gorunum()["items"]}
        with self.depo.baglanti() as connection:
            outside = next(dict(row) for row in connection.execute("SELECT * FROM items") if row["id"] not in visible)
        found = kartlari_listele(self.depo, query=outside["title"])
        self.assertIn(outside["id"], [item["id"] for item in found["items"]])
        pages = [kartlari_listele(self.depo, offset=i) for i in range(0, 523, 100)]
        ids = [item["id"] for page in pages for item in page["items"]]
        self.assertEqual(len(ids), 523)
        self.assertEqual(len(set(ids)), 523)
        self.assertTrue(all(page["total"] == 523 for page in pages))

    def test_turkce_i_noktalari_ve_aranan_isaretler(self):
        self.ekle(1, title="İSTANBUL ve IŞIK %50_özel")
        self.ekle(2, title="Başka bir açıklama")
        for query in ("istanbul", "ışık", "%50_özel"):
            with self.subTest(query=query):
                self.assertEqual(kartlari_listele(self.depo, query=query)["total"], 1)
        self.assertEqual(kartlari_listele(self.depo, query="%_")["total"], 0)
        self.assertEqual(kartlari_listele(self.depo, query="' OR 1=1 --")["total"], 0)

    def test_editor_notu_yalniz_guncel_revizyonda_aranir(self):
        raw = self.ekle(1)
        item = self.depo.gorunum()["items"][0]
        self.depo.editor_karti_yaz({"id": item["id"], "source_revision": 1, "title_tr": "Özel editör başlığı",
                                   "summary_tr": "Sera ile ilgisi", "benefit_tr": "Yarar"})
        self.assertEqual(kartlari_listele(self.depo, query="özel editör")["items"][0]["title"], "Özel editör başlığı")
        self.depo.kaydet("kaynak", [{**raw, "excerpt": "Değişen açıklama"}])
        self.assertEqual(kartlari_listele(self.depo, query="özel editör")["total"], 0)

    def test_konu_arsiv_ve_sinirlar(self):
        self.ekle(1)
        self.assertEqual(kartlari_listele(self.depo, include_archive=False)["total"], 0)
        self.assertEqual(kartlari_listele(self.depo, topic="tarim")["total"], 0)
        self.assertEqual(kartlari_listele(self.depo, topic="ai")["total"], 1)
        for kwargs in ({"limit": 101}, {"offset": -1}, {"topic": "bad"}, {"query": "x" * 201}):
            with self.assertRaises(ValueError):
                kartlari_listele(self.depo, **kwargs)

    def test_tek_kaydin_birden_cok_kaynak_iliskisi_kaynak_filtresinde_korunur(self):
        other = {"id": "diger", "name": "Diğer kaynak", "topic": "ai", "interval_hours": 24, "benefit": "Yarar"}
        Depo(self.depo.path, [other])
        item = self.ekle(1)
        self.ekle(2)
        self.depo.kaydet('diger', [item])
        self.assertEqual(kartlari_listele(self.depo, source='kaynak')['total'], 2)
        self.assertEqual(kartlari_listele(self.depo, source='diger')['total'], 1)
        self.assertEqual(kartlari_listele(self.depo, source='diger')['items'][0]['url'], item['url'])
        self.assertEqual(kartlari_listele(self.depo)['total'], 2)
        with self.assertRaises(ValueError):
            kartlari_listele(self.depo, source='bilinmeyen')


if __name__ == "__main__":
    unittest.main()

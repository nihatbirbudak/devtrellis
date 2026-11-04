"""Kaynak tercihlerinin kalıcılığını, tek deneme hakkını ve özel besleme sınırlarını gerçek veritabanında doğrular."""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from radar.depo import Depo
from radar.kaynak_yonetimi import KaynakSurumCatismasi, KaynakYonetimi, YenidenDenemeBekliyor, tercihleri_uygula


class KaynakYonetimiTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "radar.sqlite3"
        self.source = {"id": "deneme", "name": "Deneme", "topic": "ai", "enabled": True,
                       "kind": "rss", "interval_hours": 48, "benefit": "Araştırma için.",
                       "fetch_url": "https://example.org/feed", "url": "https://example.org/feed"}
        self.depo = Depo(self.path, [self.source])
        self.management = KaynakYonetimi(self.depo)

    def fail(self, count=3, when="2026-10-26T10:00:00+00:00"):
        with patch("radar.depo.simdi", return_value=when):
            for _ in range(count):
                self.depo.tarama_sonucu("deneme", error="Robots kuralı okumaya izin vermiyor.")

    def add(self, **changes):
        values = {"name": "Bağımsız yayın", "fetch_url": "https://feeds.example.org/rss.xml",
                  "kind": "rss", "topic": "jarvis", "publisher": "Örnek yayıncı",
                  "branches": ["Yerel ses", "Otomasyon"]}
        values.update(changes)
        return self.management.ekle(**values)

    def test_tercih_config_yenilenince_korunur_ve_haber_silinmez(self):
        self.depo.kaydet("deneme", [{"external_id": "1", "url": "https://example.org/haber", "title": "Haber"}])
        result = self.management.kaydet("deneme", enabled=False, interval_hours=96, expected_version=0)
        self.assertEqual(result["preference_version"], 1)
        restarted = Depo(self.path, [{**self.source, "name": "Yeni katalog adı", "interval_hours": 24}])
        again = KaynakYonetimi(restarted).oku("deneme")
        self.assertFalse(again["enabled"])
        self.assertEqual(again["interval_hours"], 96)
        self.assertEqual(again["name"], "Yeni katalog adı")
        self.assertEqual(restarted.gorunum()["stats"]["total"], 1)
        with restarted.baglanti() as connection:
            stored = json.loads(connection.execute("SELECT config FROM sources WHERE id='deneme'").fetchone()[0])
            applied = tercihleri_uygula(restarted.kaynaklar(connection), connection)[0]
        self.assertTrue(stored["enabled"])
        self.assertEqual(stored["interval_hours"], 24)
        self.assertFalse(applied["enabled"])
        self.assertEqual(applied["interval_hours"], 96)

    def test_acikca_ayni_ayar_kaydedilmesi_katalog_degisiminden_korur(self):
        result = self.management.kaydet("deneme", enabled=True, interval_hours=48, expected_version=0)
        self.assertEqual(result["preference_version"], 1)
        same = self.management.kaydet("deneme", enabled=True, interval_hours=48, expected_version=1)
        self.assertEqual(same["preference_version"], 1)
        Depo(self.path, [{**self.source, "enabled": False, "interval_hours": 72}])
        self.assertTrue(self.management.oku("deneme")["enabled"])
        self.assertEqual(self.management.oku("deneme")["interval_hours"], 48)

    def test_iki_pencere_ayni_surumu_ezemez(self):
        def change(hours):
            try:
                return KaynakYonetimi(self.depo).kaydet("deneme", enabled=True, interval_hours=hours,
                                                       expected_version=0)["preference_version"]
            except KaynakSurumCatismasi:
                return "cakisma"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(change, [72, 96])), [1, "cakisma"])

    def test_aralik_degisikligi_vadeyi_hesaplar_ama_hata_izini_silmez(self):
        self.fail(1)
        self.management.kaydet("deneme", enabled=True, interval_hours=24, expected_version=0)
        current = self.management.oku("deneme")
        self.assertEqual(current["next_due_at"], "2026-10-27T10:00:00+00:00")
        self.assertEqual(current["failures"], 1)
        self.assertTrue(current["last_error"])

    def test_gecersiz_ayarlar_ve_bilinmeyen_kaynak_reddedilir(self):
        for changes in ({"interval_hours": 23}, {"interval_hours": 721}, {"interval_hours": True},
                        {"interval_hours": 24.0}, {"enabled": 1}, {"expected_version": True}, {"expected_version": -1}):
            values = {"enabled": True, "interval_hours": 48, "expected_version": 0, **changes}
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.management.kaydet("deneme", **values)
        with self.assertRaises(KeyError):
            self.management.oku("bilinmeyen")
        self.assertEqual(self.management.oku("deneme")["preference_version"], 0)

    def test_yeniden_deneme_bes_dakika_bekler_ve_hatalari_silmez(self):
        self.fail()
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:04:59+00:00"):
            with self.assertRaises(YenidenDenemeBekliyor) as error:
                self.management.yeniden_dene("deneme", expected_version=0)
            self.assertEqual(error.exception.available_at, "2026-10-26T10:05:00+00:00")
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:05:00+00:00"):
            accepted = self.management.yeniden_dene("deneme", expected_version=0)
            self.assertTrue(accepted["retry_pending"])
            self.assertEqual(accepted["failures"], 3)
            self.assertTrue(accepted["last_error"])
            self.assertEqual(accepted["next_due_at"], "2026-10-26T10:05:00+00:00")
            with self.assertRaises(YenidenDenemeBekliyor):
                self.management.yeniden_dene("deneme", expected_version=1)
            with self.assertRaises(KaynakSurumCatismasi):
                self.management.yeniden_dene("deneme", expected_version=0)

    def test_bekleyen_hak_yeniden_acilir_ve_iki_isci_yalniz_bir_kez_alir(self):
        self.fail()
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:05:00+00:00"):
            self.management.yeniden_dene("deneme", expected_version=0)
        reopened = KaynakYonetimi(Depo(self.path, [self.source]))
        self.assertTrue(reopened.oku("deneme")["retry_pending"])
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:06:00+00:00"), ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(reopened.yeniden_deneme_al, ["deneme", "deneme"])), [True, False])
        current = reopened.oku("deneme")
        self.assertFalse(current["retry_pending"])
        self.assertEqual(current["failures"], 3)
        self.assertEqual(current["preference_version"], 2)
        self.assertEqual(current["last_retry_at"], "2026-10-26T10:06:00+00:00")
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:10:59+00:00"):
            with self.assertRaises(YenidenDenemeBekliyor):
                reopened.yeniden_dene("deneme", expected_version=2)
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:11:00+00:00"):
            self.assertTrue(reopened.yeniden_dene("deneme", expected_version=2)["retry_pending"])

    def test_saglikli_ve_kapali_kaynak_yeniden_denenmez(self):
        with self.assertRaises(ValueError):
            self.management.yeniden_dene("deneme", expected_version=0)
        self.fail()
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:05:00+00:00"):
            self.management.yeniden_dene("deneme", expected_version=0)
            closed = self.management.kaydet("deneme", enabled=False, interval_hours=48, expected_version=1)
            self.assertFalse(closed["retry_pending"])
            self.assertFalse(self.management.yeniden_deneme_al("deneme"))
            with self.assertRaises(ValueError):
                self.management.yeniden_dene("deneme", expected_version=2)

    def test_istekten_sonra_biten_kontrol_bekleme_suresini_asamaz(self):
        self.fail()
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:05:00+00:00"):
            self.management.yeniden_dene("deneme", expected_version=0)
        self.fail(1, when="2026-10-26T10:06:00+00:00")
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:06:01+00:00"):
            self.assertFalse(self.management.yeniden_deneme_al("deneme"))
            self.assertTrue(self.management.oku("deneme")["retry_pending"])
            self.assertEqual(self.management.oku("deneme")["next_due_at"], "2026-10-26T10:11:00+00:00")
        with patch("radar.kaynak_yonetimi.simdi", return_value="2026-10-26T10:11:00+00:00"):
            self.assertTrue(self.management.yeniden_deneme_al("deneme"))
            self.assertEqual(self.management.oku("deneme")["failures"], 4)

    def test_ozel_atom_beslemesi_tam_tek_hostla_kalici_kaydedilir(self):
        added = self.add(kind="atom", fetch_url="https://Feeds.Example.org:443/rss.xml", language="tr")
        self.assertTrue(added["custom_source"])
        self.assertEqual(added["fetch_url"], "https://feeds.example.org/rss.xml")
        self.assertEqual(added["fetch_hosts"], ["feeds.example.org"])
        self.assertEqual(added["item_hosts"], ["feeds.example.org"])
        self.assertEqual(added["kind"], "atom")
        reopened = KaynakYonetimi(Depo(self.path, [self.source]))
        self.assertEqual(reopened.ozel_kaynaklar()[0]["id"], added["id"])
        reopened.kaydet(added["id"], enabled=False, interval_hours=72, expected_version=0)
        self.assertFalse(KaynakYonetimi(Depo(self.path, [self.source])).oku(added["id"])["enabled"])

    def test_ayni_besleme_yarisinda_tek_kayit_olusur(self):
        def add(_):
            try:
                return self.add()["id"]
            except ValueError:
                return "tekrar"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add, range(2)))
        self.assertEqual(results.count("tekrar"), 1)
        self.assertEqual(len(self.management.ozel_kaynaklar()), 1)
        with self.assertRaises(ValueError):
            self.add(fetch_url="https://FEEDS.EXAMPLE.ORG:443/rss.xml")
        with self.assertRaises(ValueError):
            self.add(fetch_url=self.source["fetch_url"])

    def test_guvensiz_ve_gecersiz_yeni_kaynaklar_hic_yazilmaz(self):
        changes = [{"fetch_url": value} for value in ["http://feeds.example.org/rss", "https://127.0.0.1/rss",
                   "https://[::1]/rss", "https://local.internal/rss", "https://localhost/rss",
                   "https://user:pass@feeds.example.org/rss", "https://feeds.example.org:8443/rss",
                   "https://feeds.example.org/rss#part", "https://feeds.example.org/rss?token=secret",
                   "https://feeds.example.org/\nfeed", "https://*.example.org/rss", "https://feeds.example.org/\\rss"]]
        changes.extend([{"kind": "html"}, {"name": " "}, {"publisher": "\nYayıncı"},
                        {"branches": []}, {"branches": ["ok\nno"]}, {"branches": [1]},
                        {"topic": "bilinmeyen"}, {"interval_hours": 23}, {"source_type": "rastgele"}])
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.add(**change)
        self.assertEqual(self.management.ozel_kaynaklar(), [])
        self.assertEqual(len(self.depo.kaynaklar()), 1)

    def test_ozel_kaynak_kayit_hatasi_yarim_kayit_birakmaz(self):
        with patch.object(self.depo, "olay_yaz", side_effect=RuntimeError("Deneme hatası.")):
            with self.assertRaises(RuntimeError):
                self.add()
        self.assertEqual(self.management.ozel_kaynaklar(), [])
        self.assertEqual(len(self.depo.kaynaklar()), 1)

    def test_bozulmus_ozel_kayit_host_yetkisini_genisletemez(self):
        added = self.add()
        with self.depo.baglanti(write=True) as connection:
            config = json.loads(connection.execute("SELECT config FROM custom_sources").fetchone()[0])
            config["fetch_hosts"].append("another.example.org")
            connection.execute("UPDATE custom_sources SET config=? WHERE source_id=?",
                               (json.dumps(config), added["id"]))
        with self.assertRaises(ValueError):
            self.management.ozel_kaynaklar()


if __name__ == "__main__":
    unittest.main()

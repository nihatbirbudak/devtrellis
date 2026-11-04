"""Yerel kayıtların korunmasını, bütün arşivde aramayı ve seçerek paylaşım sınırlarını doğrular."""

import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from radar.depo import Depo
from radar.gelistirici_alani import (GelistiriciAlani, PAKET_ALANLARI, PlatformSurumCatismasi,
                                    guvenli_url, paket_metni)


class GelistiriciAlaniTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = {"id": "source", "name": "Kaynak Yayıncısı", "topic": "ai", "enabled": True,
                       "branches": ["Açık kaynak", "Bilgisayarlı görü"], "benefit": "Teknik gelişmeler",
                       "interval_hours": 24}
        self.depo = Depo(Path(temporary.name) / "test.sqlite3", [self.source])
        self.area = GelistiriciAlani(self.depo)

    def entry(self, **changes):
        record = {"kind": "research", "title": "Açık kaynak araştırma araçları", "summary": "Kaynaklı kısa özet.",
                  "body": "İncelenen kaynakların söylediği bulgular.", "url": "https://example.org/research",
                  "tags": ["Araştırma", "Python"], "sources": [{"title": "Birincil kaynak", "url": "https://example.org/docs"}],
                  "author": "Örnek geliştirici", "license": "CC-BY-4.0"}
        record.update(changes)
        return self.area.olustur(record)

    def packet(self):
        entry = self.entry()
        return self.area.onizle(entry["id"], {"expected_version": entry["version"]})["packet"]

    def test_ayarlar_ve_taslaklar_yeniden_acilinca_korunur(self):
        settings = self.area.ayarlari_kaydet({"interests": ["Python", "Robotik", "python"], "expected_version": 0})
        self.assertEqual(settings, {"interests": ["Python", "Robotik"], "version": 1})
        created = self.area.olustur({"kind": "project", "title": "Henüz özel taslak"})
        reopened = GelistiriciAlani(Depo(self.depo.path, [self.source])).gorunum()
        self.assertEqual(reopened["settings"], settings)
        self.assertEqual(reopened["entries"], [created])
        self.assertEqual(created["origin"], "local")
        self.assertIsNone(created["imported_at"])
        self.assertEqual(reopened["mode"], "local")
        self.assertEqual(reopened["coverage"], {"source_count": 1, "checked_sources": 0, "last_checked_at": None,
                                               "successful_sources": 0, "failed_sources": 0, "last_success_at": None})

    def test_eski_ayar_ve_kayit_surumleri_uzerine_yazamaz(self):
        self.area.ayarlari_kaydet({"interests": ["Veri"], "expected_version": 0})
        with self.assertRaises(PlatformSurumCatismasi):
            self.area.ayarlari_kaydet({"interests": [], "expected_version": 0})
        entry = self.entry()
        updated = self.area.guncelle(entry["id"], {"kind": "research", "title": "Yeni başlık", "expected_version": 1})
        self.assertEqual(updated["version"], 2)
        for operation, payload in ((self.area.guncelle, {"kind": "project", "title": "Eski pencere", "expected_version": 1}),
                                   (self.area.arsivle, {"archived": True, "expected_version": 1}),
                                   (self.area.onizle, {"expected_version": 1})):
            with self.subTest(operation=operation.__name__), self.assertRaises(PlatformSurumCatismasi):
                operation(entry["id"], payload)
        self.assertEqual(self.area.oku(entry["id"]), updated)

    def test_iki_pencere_ayni_surumu_birlikte_degistiremez(self):
        entry = self.entry()

        def update(title):
            try:
                return self.area.guncelle(entry["id"], {"kind": "research", "title": title, "expected_version": 1})["version"]
            except PlatformSurumCatismasi:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, ["İlk pencere", "İkinci pencere"]))
        self.assertCountEqual(results, [2, "conflict"])

    def test_arsiv_geri_alinir_ve_icerik_tarihini_degistirmez(self):
        entry = self.entry()
        archived = self.area.arsivle(entry["id"], {"archived": True, "expected_version": 1})
        self.assertTrue(archived["archived"])
        self.assertEqual(archived["updated_at"], entry["updated_at"])
        self.assertEqual(self.area.gorunum()["entries"][0], archived)
        restored = self.area.arsivle(entry["id"], {"archived": False, "expected_version": 2})
        self.assertFalse(restored["archived"])
        self.assertEqual(restored["version"], 3)

    def test_paylasim_yalniz_secilen_kaydi_tasir(self):
        self.area.ayarlari_kaydet({"interests": ["Özel ilgi bilgisi"], "expected_version": 0})
        self.entry(title="Paylaşılmayan özel proje", body="Gizli kişisel not")
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE user_settings SET settings=? WHERE id=1", ('{"private":"Özel profil bilgisi"}',))
        packet = self.packet()
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertEqual(set(packet), PAKET_ALANLARI)
        for private in ("Özel ilgi bilgisi", "Paylaşılmayan özel proje", "Gizli kişisel not", "Özel profil bilgisi"):
            self.assertNotIn(private, serialized)
        self.assertNotIn("id", packet)
        self.assertNotIn("origin", packet)

    def test_paylasim_yazar_lisans_ve_ture_uygun_kaynak_gerektirir(self):
        for changes in ({"author": ""}, {"license": None}, {"sources": []},
                        {"kind": "project", "url": ""}):
            with self.subTest(changes=changes):
                entry = self.entry(**changes)
                with self.assertRaises(ValueError):
                    self.area.onizle(entry["id"], {"expected_version": 1})

    def test_ice_aktarma_onizlemesi_yazmaz_ayni_paket_tekrarlanmaz(self):
        packet = self.packet()
        before = self.area.gorunum()["entries"]
        preview = self.area.ice_aktarma_onizle({"packet": packet})
        self.assertEqual(preview["packet"], packet)
        self.assertEqual(self.area.gorunum()["entries"], before)
        first = self.area.ice_aktar({"packet": packet})
        reordered = dict(reversed(list(packet.items())))
        second = self.area.ice_aktar({"packet": reordered})
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["entry"], second["entry"])
        self.assertEqual(first["entry"]["origin"], "imported")
        self.assertIsNotNone(first["entry"]["imported_at"])
        self.assertNotEqual(first["entry"]["id"], before[0]["id"])
        self.assertIn("doğrulanmış değildir", preview["notice"])

    def test_ice_aktarilan_yazarin_kaydi_degistirilemez_arsivlenebilir(self):
        entry = self.area.ice_aktar({"packet": self.packet()})["entry"]
        with self.assertRaises(ValueError):
            self.area.guncelle(entry["id"], {"kind": "project", "title": "Başkası yazmış gibi", "expected_version": 1})
        self.assertEqual(self.area.oku(entry["id"]), entry)
        archived = self.area.arsivle(entry["id"], {"archived": True, "expected_version": 1})
        self.assertTrue(archived["archived"])
        exported = self.area.onizle(entry["id"], {"expected_version": 2})["packet"]
        self.assertEqual(exported["updated_at"], entry["updated_at"])

    def test_tehlikeli_ve_yerel_url_kayit_ve_importta_reddedilir(self):
        urls = ["javascript:alert(1)", "file:///C:/private", "https://user:pass@example.org/", "http://localhost:8781/",
                "http://LOCALHOST./", "http://example.local/", "http://192.168.1.10/", "http://127.0.0.1/",
                "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://169.254.169.254/latest/", "http://100.64.0.1/",
                "http://2130706433/", "http://0177.0.0.1/", "http://0x7f.0.0.1/", "http://intranet/",
                "https://example.org/\nprivate", "https://example.org/%0d%0aheader", "https://example.org\\@localhost/",
                "https://example.org:broken/", "https://example.org:65536/"]
        packet = self.packet()
        before = self.area.gorunum()["entries"]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    self.entry(url=url)
                damaged = copy.deepcopy(packet)
                damaged["sources"][0]["url"] = url
                with self.assertRaises(ValueError):
                    self.area.ice_aktar({"packet": damaged})
        self.assertEqual(self.area.gorunum()["entries"], before)
        self.assertEqual(guvenli_url("https://EXAMPLE.org/docs"), "https://example.org/docs")
        self.assertEqual(guvenli_url("https://8.8.8.8/"), "https://8.8.8.8/")

    def test_bilinmeyen_alan_ve_hatali_tipler_importta_hic_yazmaz(self):
        packet = self.packet()
        invalid = []
        for changes in ({"id": "attacker"}, {"origin": "local"}, {"schema_version": True}, {"schema_version": 2},
                        {"created_at": "dün"}, {"created_at": "2026-01-01T00:00:00"}, {"created_at": "2026-01-01"},
                        {"updated_at": "2000-01-01T00:00:00+00:00"},
                        {"tags": [{"execute": "code"}]}, {"kind": "job"}, {"author": None}, {"license": "unknown"}):
            invalid.append({**copy.deepcopy(packet), **changes})
        nested = copy.deepcopy(packet)
        nested["sources"][0]["private"] = "unknown"
        invalid.append(nested)
        before = self.area.gorunum()["entries"]
        for damaged in invalid:
            with self.subTest(damaged=damaged), self.assertRaises(ValueError):
                self.area.ice_aktar({"packet": damaged})
        with self.assertRaises(ValueError):
            self.area.ice_aktar({"packet": packet, "execute": True})
        self.assertEqual(self.area.gorunum()["entries"], before)

    def test_cok_buyuk_surum_ve_sayfalama_sayilari_reddedilir(self):
        with self.assertRaises(ValueError):
            self.area.ayarlari_kaydet({"interests": [], "expected_version": 2 ** 64})
        with self.assertRaises(ValueError):
            self.area.akis(offset=2 ** 64)

    def test_alan_sinirlari_ve_toplam_utf8_paket_siniri_uygulanir(self):
        for changes in ({"title": "a" * 161}, {"summary": "a" * 601}, {"body": "a" * 12001},
                        {"author": "a" * 101}, {"tags": ["a"] * 13}, {"tags": ["a" * 41]},
                        {"sources": [{"title": "x", "url": "https://example.org/"}] * 11}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.entry(**changes)
        entry = self.entry(body="界" * 12000)
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            self.area.onizle(entry["id"], {"expected_version": 1})
        packet = self.packet()
        packet["body"] = "界" * 12000
        before = self.area.gorunum()["entries"]
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            self.area.ice_aktar({"packet": packet})
        self.assertEqual(self.area.gorunum()["entries"], before)
        self.assertLessEqual(len(paket_metni({"packet": self.packet()}).encode("utf-8")), 32768)

    def test_akis_tum_arsivde_arar_sonra_sayfalar(self):
        incoming = [{"external_id": str(index), "url": f"https://example.org/post/{index}",
                     "title": f"Gelişme {index:03}", "excerpt": "Bilimsel yöntem", "language": "tr",
                     "published_at": "2026-10-01T00:00:00+00:00"} for index in range(210)]
        incoming[209]["excerpt"] = "Sayfalamanın dışındaki benzersiz sonuç"
        self.depo.kaydet("source", incoming)
        result = self.area.akis(query="benzersiz")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["title"], "Gelişme 209")
        first = self.area.akis(limit=30)
        second = self.area.akis(offset=30, limit=30)
        self.assertEqual((first["total"], second["total"]), (210, 210))
        self.assertFalse({item["id"] for item in first["items"]} & {item["id"] for item in second["items"]})
        self.assertEqual(self.area.akis(query="%'") ["total"], 0)

    def test_akis_yayin_tarihine_gore_siralar_bilinmeyeni_sona_alir(self):
        incoming = [{"external_id": name, "url": f"https://example.org/{name}", "title": name,
                     "published_at": date} for name, date in (("old", "2026-10-01"), ("unknown", None),
                     ("new", "2026-11-03T21:00:00-03:00"), ("middle", "2026-11-03T23:00:00+00:00"))]
        self.depo.kaydet("source", incoming)
        results = self.area.akis()["items"]
        self.assertEqual([item["title"] for item in results], ["new", "middle", "old", "unknown"])
        self.assertIsNone(results[-1]["published_at"])
        self.assertEqual(results[0]["source_name"], "Kaynak Yayıncısı")
        self.assertIn("last_seen_at", results[0])

    def test_ilgi_alanlari_kaynak_dali_konu_ve_icerikte_eslesir(self):
        self.depo.kaydet("source", [{"url": "https://example.org/duyuru", "title": "Python yenilikleri", "excerpt": "Veri işlemleri"}])
        for interest in ("AÇIK KAYNAK", "Bilgisayarlı görü", "AI", "python", "İŞLEMLERİ"):
            with self.subTest(interest=interest):
                self.assertEqual(self.area.akis(interest=interest)["total"], 1)
        self.assertEqual(self.area.akis(interest="olmayan konu")["total"], 0)
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET last_checked_at=? WHERE id='source'", ("2026-11-04T09:00:00+00:00",))
        self.assertEqual(self.area.gorunum()["coverage"], {"source_count": 1, "checked_sources": 1,
                                                          "last_checked_at": "2026-11-04T09:00:00+00:00",
                                                          "successful_sources": 0, "failed_sources": 0,
                                                          "last_success_at": None})

    def test_basarisiz_kontrol_basarili_kaynak_sayilmaz(self):
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET last_checked_at=?,last_error='Timeout' WHERE id='source'",
                               ("2026-11-04T09:00:00+00:00",))
        coverage = self.area.gorunum()["coverage"]
        self.assertEqual(coverage["checked_sources"], 1)
        self.assertEqual(coverage["successful_sources"], 0)
        self.assertEqual(coverage["failed_sources"], 1)
        self.assertIsNone(coverage["last_success_at"])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET last_success_at=?,last_error=NULL WHERE id='source'",
                               ("2026-11-04T10:00:00+00:00",))
        recovered = self.area.gorunum()["coverage"]
        self.assertEqual(recovered["successful_sources"], 1)
        self.assertEqual(recovered["failed_sources"], 0)
        self.assertEqual(recovered["last_success_at"], "2026-11-04T10:00:00+00:00")

    def test_yerel_eski_haber_baglantisi_tiklanabilir_olarak_verilmez(self):
        self.depo.kaydet("source", [{"url": "http://127.0.0.1/private", "title": "Eski yerel veri"}])
        self.assertEqual(self.area.akis()["items"][0]["url"], "")

    def test_okunabilir_konu_adi_ve_kaynak_turu_ilgi_aramasinda_bulunur(self):
        source = {**self.source, "branches": [], "source_type": "Açık kaynak", "publisher": "Örnek topluluk"}
        Depo(self.depo.path, [source])
        self.depo.kaydet("source", [{"url": "https://example.org/new", "title": "Yeni sürüm"}])
        for interest in ("Yapay zekâ", "Açık kaynak", "Örnek topluluk", "Kaynak Yayıncısı"):
            with self.subTest(interest=interest):
                self.assertEqual(self.area.akis(interest=interest)["total"], 1)


if __name__ == "__main__":
    unittest.main()

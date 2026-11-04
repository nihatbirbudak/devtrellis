"""Haberlerin arşivini, kaynak izini ve kullanıcı kararlarını ağ kullanmadan doğrular."""

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from radar.depo import Depo


AN = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def kaynak(kimlik="deneme", konu="ai"):
    return {
        "id": kimlik, "name": "Deneme kaynağı", "topic": konu,
        "url": "https://example.org/", "fetch_url": "https://example.org/feed.xml",
        "kind": "rss", "interval_hours": 12, "benefit": "Yerel model araştırmasına katkı.",
        "enabled": True,
    }


def haber(kimlik="haber-1", **degisiklikler):
    sonuc = {
        "external_id": kimlik, "url": "https://example.org/" + kimlik,
        "title": "Yerel modeller için yeni araştırma", "excerpt": "Kaynağın kısa açıklaması.",
        "published_at": (AN - timedelta(days=1)).isoformat(), "updated_at": None,
        "event_at": None, "evidence_type": "Birincil araştırma", "language": "tr",
    }
    sonuc.update(degisiklikler)
    return sonuc


class DepoKabulTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.dosya = Path(self.gecici.name) / "radar.sqlite3"
        self.kaynaklar = [kaynak(), kaynak("ikinci", "robotik")]
        self.saat = patch("radar.depo.simdi", return_value=AN.isoformat())
        self.saat.start()
        self.addCleanup(self.saat.stop)
        self.depo = Depo(self.dosya, self.kaynaklar)

    def ekle(self, **degisiklikler):
        self.depo.kaydet("deneme", [haber(**degisiklikler)])
        return self.depo.gorunum()["items"][0]

    def test_ilk_aktarim_yeni_yayinlari_bile_arsivde_tutar(self):
        self.depo.kaydet("deneme", [haber(), haber("eski", published_at="2020-01-01T00:00:00Z")])
        gorunum = self.depo.gorunum()
        self.assertEqual(gorunum["stats"]["total"], 2)
        self.assertEqual(gorunum["stats"]["new_count"], 0)
        self.assertTrue(all(item["is_archive"] for item in gorunum["items"]))

    def test_eksik_besleme_tarihi_onceki_acik_tarih_kanitini_silmez(self):
        ilk = self.ekle(published_at="2026-10-22", event_at="2026-10-21")
        self.depo.kaydet("deneme", [haber(published_at=None, event_at=None, excerpt="Yeni açıklama")])
        kart = self.depo.gorunum()["items"][0]
        self.assertEqual(kart["published_at"], "2026-10-22")
        self.assertEqual(kart["event_at"], "2026-10-21")
        self.assertEqual(self.depo.revizyonlar(ilk["id"])[0]["content"]["published_at"], "2026-10-22")

    def test_turkce_degerlendirme_ham_kaynagi_korur_degisen_kaynakta_kullanilmaz(self):
        ilk = self.ekle()
        card = {"id": ilk["id"], "source_revision": 1, "title_tr": "Türkçe başlık",
                "summary_tr": "Kısa açıklama", "benefit_tr": "Proje ilgisi", "limitation": "Bağımsız test yok"}
        self.depo.editor_karti_yaz(card)
        kart = self.depo.gorunum()["items"][0]
        self.assertEqual(kart["title"], "Türkçe başlık")
        self.assertEqual(kart["source_title"], ilk["title"])
        self.assertEqual(self.depo.disa_aktar()["items"][0]["title"], ilk["title"])
        self.depo.kaydet("deneme", [haber(excerpt="Kaynak değişti")])
        self.assertEqual(self.depo.gorunum()["items"][0]["summary_status"], "kaynak_metni")
        with self.assertRaises(ValueError):
            self.depo.editor_karti_yaz(card)

    def test_sonraki_tarama_yalniz_yakin_gecmiste_yayimlanani_yeni_sayar(self):
        self.ekle()
        self.depo.tarama_sonucu("deneme")
        tarihler = {
            "yeni": AN - timedelta(hours=1),
            "sinir": AN - timedelta(days=14),
            "eski": AN - timedelta(days=14, seconds=1),
            "gelecek": AN + timedelta(seconds=1),
        }
        self.depo.kaydet("deneme", [haber(ad, published_at=deger.isoformat()) for ad, deger in tarihler.items()]
                        + [haber("tarihsiz", published_at=None), haber("bozuk", published_at="bilinmiyor")])
        gorunum = self.depo.gorunum()
        yeni = {item["url"].rsplit("/", 1)[-1] for item in gorunum["items"] if not item["is_archive"]}
        self.assertEqual(yeni, {"yeni", "sinir"})
        self.assertEqual(gorunum["stats"]["new_count"], 2)

    def test_tekrar_tarama_ve_takip_parametreleri_kart_cogaltmaz(self):
        self.ekle(url="https://EXAMPLE.org/haber-1?x=1&utm_source=bulten#bolum")
        sonuc = self.depo.kaydet("deneme", [haber("baska-kayit", url="https://example.org/haber-1?x=1&fbclid=abc")])
        gorunum = self.depo.gorunum()
        self.assertEqual(sonuc, {"added": 0, "changed": 0})
        self.assertEqual(gorunum["stats"]["total"], 1)
        self.assertEqual(gorunum["items"][0]["url"], "https://example.org/haber-1?x=1")
        self.assertEqual(len(self.depo.revizyonlar(gorunum["items"][0]["id"])), 1)
        self.assertEqual(len(self.depo.disa_aktar()["source_entries"]), 2)

    def test_icerik_degisimleri_eski_kaniti_korur_ve_yeniden_acilinca_kalir(self):
        ilk = self.ekle()
        self.depo.tarama_sonucu("deneme", etag='"ornek-1"', modified="Sun, 25 Oct 2026 12:00:00 GMT")
        self.depo.kaydet("deneme", [haber(title="Düzeltilmiş araştırma başlığı", excerpt="Düzeltilmiş sonuç.")])
        yeniden = Depo(self.dosya, self.kaynaklar)
        kart = yeniden.gorunum()["items"][0]
        gecmis = yeniden.revizyonlar(ilk["id"])
        self.assertEqual(kart["id"], ilk["id"])
        self.assertEqual(kart["revision"], 2)
        self.assertEqual(kart["title"], "Düzeltilmiş araştırma başlığı")
        self.assertEqual([satir["revision"] for satir in gecmis], [2, 1])
        self.assertEqual(gecmis[-1]["content"]["title"], ilk["title"])
        self.assertEqual(yeniden.kaynaklar()[0]["etag"], '"ornek-1"')

    def test_sabit_kaynak_kimligiyle_degisen_baglanti_son_revizyona_yansir(self):
        ilk = self.ekle(url="https://example.org/makale-v1")
        sonuc = self.depo.kaydet("deneme", [haber(url="https://example.org/makale-v2")])
        kart = self.depo.gorunum()["items"][0]
        self.assertEqual(sonuc, {"added": 0, "changed": 1})
        self.assertEqual(kart["id"], ilk["id"])
        self.assertEqual(kart["url"], "https://example.org/makale-v2")
        self.assertEqual(self.depo.revizyonlar(ilk["id"])[-1]["content"]["url"], "https://example.org/makale-v1")

    def test_yalniz_olay_ve_guncelleme_tarihi_degisse_de_kanit_korunur(self):
        ilk = self.ekle()
        olay = "2026-10-24T09:00:00+00:00"
        guncelleme = "2026-10-26T10:00:00+00:00"
        self.depo.kaydet("deneme", [haber(event_at=olay, updated_at=guncelleme)])
        kart = self.depo.gorunum()["items"][0]
        self.assertEqual(kart["event_at"], olay)
        self.assertEqual(kart["updated_at"], guncelleme)
        self.assertGreater(kart["revision"], ilk["revision"])
        self.assertEqual(self.depo.revizyonlar(ilk["id"])[0]["content"]["event_at"], olay)

    def test_ayni_haberi_baska_kaynakta_gormek_asil_karti_ezmez(self):
        ilk = self.ekle()
        self.depo.kaydet("ikinci", [haber("kopya", url=ilk["url"], title="Başka sitenin başlığı")])
        gorunum = self.depo.gorunum()
        self.assertEqual(gorunum["stats"]["total"], 1)
        self.assertEqual(gorunum["items"][0]["title"], ilk["title"])
        kaynak_izleri = self.depo.disa_aktar()["source_entries"]
        self.assertEqual({satir["source_id"] for satir in kaynak_izleri}, {"deneme", "ikinci"})

    def test_eszamanli_arastirma_secimleri_tek_is_ve_tek_kayit_olusturur(self):
        kart = self.ekle()
        with ThreadPoolExecutor(max_workers=8) as workers:
            isler = list(workers.map(lambda _: self.depo.arastirma_sec(kart["id"]), range(16)))
        self.assertEqual(len({islem["id"] for islem in isler}), 1)
        self.assertEqual(len(self.depo.gorunum()["jobs"]), 1)
        self.assertEqual(isler[0]["status"], "bekliyor")
        self.assertIsNone(isler[0]["owner"])
        kayitlar = [olay for olay in self.depo.disa_aktar()["audit"] if olay["kind"] == "arastirma_secildi"]
        self.assertEqual(len(kayitlar), 1)

    def test_sonraya_birakma_geri_alinabilir_ve_tercih_gecmisi_silinmez(self):
        kart = self.ekle()
        self.depo.geri_bildirim(kart["id"], "sonra", "Haftalık görüşmede bak.")
        ertelenmis = self.depo.gorunum()["items"][0]
        self.assertEqual(ertelenmis["feedback"], "sonra")
        self.assertEqual(datetime.fromisoformat(ertelenmis["snoozed_until"]), AN + timedelta(days=7))
        self.depo.geri_bildirim(kart["id"], "yok")
        son = self.depo.gorunum()["items"][0]
        self.assertEqual(son["feedback"], "yok")
        self.assertIsNone(son["snoozed_until"])
        aktarim = self.depo.disa_aktar()
        self.assertEqual([olay["value"] for olay in aktarim["feedback_events"]], ["sonra", "yok"])
        self.assertEqual(aktarim["feedback_events"][0]["note"], "Haftalık görüşmede bak.")
        self.assertEqual(sum(olay["kind"] == "tercih" for olay in aktarim["audit"]), 2)

    def test_gecersiz_tercih_ve_bilinmeyen_kart_yeni_kayit_uretmez(self):
        kart = self.ekle()
        with self.assertRaises(ValueError):
            self.depo.geri_bildirim(kart["id"], "otomatik_dagit")
        with self.assertRaises(ValueError):
            self.depo.geri_bildirim(kart["id"], "yararli", "a" * 501)
        with self.assertRaises(KeyError):
            self.depo.geri_bildirim("olmayan", "yararli")
        with self.assertRaises(KeyError):
            self.depo.arastirma_sec("olmayan")
        self.assertEqual(self.depo.disa_aktar()["feedback_events"], [])
        self.assertEqual(self.depo.gorunum()["jobs"], [])

    def test_tehlikeli_baglanti_aktarimi_tum_islemi_geri_alir(self):
        with self.assertRaises(ValueError):
            self.depo.kaydet("deneme", [haber(), haber("tehlikeli", url="javascript:alert(1)")])
        self.assertEqual(self.depo.gorunum()["stats"]["total"], 0)

    def test_yedek_bagimsiz_acilir_ve_tum_kanitlari_korur(self):
        kart = self.ekle()
        self.depo.kaydet("deneme", [haber(title="İkinci sürüm")])
        self.depo.geri_bildirim(kart["id"], "yararli", "İncelenecek.")
        job = self.depo.arastirma_sec(kart["id"])
        with self.depo.baglanti(write=True) as connection:
            connection.execute("INSERT INTO research_notes VALUES(?,?,?,?,?)",
                               (job["id"], 1, 2, "İkinci sürüm incelendi.", AN.isoformat()))
        self.depo.tarama_sonucu("deneme")
        once = self.depo.disa_aktar()
        yedek = Path(self.gecici.name) / "yedek" / "radar.sqlite3"
        self.depo.yedekle(yedek)
        self.depo.geri_bildirim(kart["id"], "gereksiz")
        geri_yuklenen = Depo(yedek, self.kaynaklar).disa_aktar()
        self.assertEqual(geri_yuklenen, once)
        self.assertEqual(geri_yuklenen["items"][0]["feedback"], "yararli")
        self.assertEqual(geri_yuklenen["research_notes"][0]["body"], "İkinci sürüm incelendi.")

    def test_kayit_siniri_kaydedilen_ertelenen_ve_arastirma_kartlarini_gizlemez(self):
        self.depo.kaydet("deneme", [haber(ad, published_at="2000-01-01")
                                    for ad in ("kayitli", "ertelenen", "arastirma", "arsiv")])
        eski = {kart["url"].rsplit("/", 1)[-1]: kart for kart in self.depo.gorunum()["items"]}
        self.depo.geri_bildirim(eski["kayitli"]["id"], "yararli")
        self.depo.geri_bildirim(eski["ertelenen"]["id"], "sonra")
        self.depo.arastirma_sec(eski["arastirma"]["id"])
        self.depo.kaydet("deneme", [haber(f"guncel-{sira}") for sira in range(510)])
        gorunum = self.depo.gorunum()
        kimlikler = {kart["id"] for kart in gorunum["items"]}
        self.assertEqual(gorunum["stats"]["total"], 514)
        self.assertEqual(len(kimlikler), 503)
        self.assertEqual(gorunum["stats"]["saved_count"], 1)
        self.assertEqual(gorunum["stats"]["later_count"], 1)
        self.assertTrue({eski[ad]["id"] for ad in ("kayitli", "ertelenen", "arastirma")} <= kimlikler)
        self.assertNotIn(eski["arsiv"]["id"], kimlikler)
        self.assertEqual(len(self.depo.disa_aktar()["items"]), 514)
        with patch("radar.depo.simdi", return_value=(AN + timedelta(days=8)).isoformat()):
            sonraki = self.depo.gorunum()
        self.assertEqual(sonraki["stats"]["later_count"], 0)
        self.assertEqual(len(sonraki["items"]), 502)

    def test_ayni_anda_arastirma_secimi_kart_ve_is_gorunumunu_karistirmaz(self):
        kart = self.ekle()
        baglanti = self.depo.baglanti
        araya_girdi = False
        hatalar = []

        def araya_secim(statement):
            nonlocal araya_girdi
            if not araya_girdi and statement.startswith("SELECT j.*"):
                araya_girdi = True
                try:
                    self.depo.arastirma_sec(kart["id"])
                except Exception as exc:
                    hatalar.append(exc)

        @contextmanager
        def araya_yazilan_baglanti(write=False):
            with baglanti(write=write) as connection:
                if not write:
                    connection.set_trace_callback(araya_secim)
                yield connection

        with patch.object(self.depo, "baglanti", araya_yazilan_baglanti):
            gorunum = self.depo.gorunum()
        self.assertTrue(araya_girdi)
        self.assertEqual(hatalar, [])
        self.assertFalse(gorunum["items"][0]["selected"])
        self.assertEqual(gorunum["jobs"], [])
        sonraki = self.depo.gorunum()
        self.assertTrue(sonraki["items"][0]["selected"])
        self.assertEqual(len(sonraki["jobs"]), 1)

    def test_tarama_surerken_disa_aktarim_ayni_veritabani_anini_korur(self):
        self.ekle()
        baglanti = self.depo.baglanti
        araya_girdi = False
        hatalar = []

        def araya_tarama(statement):
            nonlocal araya_girdi
            if not araya_girdi and statement == "SELECT * FROM source_entries":
                araya_girdi = True
                try:
                    self.depo.kaydet("deneme", [haber("aktarim-surerken-gelen")])
                    self.depo.tarama_sonucu("deneme")
                except Exception as exc:
                    hatalar.append(exc)

        @contextmanager
        def araya_yazilan_baglanti(write=False):
            with baglanti(write=write) as connection:
                if not write:
                    connection.set_trace_callback(araya_tarama)
                yield connection

        with patch.object(self.depo, "baglanti", araya_yazilan_baglanti):
            aktarim = self.depo.disa_aktar()
        self.assertTrue(araya_girdi)
        self.assertEqual(hatalar, [])
        self.assertEqual(len(aktarim["items"]), 1)
        self.assertEqual(len(aktarim["source_entries"]), 1)
        kaynak_sonucu = next(kaynak for kaynak in aktarim["sources"] if kaynak["id"] == "deneme")
        self.assertEqual(kaynak_sonucu["item_count"], 1)
        self.assertIsNone(kaynak_sonucu["last_success_at"])
        self.assertEqual(self.depo.gorunum()["stats"]["total"], 2)

    def test_basarili_cevapta_kaldirilan_onbellek_basliklari_silinir(self):
        self.depo.tarama_sonucu("deneme", etag='"onceki"', modified="Sun, 25 Oct 2026 12:00:00 GMT")
        self.depo.tarama_sonucu("deneme", error="Geçici bağlantı sorunu")
        kaynak_sonucu = next(kaynak for kaynak in self.depo.kaynaklar() if kaynak["id"] == "deneme")
        self.assertEqual(kaynak_sonucu["etag"], '"onceki"')
        self.depo.tarama_sonucu("deneme", etag=None, modified=None)
        kaynak_sonucu = next(kaynak for kaynak in self.depo.kaynaklar() if kaynak["id"] == "deneme")
        self.assertIsNone(kaynak_sonucu["etag"])
        self.assertIsNone(kaynak_sonucu["modified"])
        self.assertIsNone(kaynak_sonucu["last_error"])

    def test_asil_veritabanina_yedekleme_kilitlenmeden_reddedilir(self):
        self.ekle()
        once = self.depo.disa_aktar()
        with self.assertRaisesRegex(ValueError, "asıl veritabanından farklı"):
            self.depo.yedekle(self.dosya.parent / "." / self.dosya.name)
        self.assertEqual(self.depo.disa_aktar(), once)


if __name__ == "__main__":
    unittest.main()

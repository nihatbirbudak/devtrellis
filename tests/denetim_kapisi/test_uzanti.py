"""alpha.15 dilim 14a: kapının 10. denetimi (uzantı kuralları) ve sunucunun birleştirme işlevleri; saf işlevler ve sahte ağ."""

import copy
import unittest
from datetime import datetime, timezone

from radar.denetim import denetle
from radar.denetim import uzanti as uzanti_kurallari
from radar.denetim.sonuc import KALDI, Denetim
from radar.katki_v4 import kayit_olustur, kayittan_aday, uzanti_birlestir, uzanti_hazirla

from .yardimci import KAYNAK_1, KAYNAK_2, rapor, temel_ag

SIMDI = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
KOKEN = {"origin": "model_assisted", "stages": [{"provider": "claude", "model": "claude-sonnet-5", "access_type": "non_trial",
                                                 "basis": "contributor_declaration", "terms_url": None}]}
YENI = {"text": "Projects are kept in the data directory of the user.",
        "quote": "Projects are kept in the user's data directory.", "source_url": KAYNAK_2, "checked_on": "2026-11-06"}


def kayit_kur(paket=None, surum=1):
    """Sahte ağla kapıdan geçen raporun kamu kaydı."""
    paket = paket or dict(rapor(), rights={"own_text_license": "CC0-1.0"})
    sonuc = denetle(copy.deepcopy(paket), getirici=temel_ag().getirici(), yayindakiler=[], simdi=SIMDI)
    assert sonuc["hukum"] == "yayimlanir", sonuc["ret_nedenleri"]
    return kayit_olustur(paket, sonuc, kayit_id="a" * 32, surum=surum, yol="machine_gate", yayin_saniye=1793966400,
                         origin="https://community.example.org")


def ek(kayit, iddialar=(YENI,), **degisiklik):
    paket = {"kind": "record_extension", "schema_version": 4, "language": "en",
             "extends": {"id": kayit["id"], "version": kayit["version"], "change_summary": "Adds the storage note."},
             "claims": [copy.deepcopy(i) for i in iddialar], "provenance": copy.deepcopy(KOKEN),
             "rights": {"own_text_license": "CC0-1.0"}}
    paket.update(degisiklik)
    return paket


class Uzanti(unittest.TestCase):
    def setUp(self):
        self.kayit = kayit_kur()

    def test_uzanti_verilmezse_kapi_sonucu_onceki_gibi(self):
        sonuc = denetle(rapor(), getirici=temel_ag().getirici(), yayindakiler=[], simdi=SIMDI)
        self.assertNotIn("uzanti", sonuc)
        self.assertEqual(sonuc["denetim_surumu"], "denetim-kapisi-v0.2.3")
        self.assertNotIn("uzanti", [d["ad"] for d in sonuc["denetimler"]])

    def test_birlesik_paket_kapidan_gecer_ust_kayit_yinelenen_listesinden_cikar(self):
        baglam = uzanti_hazirla(ek(self.kayit), self.kayit, self.kayit)
        yayindakiler = [{"id": self.kayit["id"], "title": self.kayit["content"]["title"],
                         "claims": [{"text": i["text"], "quote": i["evidence"]["quote"], "source_url": i["evidence"]["source_url"]}
                                    for i in self.kayit["claims"]]}]
        sonuc = denetle(baglam["birlesik"], getirici=temel_ag().getirici(), yayindakiler=yayindakiler, simdi=SIMDI,
                        uzanti=baglam)
        self.assertEqual(sonuc["hukum"], "yayimlanir", sonuc["ret_nedenleri"])
        self.assertEqual(sonuc["denetim_surumu"], "denetim-kapisi-v0.2.3+uzanti-v1")
        self.assertNotIn("yinelenen.uzanti_oner", sonuc["uyarilar"])
        self.assertEqual([d["sira"] for d in sonuc["denetimler"]], [*range(1, 11)])
        self.assertEqual((sonuc["uzanti"]["taban_surum"], sonuc["uzanti"]["yeni_iddia"], sonuc["girdi"]["iddia_sayisi"]), (1, 1, 4))
        self.assertIn("uzantı kuralları", sonuc["kanit_etiketi"])

    def test_birlestirilemeyen_uzanti_yalniz_10_denetimle_reddedilir(self):
        baglam = uzanti_hazirla(ek(self.kayit), None, None)
        sonuc = denetle(ek(self.kayit), ag=False, yayindakiler=None, simdi=SIMDI, uzanti=baglam)
        self.assertEqual((sonuc["hukum"], sonuc["ret_nedenleri"]), ("reddedilir", ["uzanti.ust_yok"]))
        self.assertEqual([d["ad"] for d in sonuc["denetimler"]], ["uzanti"])
        self.assertTrue(sonuc["denetim_surumu"].endswith("+uzanti-v1"))

    def test_on_denetim_kurallari(self):
        anahtar = lambda url: url
        k = self.kayit
        self.assertEqual(uzanti_kurallari.on_denetim(ek(k), k, k, adres_anahtari=anahtar), [])
        durumlar = [
            (ek(k, extends={"id": k["id"], "version": 1}), "uzanti.bicim"),
            (ek(k, extends={"id": "X" * 32, "version": 1, "change_summary": "x"}), "uzanti.bicim"),
            (ek(k, extends={"id": k["id"], "version": 1, "change_summary": "x" * 301}), "uzanti.bicim"),
            (ek(k, iddialar=()), "uzanti.bos"),
            (ek(k, language="tr"), "uzanti.bicim"),
            (ek(k, not_covered_remove=[0, 0]), "uzanti.bicim"),
            (ek(k, rights={"own_text_license": "CC-BY-4.0"}), "uzanti.lisans"),
            (ek(k, quote_match={"state": "exact"}), "uzanti.bicim"),
        ]
        for paket, kod in durumlar:
            self.assertIn(kod, [h[0] for h in uzanti_kurallari.on_denetim(paket, k, k, adres_anahtari=anahtar)], kod)
        self.assertEqual(uzanti_kurallari.on_denetim(ek(k), None, None, adres_anahtari=anahtar)[0][0], "uzanti.ust_yok")
        baska = dict(k, version=2, lineage={"versions": [{"version": 1}, {"version": 2}]})
        gorulen_yok = dict(k, version=3)
        self.assertEqual(uzanti_kurallari.on_denetim(ek(k), baska, gorulen_yok, adres_anahtari=anahtar)[0][0],
                         "uzanti.gorulen_surum_yok")
        # Gördüğü sürümün iddiaları güncelin önü değilse (kimlik farkı) yine gorulen_surum_yok.
        bozuk = copy.deepcopy(k)
        bozuk["claims"][0]["id"] = "f" * 32
        self.assertEqual(uzanti_kurallari.on_denetim(ek(k), baska, bozuk, adres_anahtari=anahtar)[0][0],
                         "uzanti.gorulen_surum_yok")

    def test_ust_alinti_kaydi_ret_degil_denenemedi(self):
        d4 = Denetim(4, "alinti_eslesmesi", "Alıntı")
        d4.ayrinti = [{"iddia": 0, "sonuc": KALDI, "neden": "alinti.bulunamadi"}, {"iddia": 1, "sonuc": "gecti"},
                      {"iddia": 2, "sonuc": KALDI, "neden": "alinti.yaklasik"}]
        d4.kal("alinti.bulunamadi")
        d4.kal("alinti.yaklasik")
        yeni, kaydi = uzanti_kurallari.ust_alinti_ayir(d4, 2)
        self.assertTrue(kaydi)
        self.assertEqual(sorted(yeni.nedenler), ["alinti.yaklasik", "uzanti.ust_alinti_kaydi"])
        self.assertEqual(yeni.durum, KALDI)                         # yeni iddianın hatası ret olarak kalır
        d4b = Denetim(4, "alinti_eslesmesi", "Alıntı")
        d4b.ayrinti = [{"iddia": 0, "sonuc": KALDI, "neden": "alinti.bulunamadi"}, {"iddia": 2, "sonuc": "gecti"}]
        d4b.kal("alinti.bulunamadi")
        yeni, _ = uzanti_kurallari.ust_alinti_ayir(d4b, 2)
        self.assertEqual((yeni.durum, yeni.nedenler), ("denenemedi", ["uzanti.ust_alinti_kaydi"]))

    def test_birlestirme_not_covered_metinle_kaldirilir_cevap_bagi_cevrilir(self):
        k = kayit_kur(dict(rapor(not_covered=["Restore steps.", "Cloud pricing."]), rights={"own_text_license": "CC0-1.0"}))
        ust = copy.deepcopy(k)
        ust["content"]["not_covered"] = ["Cloud pricing."]           # araya biri "Restore steps."'i kaldırdı
        ust["claims"].append(dict(copy.deepcopy(ust["claims"][0]), id="e" * 32))
        ust["version"] = 2
        paket = ek(k, not_covered_remove=[0, 1], not_covered_add=["Retention."],
                   sources_added=[{"title": "Widget reference", "url": KAYNAK_1}, {"title": "CLI", "url": "https://x.example/c"}],
                   answers_added=[{"q": "Where?", "claims": [1, 4]}, "Plain?"])
        birlesik, bilgi = uzanti_birlestir(ust, k, paket)
        self.assertEqual(birlesik["not_covered"], ["Retention."])
        self.assertEqual(bilgi["kaldirilan_not_covered"], ["Cloud pricing."])
        self.assertEqual([s["url"] for s in birlesik["sources"]], [KAYNAK_1, KAYNAK_2, "https://x.example/c"])
        self.assertEqual(birlesik["answers"], [{"q": "Where?", "claims": [1, 5]}, "Plain?"])
        self.assertEqual(birlesik["claims"][:4], kayittan_aday(ust)["claims"])
        self.assertEqual(birlesik["claims"][4]["text"], YENI["text"])
        self.assertEqual(birlesik["provenance"], k["provenance"])

    def test_kayit_olustur_ust_iddiayi_degistiren_birlesigi_reddeder(self):
        baglam = uzanti_hazirla(ek(self.kayit), self.kayit, self.kayit)
        sonuc = denetle(baglam["birlesik"], getirici=temel_ag().getirici(), yayindakiler=[], simdi=SIMDI, uzanti=baglam)
        bozuk = copy.deepcopy(baglam["birlesik"])
        bozuk["claims"][0]["text"] = "Widget 2.0 stores nothing."
        with self.assertRaises(ValueError):
            kayit_olustur(bozuk, sonuc, kayit_id=self.kayit["id"], surum=2, yol="machine_gate", yayin_saniye=1793966500,
                          origin="https://community.example.org", ust=self.kayit, ek=baglam["ek"])
        kayit = kayit_olustur(baglam["birlesik"], sonuc, kayit_id=self.kayit["id"], surum=2, yol="machine_gate",
                              yayin_saniye=1793966500, origin="https://community.example.org", ust=self.kayit, ek=baglam["ek"])
        self.assertEqual([i["id"] for i in kayit["claims"][:3]], [i["id"] for i in self.kayit["claims"]])
        self.assertEqual(kayit["lineage"]["versions"][0]["claims"], 3)


if __name__ == "__main__":
    unittest.main()

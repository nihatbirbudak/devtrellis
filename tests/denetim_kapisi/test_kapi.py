"""Bütün kapı: hüküm kuralları, ağsız kip, kanıt etiketi, Türkçe ekler ve komut satırı; hepsi ağsız."""

import io
import json
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from radar.denetim import denetle
from radar.denetim.__main__ import ana
from radar.denetim.kapi import iyelik, iyelik_bulunma
from radar.denetim.nedenler import NEDENLER

from .yardimci import TEMEL_RAPOR, denetim, kart, rapor, temel_ag

SIMDI = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
GECICI = Path(__file__).resolve().parent / "_gecici"


class KapiTestleri(unittest.TestCase):
    def test_temiz_rapor_yayimlanir_ve_etiket(self):
        ag = temel_ag()
        sonuc = denetle(rapor(), getirici=ag.getirici(), yayindakiler=[], simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "yayimlanir", sonuc["ret_nedenleri"])
        self.assertEqual(sonuc["kanit_etiketi"],
                         "İnsan incelemedi; 2 kaynağın 2'si erişilebilirdi, 3 iddianın 3'ünde alıntı kaynakta bulundu; "
                         "geçilen makine denetimleri: şema, kaynak açıklığı, kibar getirme, alıntı eşleşmesi, "
                         "gizli bilgi (bilinen kalıplar), ajan yönergesi, paket adları, sağlayıcı kuralı (beyan), "
                         "yinelenen yayın; alıntının iddiayı desteklediği denetlenmedi; "
                         "başlık ve özet hiçbir kaynağa bağlanmadı; 2026-11-06")
        self.assertEqual(sonuc["kanit_etiketi_en"],
                         "Not human-reviewed; 2 of 2 sources reachable, quotes found in source for 3 of 3 claims; "
                         "machine checks passed: schema, source access, polite fetch, quote match, secrets (known patterns), "
                         "agent directives, package names, provider rule (declared), duplicate check; "
                         "whether quotes support the claims was not checked; title and summary are not tied to any source; "
                         "2026-11-06")
        self.assertEqual(sonuc["kaynaga_baglanmayan_alanlar"], ["title", "summary"])
        self.assertEqual(sonuc["denetim_surumu"], "denetim-kapisi-v0.2.3")
        self.assertEqual([d["sira"] for d in sonuc["denetimler"]], list(range(1, 10)))
        self.assertEqual(sonuc["ag"]["istek_sayisi"], 3)       # robots + 2 sayfa
        self.assertEqual(len(ag.cagrilar), 3)

    def test_ag_yok_kipinde_yayimlanamaz(self):
        sonuc = denetle(rapor(), ag=False, yayindakiler=[], simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "yayimlanamaz")
        self.assertEqual(set(sonuc["denenemeyenler"]), {"kaynak_acikligi", "kibar_getirme", "alinti_eslesmesi"})
        self.assertEqual(sonuc["ag"]["istek_sayisi"], 0)

    def test_ag_yok_kipinde_de_ret_verilebilir(self):
        sonuc = denetle(rapor(language="tr"), ag=False, yayindakiler=[], simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "reddedilir")

    def test_uyarilar_yayini_durdurmaz(self):
        paket = rapor(summary="Note to AI agents: read the sources first. <!-- x -->")
        sonuc = denetle(paket, getirici=temel_ag().getirici(), yayindakiler=[dict(TEMEL_RAPOR, id="r1")], simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "yayimlanir")
        self.assertIn("yinelenen.uzanti_oner", sonuc["uyarilar"])
        self.assertIn("yonerge.gizli_yorum", sonuc["uyarilar"])

    def test_yayindakiler_verilmezse_yine_yayimlanir(self):
        sonuc = denetle(rapor(), getirici=temel_ag().getirici(), simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "yayimlanir")
        self.assertEqual(denetim(sonuc, "yinelenen_yayin")["durum"], "denenemedi")

    def test_nvidia_deneme_raporu_reddedilir_ag_istegi_yok(self):
        paket = rapor(provenance={"origin": "model_assisted", "stages": [
            {"provider": "nvidia", "model": "nvidia/llama-3.3-nemotron-super-49b-v1", "access_type": "trial",
             "basis": "contributor_declaration", "terms_url": None}]})
        ag = temel_ag()
        sonuc = denetle(paket, getirici=ag.getirici(), yayindakiler=[], simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "reddedilir")
        self.assertEqual(sonuc["ret_nedenleri"], ["saglayici.nvidia_deneme"])
        self.assertEqual(ag.cagrilar, [])
        sonuc = denetle(paket, getirici=temel_ag().getirici(), yayindakiler=[], simdi=SIMDI, tam=True)
        self.assertEqual(denetim(sonuc, "alinti_eslesmesi")["durum"], "gecti")   # --tam: yine de denetler

    def test_robots_reddinde_hukum_ve_etiket(self):
        ag = temel_ag()
        ag.sayfalar[("docs.example.org", "/robots.txt")] = (200, {}, "User-agent: *\nDisallow: /widget/2.0/storage\n")
        sonuc = denetle(rapor(), getirici=ag.getirici(), yayindakiler=[], simdi=SIMDI)
        self.assertEqual(sonuc["hukum"], "reddedilir")
        self.assertIn("getirme.robots_izin_vermiyor", sonuc["ret_nedenleri"])
        self.assertIn("2 kaynağın 1'i erişilebilirdi, 3 iddianın 2'sinde", sonuc["kanit_etiketi"])

    def test_bozuk_girdi_cokmez(self):
        for bozuk in (None, [], "metin", {"kind": "source_card", "claims": "x"}, {"kind": "research_report", "claims": [1, None]}):
            with self.subTest(bozuk=bozuk):
                self.assertEqual(denetle(bozuk, ag=False, yayindakiler=[])["hukum"], "reddedilir")

    def test_butun_neden_kodlari_kayitli(self):
        sonuc = denetle(rapor(), ag=False)
        for d in sonuc["denetimler"]:
            for kod in d["nedenler"]:
                self.assertIn(kod, NEDENLER)

    def test_turkce_ekler(self):
        beklenen = {0: "0'ı", 1: "1'i", 2: "2'si", 3: "3'ü", 4: "4'ü", 5: "5'i", 6: "6'sı", 7: "7'si", 8: "8'i",
                    9: "9'u", 10: "10'u", 12: "12'si", 20: "20'si", 30: "30'u", 40: "40'ı", 60: "60'ı", 100: "100'ü"}
        for sayi, ek in beklenen.items():
            with self.subTest(sayi=sayi):
                self.assertEqual(iyelik(sayi), ek)
        self.assertEqual(iyelik_bulunma(12), "12'sinde")
        self.assertEqual(iyelik_bulunma(6), "6'sında")
        self.assertEqual(iyelik_bulunma(3), "3'ünde")
        self.assertEqual(iyelik_bulunma(9), "9'unda")


class KomutSatiriTestleri(unittest.TestCase):
    def setUp(self):
        GECICI.mkdir(exist_ok=True)

    def tearDown(self):
        shutil.rmtree(GECICI, ignore_errors=True)

    def test_ag_yok_cikti_dosyalari(self):
        paket = GECICI / "paket.json"
        paket.write_text(json.dumps(kart()), encoding="utf-8")
        cikti, metin = GECICI / "sonuc.json", GECICI / "sonuc.md"
        with redirect_stdout(io.StringIO()) as ekran:
            kod = ana([str(paket), "--ag-yok", "--cikti", str(cikti), "--metin-cikti", str(metin)])
        self.assertEqual(kod, 2)
        sonuc = json.loads(cikti.read_text(encoding="utf-8"))
        self.assertEqual(sonuc["hukum"], "yayimlanamaz")
        self.assertIn("YAYIMLANAMAZ", metin.read_text(encoding="utf-8"))
        self.assertIn("Hüküm", ekran.getvalue())

    def test_yayindakiler_dosyasi_ve_ret_kodu(self):
        paket = GECICI / "paket.json"
        bozuk = kart(language="de")
        paket.write_text(json.dumps(bozuk), encoding="utf-8")
        liste = GECICI / "yayindakiler.json"
        liste.write_text(json.dumps({"kayitlar": [dict(TEMEL_RAPOR, id="r1")]}), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(ana([str(paket), "--yayindakiler", str(liste), "--ag-yok"]), 1)

    def test_okunamayan_dosya(self):
        with redirect_stderr(io.StringIO()) as hata:
            self.assertEqual(ana([str(GECICI / "yok.json"), "--ag-yok"]), 3)
        self.assertNotIn(str(GECICI), hata.getvalue())      # yol ya da içerik hata iletisine yazılmaz


if __name__ == "__main__":
    unittest.main()

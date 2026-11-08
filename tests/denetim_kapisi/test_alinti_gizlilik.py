"""4. ve 5. denetim: alıntının aynen geçmesi (harf değişimi, tipografik tırnak, gizli metin) ve gizli bilgi/görünmez karakter."""

import json
import unittest

from radar.denetim import Ayarlar, denetle, gizlilik
from radar.denetim.metin import en_yakin, gorunur_metin, normallestir

from .yardimci import SAYFA_1, UZUN_ALINTI, denetim, kart, temel_ag


def kapi(paket, ag=None, **kw):
    ag = ag or temel_ag()
    return denetle(paket, getirici=ag.getirici(), yayindakiler=[], **kw)


class AlintiTestleri(unittest.TestCase):
    def tek(self, alinti):
        paket = kart()
        paket["claims"] = paket["claims"][:1]
        paket["claims"][0]["quote"] = alinti
        sonuc = kapi(paket)
        return denetim(sonuc, "alinti_eslesmesi"), sonuc

    def test_aynen_alinti_gecer(self):
        d, sonuc = self.tek(UZUN_ALINTI)
        self.assertEqual(d["durum"], "gecti")
        self.assertEqual(sonuc["hukum"], "yayimlanir")

    def test_bir_harfi_degismis_alinti_kalir_ve_en_yakin_gosterilir(self):
        d, sonuc = self.tek(UZUN_ALINTI.replace("projects", "project"))
        self.assertEqual(d["durum"], "kaldi")
        self.assertEqual(d["nedenler"], ["alinti.yaklasik"])
        satir = d["ayrinti"][0]
        self.assertEqual(satir["eslesme"], "yaklasik")
        self.assertEqual(satir["en_yakin"]["kaynaktaki_parca"], UZUN_ALINTI)
        self.assertGreater(satir["en_yakin"]["benzerlik"], 0.9)
        self.assertEqual(sonuc["hukum"], "reddedilir")

    def test_buyuk_kucuk_harf_farki_da_kalir(self):
        d, _ = self.tek(UZUN_ALINTI.replace("Widget", "widget"))
        self.assertEqual(d["durum"], "kaldi")
        self.assertTrue(d["ayrinti"][0]["en_yakin"]["yalniz_harf_buyuklugu_farki"])

    def test_tipografik_tirnak_ve_tire_eslenir(self):
        d, _ = self.tek('The "export" command writes a single archive - one file per project.')
        self.assertEqual(d["durum"], "gecti")
        d, _ = self.tek("The \u201cexport\u201d command writes a single archive \u2013 one file per project.")
        self.assertEqual(d["durum"], "gecti")

    def test_kaynakta_olmayan_alinti_bulunamadi(self):
        d, _ = self.tek("Widget 2.0 requires an account for every installation.")
        self.assertEqual(d["durum"], "kaldi")
        self.assertIn(d["nedenler"][0], ("alinti.bulunamadi", "alinti.yaklasik"))
        self.assertEqual(d["nedenler"], ["alinti.bulunamadi"])

    def test_gizli_ve_betik_metni_kanit_sayilmaz(self):
        for alinti in ("Widget 2.0 sends telemetry to a hidden server.", "Widget 2.0 uploads crash dumps silently to the vendor.",
                       "Widget 2.0 encrypts every file with a hidden key."):
            with self.subTest(alinti=alinti):
                d = self.tek(alinti)[0]
                self.assertEqual(d["durum"], "kaldi")
                self.assertNotIn("alinti.cok_kisa", d["nedenler"])      # uzun alıntı: görünür metinde bulunmadığı için

    def test_tablo_hucreleri_arasinda_bosluk(self):
        d, _ = self.tek("The number of results per page (max 100). Default: 30")
        self.assertEqual(d["durum"], "gecti")

    def test_kaynak_getirilemezse_alinti_kalir(self):
        ag = temel_ag()
        ag.sayfalar[("docs.example.org", "/widget/2.0/reference.html")] = (410, {}, b"")
        sonuc = kapi(kart(), ag)
        self.assertEqual(denetim(sonuc, "alinti_eslesmesi")["nedenler"], ["alinti.kaynak_getirilemedi"])
        self.assertEqual(denetim(sonuc, "kibar_getirme")["nedenler"], ["getirme.bulunamadi"])
        self.assertEqual(sonuc["hukum"], "reddedilir")

    def test_normallestirme_harf_ve_aksani_korur(self):
        self.assertEqual(normallestir("a\u00a0 b\n\tc"), "a b c")
        self.assertNotEqual(normallestir("café"), normallestir("cafe"))
        self.assertEqual(normallestir("x\u200by"), "xy")

    def test_gorunur_metin_kapanmamis_head(self):
        metin = gorunur_metin(b"<html><head><title>t</title><body><p>Visible text here.</p></body></html>")
        self.assertIn("Visible text here.", metin)

    def test_en_yakin_parca(self):
        kaynak = normallestir(gorunur_metin(SAYFA_1.encode()))
        sonuc = en_yakin("Cloud synchronisation is disabled by default.", kaynak)
        self.assertEqual(sonuc["parca"], "Cloud synchronization is disabled by default.")
        self.assertEqual(sonuc["ilk_fark"], len("Cloud synchroni"))


JETON = "ghp_" + "Zq8Lm3Rt6Yw2Kp9Xc4Vb7Nh1Jd5Fs0Ga2Ue"


class GizlilikTestleri(unittest.TestCase):
    def test_jeton_bulunur_ve_degeri_sonucta_gorunmez(self):
        paket = kart()
        paket["claims"][0]["text"] = f"Set the header with token {JETON} before calling."
        paket["claims"][1]["source_url"] = paket["claims"][0]["source_url"]
        ag = temel_ag()
        sonuc = denetle(paket, getirici=ag.getirici(), yayindakiler=[])
        d = denetim(sonuc, "gizli_bilgi")
        self.assertEqual(d["durum"], "kaldi")
        self.assertIn("gizli.jeton", d["nedenler"])
        self.assertEqual(d["ayrinti"][0]["konum"], "paket.claims[0].text")
        self.assertEqual(sonuc["hukum"], "reddedilir")
        metin = json.dumps(sonuc, ensure_ascii=False)
        self.assertNotIn(JETON, metin)
        self.assertNotIn("Zq8Lm3", metin)
        self.assertEqual(ag.cagrilar, [])            # reddedilecek paket için ağa çıkılmadı

    def test_kalip_turleri(self):
        ornekler = {
            "anahtar": "export OPENAI_API_KEY=sk-proj-" + "aB3" * 8,
            "jeton": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlMTIz",
            # Uydurma kişi ve alan adı; RFC 2606 example.* alanlarını kapı yer tutucu sayar, o yüzden burada kullanılmaz.
            "eposta": "Contact ali.veli@acme-ornek.test for access.",
            "telefon": "Call +90 532 123 45 67 for support.",
            # Yerel yol da çalışma anında birleştirilir (yayın kopyası denetimi sürücü + Users dizisini durdurur; kapı tam yolu görür).
            "yerel_yol": "The file is in C:" + r"\Users\Example\Desktop\notes.txt now.",
            "ozel_alan_adi": "The mirror runs on build01.corp for the team.",
            # Anahtar bloğu işareti çalışma anında birleştirilir: dosyanın kendisi gerçek görünümlü bir blok taşımaz
            # (yayın kopyası aracının özel içerik denetimi aynen kalır); kapı yine tam işareti görür.
            "ozel_anahtar": "-----BEGIN " + "RSA PRIVATE KEY" + "-----",
        }
        for tur, metin in ornekler.items():
            with self.subTest(tur=tur):
                self.assertIn(tur, [t for t, _ in gizlilik.gizli_kalip_bul(metin)])

    def test_yer_tutucular_ret_sayilmaz(self):
        for metin in ("Send the token: YOUR_TOKEN_HERE in the header.", "password=<password>",
                      "Files live in /home/user/.config and ~/.ssh/config.", "Write to support@example.com.",
                      "per_page: 100, 2026-03-10, 5,000 requests per hour"):
            with self.subTest(metin=metin):
                self.assertEqual(gizlilik.gizli_kalip_bul(metin), [])

    def test_gorunmez_karakterler(self):
        ornekler = {
            "bicim_denetim": "Widget\u200b stores",
            "yon_isareti": "Widget \u202estores",
            "etiket_blogu": "Widget " + "".join(chr(0xE0000 + ord(c)) for c in "ignore") + " stores",
            "varyasyon_secici": "Widget\ufe0f stores",
            "denetim_karakteri": "Widget\x07 stores",
        }
        for tur, metin in ornekler.items():
            with self.subTest(tur=tur):
                paket = kart()
                paket["claims"][0]["quote"] = metin
                d = gizlilik.denetle(paket)
                self.assertEqual(d.durum, "kaldi")
                self.assertEqual(d.ayrinti[0]["tur"], tur)
                self.assertEqual(d.ayrinti[0]["konum"], "paket.claims[0].quote")

    def test_temiz_paket_gecer(self):
        self.assertEqual(gizlilik.denetle(kart()).durum, "gecti")

    def test_bulunan_deger_yalniz_tur_ve_konum(self):
        paket = kart()
        paket["claims"][0]["text"] = "Mail ali.veli@acme-ornek.test now."
        d = gizlilik.denetle(paket)
        self.assertEqual(set(d.ayrinti[0]), {"tur", "konum", "karakter"})
        self.assertNotIn("acme-ornek", json.dumps(d.sozluk(), ensure_ascii=False))

    def test_ayarlar_dondurulmus(self):
        with self.assertRaises(Exception):
            Ayarlar().yakin_esik = 0.1


if __name__ == "__main__":
    unittest.main()

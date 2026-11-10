"""1. ve 2. denetim: şema/sınırlar ve kaynağın herkese açık olması (DNS sonrası özel adres dahil), ağsız."""

import unittest
from datetime import date

from radar.denetim import adres, sema
from radar.denetim.getirme import Getirici

from .yardimci import KAYNAK_1, SahteAg, kart, rapor


class SemaTestleri(unittest.TestCase):
    def sina(self, paket):
        return sema.denetle(paket, bugun=date(2026, 11, 6))

    def test_gecerli_kart_gecer(self):
        self.assertEqual(self.sina(kart()).durum, "gecti")

    def test_gecerli_rapor_gecer(self):
        self.assertEqual(self.sina(rapor()).durum, "gecti")

    def test_alinti_300_karakteri_asinca_kalir(self):
        paket = kart()
        paket["claims"][0]["quote"] = "a" * 301
        d = self.sina(paket)
        self.assertEqual(d.durum, "kaldi")
        self.assertIn("sema.uzunluk", d.nedenler)

    def test_alinti_tam_300_karakter_gecer(self):
        paket = kart()
        paket["claims"][0]["quote"] = "a" * 300
        self.assertNotIn("sema.uzunluk", self.sina(paket).nedenler)

    def test_eksik_zorunlu_alan(self):
        paket = kart()
        del paket["claims"][0]["checked_on"]
        self.assertIn("sema.eksik_alan", self.sina(paket).nedenler)

    def test_tarih_bicimi_ve_gelecek_tarih(self):
        paket = kart()
        paket["claims"][0]["checked_on"] = "06.11.2026"
        paket["claims"][1]["checked_on"] = "2026-12-30"
        d = self.sina(paket)
        self.assertIn("sema.tarih_bicimi", d.nedenler)
        self.assertIn("sema.gelecek_tarih", d.nedenler)

    def test_dil_en_olmali(self):
        self.assertIn("sema.dil", self.sina(kart(language="tr")).nedenler)

    def test_turkce_iddia_uyari(self):
        paket = kart()
        paket["claims"][0]["text"] = "Widget 2.0 projeleri yerelde saklar; bulut eşitlemesi kapalı."
        d = self.sina(paket)
        self.assertEqual(d.durum, "uyari")
        self.assertIn("sema.dil_kuskusu", d.nedenler)

    def test_sunucu_alani_istemciden_gelemez(self):
        paket = kart()
        paket["claims"][0]["quote_match"] = {"state": "exact"}
        self.assertIn("sema.sunucu_alani", self.sina(paket).nedenler)

    def test_bilinmeyen_alan_adi_sonuca_yazilmaz(self):
        paket = kart()
        paket["ghp_" + "A" * 30] = 1
        d = self.sina(paket)
        self.assertIn("sema.bilinmeyen_alan", d.nedenler)
        self.assertNotIn("ghp_", d.aciklama())

    def test_kart_tek_kaynaga_bagli(self):
        paket = kart()
        paket["claims"][1]["source_url"] = "https://docs.example.org/other.html"
        self.assertIn("sema.kart_tek_kaynak", self.sina(paket).nedenler)

    def test_kartta_iddia_turu_ve_kapsam_zorunlu(self):
        paket = kart()
        paket["claims"][0]["claim_type"] = "opinion"
        paket["claims"][1]["applies_to"] = [{"product": "Widget X", "versions": "2.0", "version_scheme": "exact"}]
        d = self.sina(paket)
        self.assertIn("sema.iddia_turu", d.nedenler)
        self.assertIn("sema.kapsam", d.nedenler)

    def test_kart_en_cok_yirmi_iddia(self):
        # alpha.13b: sınır 6'dan 20'ye çıktı; 20 iddia şema sınırına takılmaz, 21 takılır.
        paket = kart()
        tek = paket["claims"][0]
        paket["claims"] = [dict(tek, quote=f"{tek['quote']} {n}") for n in range(20)]
        self.assertNotIn("sema.liste_boyutu", self.sina(paket).nedenler)
        paket["claims"].append(dict(tek))
        self.assertIn("sema.liste_boyutu", self.sina(paket).nedenler)

    def test_adres_bicimi(self):
        paket = kart()
        for iddia in paket["claims"]:
            iddia["source_url"] = "http://docs.example.org/a"
        self.assertIn("sema.adres_bicimi", self.sina(paket).nedenler)

    def test_human_kokende_model_asamasi_olamaz(self):
        paket = kart()
        paket["provenance"]["origin"] = "human"
        self.assertIn("sema.koken_bicimi", self.sina(paket).nedenler)


class AdresTestleri(unittest.TestCase):
    def kod(self, url):
        return adres.statik_denetle(url)[0]

    def test_kamusal_https_gecer(self):
        self.assertIsNone(self.kod(KAYNAK_1))

    def test_ret_kurallari(self):
        beklenen = {
            "http://docs.example.org/a": "acik.https_degil",
            "https://user:pw@docs.example.org/a": "acik.kimlik_bilgisi",
            "https://docs.example.org:8443/a": "acik.standart_disi_port",
            "https://10.0.0.5/a": "acik.ip_adresi",
            "https://[::1]/a": "acik.ip_adresi",
            "https://2130706433/a": "acik.ip_adresi",
            "https://localhost/a": "acik.yerel_ad",
            "https://nas.internal/a": "acik.yerel_ad",
            "https://printer.local/a": "acik.yerel_ad",
            "https://intranet/a": "acik.yerel_ad",
            "https://xn--80ak6aa92e.com/a": "acik.punycode",
            "https://bit.ly/abc": "acik.kisaltici",
            "https://docs.example.org/a?utm_source=x": "acik.sorgu_parametresi",
            "https://docs.example.org/a?token=abc123": "acik.sorgu_parametresi",
            "https://docs.example.org/a#section": "acik.parca_isareti",
            "https://docs.example.org/a b": "acik.gecersiz_karakter",
            "https://docs.example.org/%E2%80%8Bx": "acik.gecersiz_karakter",
            "https://docs.example.org/reset/ghp_" + "a1" * 15: "acik.kimlik_tasiyan_adres",
            "https://docs.example.org/u/ali%40mail.com": "acik.kimlik_tasiyan_adres",
        }
        for url, kod in beklenen.items():
            with self.subTest(url=url):
                self.assertEqual(self.kod(url), kod)

    def test_rakamli_kamusal_alan_adi_ip_sayilmaz(self):
        self.assertIsNone(self.kod("https://cafe1.de/a"))

    def test_ozel_aga_cozulen_ad_kalir(self):
        ag = SahteAg(dns={"docs.example.org": ["192.168.1.20"]})
        d = adres.denetle([KAYNAK_1], getirici=ag.getirici())
        self.assertEqual(d.durum, "kaldi")
        self.assertEqual(d.nedenler, ["acik.ozel_adrese_cozuldu"])

    def test_bir_adresi_bile_ozel_olan_ad_kalir(self):
        ag = SahteAg(dns={"docs.example.org": ["93.184.215.14", "127.0.0.1"]})
        self.assertIn("acik.ozel_adrese_cozuldu", adres.denetle([KAYNAK_1], getirici=ag.getirici()).nedenler)

    def test_ipv4_tasiyan_ipv6_genel_sayilmaz(self):
        self.assertFalse(adres.genel_ip_mi("::ffff:10.0.0.1"))
        self.assertTrue(adres.genel_ip_mi("2606:4700::6810:84e5"))

    def test_cozulemeyen_ad_kalir(self):
        ag = SahteAg(dns={})
        self.assertEqual(adres.denetle([KAYNAK_1], getirici=ag.getirici()).nedenler, ["acik.dns_cozulemedi"])

    def test_ag_yokken_dns_denenemedi(self):
        d = adres.denetle([KAYNAK_1], ag=False)
        self.assertEqual(d.durum, "denenemedi")

    def test_ag_yokken_bicim_hatasi_yine_kalir(self):
        self.assertEqual(adres.denetle(["https://localhost/a"], ag=False).durum, "kaldi")

    def test_gecmeyen_adres_sonuca_yazilmaz(self):
        url = "https://docs.example.org/a?access_token=abcdef123456"
        d = adres.denetle([url], ag=False)
        self.assertIsNone(d.ayrinti[0]["url"])

    def test_getirici_ozel_ip_ye_baglanmaz(self):
        ag = SahteAg(dns={"docs.example.org": ["10.1.2.3"]})
        sonuc = ag.getirici().kaynak_getir(KAYNAK_1)
        self.assertEqual(sonuc.neden, "getirme.acik_olmayan_kaynak")
        self.assertEqual(ag.cagrilar, [])

    def test_getirici_cozulen_ip_ye_sabitlenir(self):
        ag = SahteAg(sayfalar={("docs.example.org", "/robots.txt"): (404, {}, b"")},
                     dns={"docs.example.org": ["93.184.215.14"]})
        ag.getirici().kaynak_getir(KAYNAK_1)
        self.assertTrue(all(c["ip"] == "93.184.215.14" for c in ag.cagrilar))
        self.assertIsInstance(Getirici, type)


if __name__ == "__main__":
    unittest.main()

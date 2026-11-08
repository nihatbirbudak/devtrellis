"""3. denetim: robots.txt, kullanıcı aracı, sınırlar, site başına bekleme, tek getirme; 'kaldı' ile 'kaynak izin vermiyor' ayrımı."""

import unittest

from radar.denetim import getirme
from radar.denetim.ayarlar import KULLANICI_ARACI, Ayarlar
from radar.denetim.getirme import AgHatasi
from radar.denetim.robots import RobotsKurallari

from .yardimci import KAYNAK_1, KAYNAK_2, SAYFA_1, SahteAg, html, temel_ag

ROBOTS = ("docs.example.org", "/robots.txt")
YOL_1 = ("docs.example.org", "/widget/2.0/reference.html")


class RobotsTestleri(unittest.TestCase):
    def test_en_uzun_kural_ve_joker(self):
        kural = RobotsKurallari.ayristir(
            "User-agent: *\nDisallow: /widget/\nAllow: /widget/2.0/\nDisallow: /*.pdf$\n")
        self.assertTrue(kural.izin_var_mi("UfukRadar", "/widget/2.0/reference.html"))
        self.assertFalse(kural.izin_var_mi("UfukRadar", "/widget/1.0/x.html"))
        self.assertFalse(kural.izin_var_mi("UfukRadar", "/a/b.pdf"))
        self.assertTrue(kural.izin_var_mi("UfukRadar", "/a/b.pdf.html"))

    def test_ozel_grup_genel_grubun_yerine_gecer(self):
        kural = RobotsKurallari.ayristir("User-agent: *\nAllow: /\n\nUser-agent: UfukRadar\nDisallow: /\n")
        self.assertFalse(kural.izin_var_mi("UfukRadar", "/x"))
        self.assertTrue(kural.izin_var_mi("OtherBot", "/x"))

    def test_bos_disallow_hepsine_izin(self):
        self.assertTrue(RobotsKurallari.ayristir("User-agent: *\nDisallow:\n").izin_var_mi("UfukRadar", "/x"))

    def test_esit_uzunlukta_allow_kazanir(self):
        kural = RobotsKurallari.ayristir("User-agent: *\nDisallow: /a\nAllow: /a\n")
        self.assertTrue(kural.izin_var_mi("UfukRadar", "/a"))


class GetirmeTestleri(unittest.TestCase):
    def test_basarili_getirme_ve_kullanici_araci(self):
        ag = temel_ag()
        d, sonuclar = getirme.denetle([KAYNAK_1], getirici=ag.getirici())
        self.assertEqual(d.durum, "gecti")
        self.assertEqual(sonuclar[KAYNAK_1].erisim, "erisildi")
        for cagri in ag.cagrilar:
            self.assertEqual(cagri["basliklar"]["User-Agent"], KULLANICI_ARACI)
            self.assertEqual(cagri["basliklar"]["Accept-Encoding"], "identity")
        self.assertEqual([c["yol"] for c in ag.cagrilar], ["/robots.txt", "/widget/2.0/reference.html"])

    def test_robots_reddi_kaynak_izin_vermiyor(self):
        ag = temel_ag()
        ag.sayfalar[ROBOTS] = (200, {}, "User-agent: UfukRadar\nDisallow: /widget/\n")
        d, sonuclar = getirme.denetle([KAYNAK_1], getirici=ag.getirici())
        self.assertEqual(d.durum, "kaldi")
        self.assertEqual(d.nedenler, ["getirme.robots_izin_vermiyor"])
        self.assertEqual(sonuclar[KAYNAK_1].erisim, "izin_vermiyor")
        self.assertEqual([c["yol"] for c in ag.cagrilar], ["/robots.txt"])   # sayfaya istek atılmadı

    def test_403_kaynak_izin_vermiyor_404_kaldi(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = html("forbidden", 403)
        g = ag.getirici()
        self.assertEqual((g.kaynak_getir(KAYNAK_1).erisim, g.kaynak_getir(KAYNAK_1).neden),
                         ("izin_vermiyor", "getirme.erisim_kisitli"))
        sonuc = g.kaynak_getir("https://docs.example.org/yok.html")
        self.assertEqual((sonuc.erisim, sonuc.neden), ("kaldi", "getirme.bulunamadi"))

    def test_giris_sayfasi_izin_vermiyor(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = html('<form><input type="password" name="p"></form>')
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.giris_sayfasi")

    def test_robots_sunucu_hatasi_gecici(self):
        ag = temel_ag()
        ag.sayfalar[ROBOTS] = (503, {}, b"")
        sonuc = ag.getirici().kaynak_getir(KAYNAK_1)
        self.assertEqual((sonuc.erisim, sonuc.neden), ("kaldi", "getirme.robots_gecici"))
        self.assertEqual([c["yol"] for c in ag.cagrilar], ["/robots.txt"])     # sayfaya gidilmedi

    def test_robots_baska_alana_yonlenirse_dogrulanamadi(self):
        ag = temel_ag()
        ag.sayfalar[ROBOTS] = (301, {"location": "https://evil.example.net/robots.txt"}, b"")
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.robots_dogrulanamadi")

    def test_robots_404_hepsine_izin(self):
        ag = temel_ag()
        del ag.sayfalar[ROBOTS]
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).erisim, "erisildi")

    def test_ayni_adres_bir_kez_robots_site_basina_bir_kez(self):
        ag = temel_ag()
        g = ag.getirici()
        for _ in range(3):
            g.kaynak_getir(KAYNAK_1)
        g.kaynak_getir(KAYNAK_2)
        yollar = [c["yol"] for c in ag.cagrilar]
        self.assertEqual(yollar.count("/robots.txt"), 1)
        self.assertEqual(yollar.count("/widget/2.0/reference.html"), 1)
        self.assertEqual(len(g.istekler), 3)

    def test_site_basina_bekleme(self):
        ag = temel_ag()
        g = ag.getirici()
        g.kaynak_getir(KAYNAK_1)
        g.kaynak_getir(KAYNAK_2)
        self.assertEqual(len(ag.uykular), 2)            # robots→sayfa1, sayfa1→sayfa2
        self.assertTrue(all(0.9 <= u <= 1.0 for u in ag.uykular))

    def test_crawl_delay_uyulur_ve_sinir(self):
        ag = temel_ag()
        ag.sayfalar[ROBOTS] = (200, {}, "User-agent: *\nCrawl-delay: 3\n")
        g = ag.getirici()
        g.kaynak_getir(KAYNAK_1)
        g.kaynak_getir(KAYNAK_2)
        self.assertGreaterEqual(max(ag.uykular), 2.9)
        ag2 = temel_ag()
        ag2.sayfalar[ROBOTS] = (200, {}, "User-agent: *\nCrawl-delay: 60\n")
        self.assertEqual(ag2.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.robots_bekleme_siniri")

    def test_baska_alana_yonlendirme_izlenmez(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = (301, {"location": "https://evil.example.net/x"}, b"")
        sonuc = ag.getirici().kaynak_getir(KAYNAK_1)
        self.assertEqual(sonuc.neden, "getirme.yonlendirme_baska_alana")
        self.assertNotIn("evil.example.net", [c["ad"] for c in ag.cagrilar])

    def test_ayni_alanda_yonlendirme_izlenir_ve_sinir(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = (302, {"location": "/widget/2.0/storage.html"}, b"")
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).erisim, "erisildi")
        dongu = temel_ag()
        dongu.sayfalar[YOL_1] = (302, {"location": "/widget/2.0/reference.html"}, b"")
        self.assertEqual(dongu.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.yonlendirme_siniri")

    def test_http_ye_yonlendirme_reddedilir(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = (301, {"location": "http://docs.example.org/a"}, b"")
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.yonlendirme_gecersiz")

    def test_boyut_siniri(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = html("<p>" + "x" * 5000 + "</p>")
        sonuc = ag.getirici(Ayarlar(en_cok_bayt=1000)).kaynak_getir(KAYNAK_1)
        self.assertEqual((sonuc.erisim, sonuc.neden), ("kaldi", "getirme.boyut_siniri"))

    def test_icerik_turu(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = (200, {"content-type": "application/pdf"}, b"%PDF-1.7")
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.icerik_turu")

    def test_zaman_asimi_kaldi(self):
        ag = temel_ag()
        ag.sayfalar[YOL_1] = AgHatasi("getirme.zaman_asimi")
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).neden, "getirme.zaman_asimi")

    def test_ag_yokken_denenemedi(self):
        d, sonuclar = getirme.denetle([KAYNAK_1], ag=False)
        self.assertEqual((d.durum, sonuclar), ("denenemedi", {}))

    def test_sayfa_metni_dogru_bayt(self):
        ag = temel_ag()
        self.assertEqual(ag.getirici().kaynak_getir(KAYNAK_1).govde, SAYFA_1.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()

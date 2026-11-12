"""alpha.15 (14r2) okuma eki, kapı tarafı: cevap bağı ve yokluk notu biçimi, kısa cevap ve bağlı cevap terim uyarısı,
kaynaklarda yok notu terim araması, kaynak başına getirme zamanı ve görünür metin özeti. Hepsi ağsız (sahte ağ)."""

import hashlib
import unittest
from datetime import datetime, timezone

from radar import okuma_eki
from radar.denetim import denetle
from radar.denetim.metin import gorunur_metin, normallestir

from .yardimci import KAYNAK_1, KAYNAK_2, SAYFA_1, SAYFA_2, UZUN_ALINTI, denetim, kart, rapor, temel_ag

SIMDI = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
SORULAR = ["Where does Widget 2.0 store projects?", "How often are backups created?",
           "Does export write one archive per project?"]


def kos(paket, ag=True, okuma_eki_acik=True):
    if not ag:
        return denetle(paket, ag=False, yayindakiler=[], simdi=SIMDI, okuma_eki=okuma_eki_acik)
    return denetle(paket, getirici=temel_ag().getirici(), yayindakiler=[], simdi=SIMDI, okuma_eki=okuma_eki_acik)


class KapaliKip(unittest.TestCase):
    """Düzeltme turu 1, yönetici kararı 1: kapı varsayılan olarak okuma ekisiz (v0.2.3 kuralları ve sonuç biçimi)."""

    def test_varsayilan_kapali_ve_v023_sonucu(self):
        temel = denetle(rapor(), getirici=temel_ag().getirici(), yayindakiler=[], simdi=SIMDI)
        self.assertEqual(temel["denetim_surumu"], "denetim-kapisi-v0.2.3")
        self.assertNotIn("kaynak_anliklari", temel)
        self.assertNotIn("yokluk_denetimi", temel)
        self.assertNotIn("Kaynaklarda yok notları", " ".join(temel["kanitlamadiklari"]))
        self.assertEqual(kos(rapor())["denetim_surumu"], "denetim-kapisi-v0.2.4")
        # Kısa cevap terim uyarısı kapalı kipte yok (v0.2.3'te yoktu).
        kisa = rapor(short_answer="Widget 2.0 keeps projects locally; call backup_now() for a backup.")
        self.assertEqual(kos(kisa, okuma_eki_acik=False)["uyarilar"], [])
        self.assertEqual(kos(kisa)["uyarilar"], ["sema.kisa_cevap_terim"])

    def test_kapali_kipte_yeni_alanlar_eskisi_gibi_reddedilir(self):
        sonuc = kos(rapor(not_in_sources=[{"item": "Restore steps", "checked": [1]}]), ag=False, okuma_eki_acik=False)
        self.assertIn("sema.bilinmeyen_alan", sonuc["ret_nedenleri"])
        self.assertNotIn("not_in_sources", sonuc["kaynaga_baglanmayan_alanlar"])
        sonuc = kos(rapor(answers=[{"q": "Where?", "claims": [1]}] + SORULAR[:2]), ag=False, okuma_eki_acik=False)
        self.assertEqual(sonuc["ret_nedenleri"], ["sema.tip"])


class EskiBicim(unittest.TestCase):
    def test_eski_bicimli_paket_ayni_denetim_listesi_ve_uyarisiz(self):
        sonuc = kos(rapor(answers=SORULAR, not_covered=["Restore steps."]))
        self.assertEqual(sonuc["hukum"], "yayimlanir", sonuc["ret_nedenleri"])
        self.assertEqual([d["sira"] for d in sonuc["denetimler"]], list(range(1, 10)))   # 11. denetim yalnız yeni alanda
        self.assertEqual(sonuc["uyarilar"], [])
        self.assertEqual(sonuc["yokluk_denetimi"], [])
        self.assertNotIn("absence-note", sonuc["kanit_etiketi_en"])
        self.assertEqual(sonuc["kaynaga_baglanmayan_alanlar"], ["title", "summary", "not_covered", "answers"])

    def test_kaynak_anliklari_zaman_ve_gorunur_metin_ozeti(self):
        sonuc = kos(rapor())
        anliklar = sonuc["kaynak_anliklari"]
        self.assertEqual([a["url"] for a in anliklar], [KAYNAK_1, KAYNAK_2])
        for anlik, sayfa in zip(anliklar, (SAYFA_1, SAYFA_2)):
            beklenen = hashlib.sha256(normallestir(gorunur_metin(sayfa.encode("utf-8"), "text/html")).encode("utf-8"))
            self.assertEqual(anlik["gorunur_metin_sha256"], beklenen.hexdigest())
            self.assertRegex(anlik["getirilme_zamani"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(kos(rapor(), ag=False)["kaynak_anliklari"], [])   # sayfa yoksa uydurulmaz


class CevapBaglari(unittest.TestCase):
    def test_gecerli_baglar_yayimlanir(self):
        cevaplar = [{"q": SORULAR[0], "claims": [1]}, {"q": SORULAR[1], "claims": [3]},
                    {"q": "Is the export archive compressed?", "inferred_from": [2]},
                    {"q": "Should I enable backups?", "inferred_from": []}, SORULAR[2]]
        sonuc = kos(rapor(answers=cevaplar))
        self.assertEqual(sonuc["hukum"], "yayimlanir", sonuc["ret_nedenleri"])
        self.assertEqual(sonuc["uyarilar"], [])
        self.assertIn("answers", sonuc["kaynaga_baglanmayan_alanlar"])

    def test_gecersiz_bag_reddedilir(self):
        durumlar = {
            "var olmayan iddia": {"q": "Where?", "claims": [4]},
            "sıfır": {"q": "Where?", "claims": [0]},
            "yinelenen": {"q": "Where?", "claims": [1, 1]},
            "boş claims": {"q": "Where?", "claims": []},
            "ikisi birden": {"q": "Where?", "claims": [1], "inferred_from": [2]},
            "hiçbiri": {"q": "Where?"},
            "sayı değil": {"q": "Where?", "claims": ["1"]},
            "mantıksal değer": {"q": "Where?", "claims": [True]},
            "on birden çok": {"q": "Where?", "inferred_from": list(range(1, 12))},
        }
        for ad, madde in durumlar.items():
            with self.subTest(ad=ad):
                sonuc = kos(rapor(answers=[madde] + SORULAR[:2]), ag=False)
                self.assertEqual(sonuc["hukum"], "reddedilir")
                self.assertIn("sema.cevap_bagi", sonuc["ret_nedenleri"])
        sonuc = kos(rapor(answers=[{"q": "Where?", "claims": [1], "why": "x"}] + SORULAR[:2]), ag=False)
        self.assertIn("sema.bilinmeyen_alan", sonuc["ret_nedenleri"])
        sonuc = kos(rapor(answers=[{"q": "x" * 121, "claims": [1]}] + SORULAR[:2]), ag=False)
        self.assertIn("sema.uzunluk", sonuc["ret_nedenleri"])

    def test_bagli_cevabin_terimi_alintida_yoksa_uyari(self):
        cevaplar = [{"q": "Does export accept the --zip flag?", "claims": [2]},
                    {"q": "Are backups created every 24 hours?", "claims": [3]}, SORULAR[0]]
        sonuc = kos(rapor(answers=cevaplar))
        self.assertEqual(sonuc["hukum"], "yayimlanir")
        self.assertEqual(sonuc["uyarilar"], ["sema.cevap_bagi_terim"])
        bulgu = denetim(sonuc, "sema")["aciklama"]
        self.assertIn("paket.answers[0]", bulgu)
        self.assertIn("--zip", bulgu)
        self.assertNotIn("paket.answers[1]", bulgu)          # 24 bağlı alıntıda geçiyor
        cikarim = [{"q": "Does export accept the --zip flag?", "inferred_from": [2]}] + SORULAR[:2]
        self.assertEqual(kos(rapor(answers=cikarim))["uyarilar"], [])   # çıkarımda terim denetimi yok

    def test_k1_duz_metin_maddede_bag_taklidi_uyari_alir(self):
        sonuc = kos(rapor(answers=["Where are projects stored? -> claims 1, 2, 3"] + SORULAR[:2]))
        self.assertEqual(sonuc["uyarilar"], ["sema.cevap_bagi_bicimi"])
        self.assertEqual(kos(rapor(answers=["Where are projects stored? -> claims 1"] + SORULAR[:2]),
                             okuma_eki_acik=False)["uyarilar"], [])   # kapalı kip v0.2.3 gibi

    def test_kartta_answers_yine_yok(self):
        sonuc = kos(kart(answers=[{"q": "Where?", "claims": [1]}] * 3), ag=False)
        self.assertIn("sema.bilinmeyen_alan", sonuc["ret_nedenleri"])


class KisaCevapTerimi(unittest.TestCase):
    def test_alintida_olmayan_kod_adi_uyari_olur_ret_olmaz(self):
        sonuc = kos(rapor(short_answer="Widget 2.0 keeps projects locally; backups run every 24 hours; call backup_now() "
                                       "to force one."))
        self.assertEqual(sonuc["hukum"], "yayimlanir")
        self.assertEqual(sonuc["uyarilar"], ["sema.kisa_cevap_terim"])
        self.assertIn("backup_now", denetim(sonuc, "sema")["aciklama"])
        self.assertIn("schema (with warnings)", sonuc["kanit_etiketi_en"])

    def test_alintida_baslikta_ya_da_kapsamda_gecen_terim_uyari_degil(self):
        temiz = rapor(short_answer="Widget 2.0 keeps projects locally and creates backups every 24 hours.",
                      applies_to=[{"product": "widget", "versions": "2.0.1", "version_scheme": "exact"}])
        self.assertEqual(kos(temiz)["uyarilar"], [])
        self.assertEqual(okuma_eki.paket_kisa_cevap_eksikleri(rapor(short_answer="Use widget 2.0.1 here.", applies_to=[
            {"product": "widget", "versions": "2.0.1", "version_scheme": "exact"}])), [])
        self.assertEqual(okuma_eki.paket_kisa_cevap_eksikleri(rapor(short_answer="Set PER_PAGE_MAX to 100.")),
                         ["PER_PAGE_MAX", "100"])

    def test_terim_cikarimi(self):
        self.assertEqual(okuma_eki.terimler("Set PYTHON_GIL=0 or use -X gil=0 with Python 3.14 (sys._is_gil_enabled())."),
                         ["PYTHON_GIL", "-X", "3.14", "sys._is_gil_enabled"])
        self.assertEqual(okuma_eki.terimler("Python is fast, e.g. for GitHub and HMAC-SHA256 or GET /repos/{owner}/x."),
                         ["GitHub", "HMAC-SHA256", "/repos/{owner}/x"])
        self.assertEqual(okuma_eki.terimler("Use one page and 5 workers."), [])
        # Kabul Ö5: yol olmayan birleşik yazım `/` yerinden bölünür; yol bütün kalır.
        self.assertEqual(okuma_eki.terimler("Node v24.12.0/v25.2.0 and HTTP/3/QUIC, GET /repos/{owner}/{repo}/releases"),
                         ["v24.12.0", "v25.2.0", "HTTP", "QUIC", "/repos/{owner}/{repo}/releases"])

    def test_o5_yazim_cesitlemesi_kisa_cevapta_yanlis_alarm_vermez(self):
        paket = rapor(short_answer="Nodes v24.12.0/v25.2.0 keep projects locally (160-hour window).")
        paket["claims"][0] = dict(paket["claims"][0], quote=UZUN_ALINTI + " Releases v24.12.0 and v25.2.0, a 160 hours window.")
        self.assertEqual(okuma_eki.paket_kisa_cevap_eksikleri(paket), [])
        self.assertEqual(okuma_eki.eksik_terimler(["per_page"], ["the per-page limit"]), [])


class YoklukNotlari(unittest.TestCase):
    def test_bicim_ve_kaynak_numarasi(self):
        iyi = rapor(not_in_sources=[{"item": "Restore steps", "checked": [1, 2]}], not_researched=["Cloud sync costs."])
        sonuc = kos(iyi)
        self.assertEqual(sonuc["hukum"], "yayimlanir", sonuc["ret_nedenleri"])
        self.assertEqual(sonuc["kaynaga_baglanmayan_alanlar"], ["title", "summary", "not_in_sources", "not_researched"])
        self.assertIn("not-in-sources notes and not-researched notes", sonuc["kanit_etiketi_en"])
        for ad, madde in {"pakette olmayan kaynak": {"item": "x", "checked": [3]},
                          "boş liste": {"item": "x", "checked": []}, "sıfır": {"item": "x", "checked": [0]}}.items():
            with self.subTest(ad=ad):
                self.assertIn("sema.kaynak_no", kos(rapor(not_in_sources=[madde]), ag=False)["ret_nedenleri"])
        self.assertIn("sema.eksik_alan", kos(rapor(not_in_sources=[{"item": "x"}]), ag=False)["ret_nedenleri"])
        self.assertIn("sema.tip", kos(rapor(not_in_sources=["Restore steps"]), ag=False)["ret_nedenleri"])
        self.assertIn("sema.liste_boyutu", kos(rapor(not_researched=["x"] * 11), ag=False)["ret_nedenleri"])

    def test_kartta_sinirlar_eski_not_covered_ile_ayni(self):
        self.assertEqual(kos(kart(not_in_sources=[{"item": "Restore steps", "checked": [1]}]))["hukum"], "yayimlanir")
        self.assertIn("sema.kaynak_no", kos(kart(not_in_sources=[{"item": "x", "checked": [2]}]), ag=False)["ret_nedenleri"])
        self.assertIn("sema.uzunluk", kos(kart(not_in_sources=[{"item": "x" * 81, "checked": [1]}]), ag=False)["ret_nedenleri"])
        self.assertIn("sema.liste_boyutu", kos(kart(not_researched=["a", "b", "c", "d"]), ag=False)["ret_nedenleri"])

    def test_kaynak_sirasi_once_sources_sonra_listede_olmayan_iddia_adresi(self):
        disarida = "https://docs.example.org/widget/2.0/cli.html"
        paket = rapor()
        paket["claims"] = paket["claims"] + [dict(paket["claims"][0], source_url=disarida)]
        self.assertEqual(okuma_eki.paket_kaynak_sirasi(paket), [KAYNAK_1, KAYNAK_2, disarida])
        paket["sources"] = [paket["sources"][1], paket["sources"][0]]
        self.assertEqual(okuma_eki.paket_kaynak_sirasi(paket), [KAYNAK_2, KAYNAK_1, disarida])
        self.assertEqual(okuma_eki.paket_kaynak_sirasi(kart()), [KAYNAK_1])

    def test_terim_sayfada_bulunursa_uyari_bulunmazsa_satir(self):
        paket = rapor(not_in_sources=[
            {"item": "No maximum for `per_page` is given", "checked": [1]},     # SAYFA_1 tablosunda per_page var
            {"item": "No max_workers setting is described", "checked": [1, 2]},
            {"item": "No retention period for backups", "checked": [2]}])
        sonuc = kos(paket)
        self.assertEqual(sonuc["hukum"], "yayimlanir")
        self.assertEqual([(s["item"], s["terms"], s["found_in"], s["found_in_claims"]) for s in sonuc["yokluk_denetimi"]],
                         [(0, 1, [1], []), (1, 0, [], []), (2, 0, [], [])])   # tırnaksız max_workers aranmaz (Y1)
        self.assertEqual([s["checked_urls"] for s in sonuc["yokluk_denetimi"]], [[KAYNAK_1], [KAYNAK_1, KAYNAK_2], [KAYNAK_2]])
        self.assertEqual(sonuc["yokluk_denetimi"][0]["found_urls"], [KAYNAK_1])
        d11 = denetim(sonuc, "yokluk_notu")
        self.assertEqual((d11["sira"], d11["durum"], d11["engelleyici"]), (11, "uyari", False))
        self.assertIn("yokluk.terim_sayfada", sonuc["uyarilar"])
        self.assertIn("not_in_sources[0]", d11["aciklama"])
        self.assertIn("absence-note term search (with warnings)", sonuc["kanit_etiketi_en"])
        self.assertEqual([d["sira"] for d in sonuc["denetimler"]], [*range(1, 10), 11])

    def test_o1_yazim_cesitlemesi_cumle_ve_kaydin_kendi_alintisi_uyari_alir(self):
        """Kabul Ö1: `per-page`/`perpage` yazımı ve terimsiz ama sayfada (ve kaydın alıntısında) aynen duran cümle."""
        paket = kart(not_in_sources=[
            {"item": "No maximum for `per-page` is given", "checked": [1]},
            {"item": "No `perpage` parameter", "checked": [1]},
            {"item": "Whether cloud synchronization is disabled by default", "checked": [1]},
        ])
        sonuc = kos(paket)
        satirlar = {s["item"]: s for s in sonuc["yokluk_denetimi"]}
        self.assertEqual(satirlar[0]["found_in"], [1])
        self.assertEqual(satirlar[1]["found_in"], [1])
        self.assertEqual((satirlar[2]["terms"], satirlar[2]["found_in"], satirlar[2]["found_in_claims"]), (0, [1], [1]))
        self.assertIn("yokluk.alintida", sonuc["uyarilar"])
        self.assertIn("[2]", denetim(sonuc, "yokluk_notu")["aciklama"])
        self.assertEqual(sonuc["hukum"], "yayimlanir")       # uyarı, ret değil

    def test_o1_aranabilir_mi_ve_ifade_penceresi(self):
        self.assertTrue(okuma_eki.aranabilir_mi("No `x_y` value"))
        self.assertTrue(okuma_eki.aranabilir_mi("Whether cloud synchronization is disabled by default"))
        self.assertFalse(okuma_eki.aranabilir_mi("Restore steps"))
        hazir = okuma_eki.aranacak_metin("Cloud synchronization is disabled by default.")
        self.assertEqual(okuma_eki.ifade_gecen("whether Cloud synchronization is disabled by default", hazir),
                         "cloud synchronization is disabled by")
        self.assertIsNone(okuma_eki.ifade_gecen("the page is not about this at all", hazir))

    def test_y1_tirnaksiz_ad_aranmaz_tirnakli_terim_ve_cumle_aranir(self):
        """Kabul Y1 (düzeltme turu 2): yokluk aramasında yalnız çift tırnak/ters tırnak içindeki terim ve 5 sözcük penceresi.
        Gerçekçi `not_covered` maddeleri (26 paketin 121 maddesinden) kendi alıntılarıyla artık uyarı almaz; Ö1 dolanmaları
        (yazım çeşitlemesi, Kiril harf, görünmeyen ve tam genişlik karakter, aynı cümle) yine yakalanır."""
        gercekci = [
            ("No benchmark, speedup figure or memory measurement was produced; none of the cited pages compares DuckDB and "
             "Polars speed.", "DuckDB can read Parquet files directly and run SQL over Polars DataFrames."),
            ("Live measurement or testing; GitHub Enterprise Server differences.",
             "GitHub sends a webhook delivery with an X-GitHub-Delivery header."),
            ("Exact behaviour of Node.js 23.x patch releases and of v22.6.0–v22.17.x warnings was not checked release by "
             "release.", "Node.js v22.18.0 enables type stripping by default."),
            ("PostgreSQL 17 and earlier", "PostgreSQL 18 adds virtual generated columns; in PostgreSQL 17 they are stored."),
            ("Only the 'latest' man pages (systemd 262 label) were read.", "This is the latest version of the page."),
            ("DuckDB's out-of-core behavior and Polars' GPU engine were not covered.", "DuckDB spills to disk."),
        ]
        for madde, alinti in gercekci:
            self.assertIsNone(okuma_eki.yokluk_eslesmesi(madde, okuma_eki.aranacak_metin(alinti)), madde)
        self.assertEqual(okuma_eki.yokluk_terimleri("Only the 'latest' man pages; DuckDB's and Polars' GPU"), [])
        self.assertEqual(okuma_eki.yokluk_terimleri('No `DuckDB` or "Polars GPU" benchmark'), ["DuckDB", "Polars GPU"])
        # Katkıcı tırnakla işaretlerse aranır ve bulunursa uyarı.
        hazir = okuma_eki.aranacak_metin("DuckDB can read Parquet files directly.")
        self.assertEqual(okuma_eki.yokluk_eslesmesi("No `DuckDB` comparison", hazir), "term")
        # Ö1 dolanmaları (sayfa: per_page ve "Cloud synchronization is disabled by default.").
        sayfa = okuma_eki.aranacak_metin(normallestir(gorunur_metin(SAYFA_1.encode("utf-8"), "text/html")))
        yakalanir = {"No maximum for `per-page` is given": "term", "No `perpage` parameter": "term",
                     "`PER_PAGE` has no documented default": "term",
                     "No maximum for `\u0440er_page` is given": "term",                     # Kiril \u0440
                     "No maximum for `per\u200b_page` is given": "term",                    # sıfır genişlik
                     "No maximum for `\uff50\uff45\uff52_\uff50\uff41\uff47\uff45` is given": "term",   # tam genişlik
                     "Whether cloud synchronization is disabled by default": "wording",
                     "Whether cl\u043eud synchronization is disabled by default": "wording"}   # Kiril \u043e
        for madde, tur in yakalanir.items():
            self.assertEqual(okuma_eki.yokluk_eslesmesi(madde, sayfa), tur, madde)
        # Temkinli yön: tırnaksız ad aranmaz (uyarı yok) ve köprü "denetlendi" demez (not tırnaksız adların aranmadığını
        # söyler; aranacak hiçbir şey yoksa "not checked by the gate").
        self.assertIsNone(okuma_eki.yokluk_eslesmesi("PER_PAGE has no documented default", sayfa))
        self.assertFalse(okuma_eki.aranabilir_mi("No perpage parameter"))

    def test_ka_tirnakli_terim_sozcuk_siniriyla_aranir(self):
        """Kabul K-a: tırnaklı terim sözcük içinde eşleşmez (`page` ↔ `per_page`, `dat` ↔ "data", `oud` ↔ "cloud"); yazım
        çeşitlemesi yalnız sözcük sınırında (`per-page`, `perpage`, `PER_PAGE`, `per page` ↔ `per_page`)."""
        hazir = okuma_eki.aranacak_metin("<td>per_page</td> Each project is a folder in the user's data directory. "
                                         "Cloud synchronization is off. Use os.path.join here.")
        for terim in ("page", "per", "dat", "oud", "ectory", "_page", "join here x"):
            self.assertFalse(okuma_eki.yokluk_terim_geciyor(terim, hazir), terim)
        for terim in ("per_page", "per-page", "perpage", "PER_PAGE", "per page", "Per​_Page", "рer_page",
                      "data", "data directory", "os.path.join", "os.path", "cloud"):
            self.assertTrue(okuma_eki.yokluk_terim_geciyor(terim, hazir), terim)
        # Madde düzeyinde: `page` artık per_page yüzünden uyarı almaz; sayfada tek başına "page" sözcüğü varsa alır.
        self.assertIsNone(okuma_eki.yokluk_eslesmesi("No `page` limit", hazir))
        self.assertEqual(okuma_eki.yokluk_eslesmesi("No `page` limit", okuma_eki.aranacak_metin("results per page")), "term")
        # Kısa cevap denetiminin eski kuralı değişmedi (alt dizge; Ö5 sayımı aynı kalır).
        self.assertTrue(okuma_eki.terim_geciyor("page", hazir))

    def test_y1_kapida_tirnaksiz_terim_uyari_vermez_tirnakli_verir(self):
        paket = kart(not_in_sources=[{"item": "PER_PAGE has no documented default", "checked": [1]},
                                     {"item": "`PER_PAGE` has no documented default", "checked": [1]}])
        sonuc = kos(paket)
        satirlar = {s["item"]: s for s in sonuc["yokluk_denetimi"]}
        self.assertEqual((satirlar[0]["terms"], satirlar[0]["found_in"]), (0, []))
        self.assertEqual((satirlar[1]["terms"], satirlar[1]["found_in"]), (1, [1]))
        self.assertIn("not_in_sources[1]", denetim(sonuc, "yokluk_notu")["aciklama"])
        self.assertNotIn("not_in_sources[0]", denetim(sonuc, "yokluk_notu")["aciklama"])
        self.assertIn("tırnaksız adları", " ".join(sonuc["kanitlamadiklari"]))

    def test_y1_katlama_yalniz_karsilastirma(self):
        # Katlama iki yana da uygulanan karşılaştırmadır: ASCII metinde sonuç aynı, benzer harf Latin'e iner.
        self.assertEqual(okuma_eki.katla("Per_Page\u200b"), "per_page")
        self.assertEqual(okuma_eki.katla("\u0420ER_PAGE"), "per_page")
        self.assertEqual(okuma_eki.eksik_terimler(["backup_now", "v2"], ["Call backup_now() in v2."]), [])

    def test_agsiz_kipte_aranmaz_ve_yayimlanamaz(self):
        sonuc = kos(rapor(not_in_sources=[{"item": "No max_workers setting", "checked": [1]}]), ag=False)
        self.assertEqual(sonuc["hukum"], "yayimlanamaz")
        self.assertEqual(denetim(sonuc, "yokluk_notu")["durum"], "denenemedi")
        self.assertEqual(sonuc["yokluk_denetimi"], [])


if __name__ == "__main__":
    unittest.main()

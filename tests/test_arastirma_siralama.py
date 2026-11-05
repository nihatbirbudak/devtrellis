"""Sıralı aramanın teknik belirteçlerini, sınırlarını ve kısmi kapsam açıklamasını sınar."""

import unittest

from radar.arastirma_siralama import aday_kapsami, belge_terimleri, eslesme_bilgisi, sorgu_hazirla


class SiralamaTestleri(unittest.TestCase):
    def test_soru_baglaclari_cikar_icerik_kaybi_acik_kalir(self):
        plan = sorgu_hazirla("Why does Python need dependency checks?")
        fields = belge_terimleri({"title": "Python dependency", "summary": "", "body": "", "tags": []})
        match = eslesme_bilgisi(plan, fields)
        self.assertIn("python", match["matched_terms"])
        self.assertIn("checks", match["missing_terms"])
        self.assertFalse(match["all_terms_matched"])
        self.assertEqual(match["answer_sufficiency"], "not_assessed")

    def test_turkce_ascii_ve_yaygin_teknik_karsiliklar(self):
        packet = {"title": "Conditional request caching", "summary": "", "body": "", "tags": []}
        for query in ("koşullu istek önbellek", "KOSULLU ISTEK ONBELLEK"):
            self.assertTrue(eslesme_bilgisi(sorgu_hazirla(query), belge_terimleri(packet))["all_terms_matched"])

    def test_teknik_isaretler_baska_surumle_karismaz(self):
        fields = belge_terimleri({"title": "C++ Python 3.14 /api/v1 FTS5", "summary": "", "body": "", "tags": []})
        for query in ("C++", "3.14", "/api/v1", "FTS5"):
            plan = sorgu_hazirla(query)
            self.assertTrue(plan["required"])
            self.assertTrue(eslesme_bilgisi(plan, fields)["all_terms_matched"])
        self.assertFalse(eslesme_bilgisi(sorgu_hazirla("3.15"), fields)["all_terms_matched"])

    def test_sql_sorgu_dili_kullaniciya_acilmaz(self):
        for query in ('" OR * NEAR(x) --', "%' OR 1=1 --", "x'); DROP TABLE reports; --"):
            plan = sorgu_hazirla(query)
            self.assertRegex(plan["match"], r'^"t[0-9a-f]+"(?: OR "t[0-9a-f]+")*$')
        self.assertTrue(sorgu_hazirla("%_")["match"])
        self.assertEqual(sorgu_hazirla("")["match"], "")

    def test_bilinmeyen_belirtec_ve_sinir_korunur(self):
        plan = sorgu_hazirla("Python missing_xyz")
        self.assertEqual(plan["required"], (1,))
        self.assertEqual(plan["minimum"], 2)
        for query in (None, True, 1, "x" * 201, "a\nb", " ".join(f"term{n}" for n in range(25))):
            with self.assertRaises(ValueError):
                sorgu_hazirla(query)

    def test_bilesik_sozcuk_ve_ingilizce_ekler(self):
        fields = belge_terimleri({"title": "Free-threaded builds", "summary": "", "body": "", "tags": []})
        self.assertTrue(eslesme_bilgisi(sorgu_hazirla("free threading build"), fields)["all_terms_matched"])
        self.assertTrue(eslesme_bilgisi(sorgu_hazirla("free-threading"), fields)["all_terms_matched"])
        self.assertEqual(len(sorgu_hazirla("source sources kaynak kaynaklar")["groups"]), 1)

    def test_miktar_soru_icin_iddiadir_zorunlu_surum_degildir(self):
        plan = sorgu_hazirla("Does CSS require 50 pixels in spec 3.2?")
        self.assertNotIn(plan["labels"].index("50"), plan["required"])
        self.assertIn(plan["labels"].index("3.2"), plan["required"])

    def test_genel_govde_kelimeleri_yetersiz_kismi_adaydir(self):
        fields = belge_terimleri({"title": "Database integrity", "summary": "", "body": "Keep work without losing edits.", "tags": []})
        self.assertEqual(aday_kapsami(sorgu_hazirla("without edits gardening tools"), fields), -1)
        self.assertGreater(aday_kapsami(sorgu_hazirla("database edits gardening tools"), fields), 0)
        self.assertGreater(aday_kapsami(sorgu_hazirla("without edits"), fields), 0)

    def test_bilinen_kavramin_turkce_eki_ve_uzun_soru(self):
        fields = belge_terimleri({"title": "Keyboard accessibility", "summary": "", "body": "", "tags": []})
        plan = sorgu_hazirla("Klavyeyle gezinirken uygulamamın erişilebilirliğinde yaşadığım sorunu hangi yöntemle anlamalıyım?")
        match = eslesme_bilgisi(plan, fields)
        self.assertEqual(plan["minimum"], 2)
        self.assertIn("klavyeyle", match["matched_terms"])
        # Ünsüz değişimi için genel ve hataya açık kök bulma yapılmaz.
        self.assertIn("erisilebilirliginde", match["missing_terms"])


if __name__ == "__main__":
    unittest.main()

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

    def test_ros_yazim_bicimi_iki_yonde_esdeger_surumu_korur(self):
        spellings = {1: ("ROS 1", "ros1"), 2: ("ROS 2", "ros2"), 12: ("ROS 12", "ros12")}
        for document_version, titles in spellings.items():
            for title in titles:
                fields = belge_terimleri({"title": title + " robot recording", "summary": "", "body": "", "tags": []})
                self.assertGreater(aday_kapsami(sorgu_hazirla("ROS robot recording"), fields), 0)
                for query_version, queries in spellings.items():
                    for query in queries:
                        with self.subTest(title=title, query=query):
                            plan = sorgu_hazirla(query + " robot recording")
                            self.assertEqual(plan["labels"][0], "ros" + str(query_version))
                            self.assertIn(0, plan["required"])
                            self.assertEqual(aday_kapsami(plan, fields) >= 0, document_version == query_version)
                            self.assertEqual(eslesme_bilgisi(plan, fields)["answer_sufficiency"], "not_assessed")

    def test_ros_normalizasyonu_diger_ad_sayi_ve_isaretleri_birlestirmez(self):
        for query, labels in (
            ("ROS 2", ("ros2",)), ("micros 2", ("micros", "2")), ("rosbag 2", ("rosbag", "2")),
            ("/ros 2", ("ros", "2")), ("ROS 2.1", ("ros", "2.1")), ("ROS 2beta", ("ros", "2beta")),
            ("Python 3.14 C++ FTS5 requires 44 pixels", ("python", "3.14", "c++", "fts5", "requires", "44", "pixels")),
        ):
            with self.subTest(query=query):
                self.assertEqual(sorgu_hazirla(query)["labels"], labels)
        fields = belge_terimleri({"title": "C++ Python 3.14 FTS5", "summary": "", "body": "", "tags": []})
        for query in ("C++", "Python 3.14", "FTS5"):
            self.assertGreater(aday_kapsami(sorgu_hazirla(query), fields), 0)
        for query in ("C#", "Python 3.13", "FTS6"):
            self.assertEqual(aday_kapsami(sorgu_hazirla(query), fields), -1)

    def test_karsilastirma_baglaci_icerik_terimi_sayilmaz(self):
        for title, left, right in (("DuckDB Polars CSV", "DuckDB", "Polars"), ("Rust Go compilation", "Rust", "Go")):
            fields = belge_terimleri({"title": title, "summary": "", "body": "", "tags": []})
            for connector in ("vs", "versus", "VS", "versus."):
                with self.subTest(title=title, connector=connector):
                    plan = sorgu_hazirla(left + " " + connector + " " + right)
                    self.assertEqual(len(plan["groups"]), 2)
                    self.assertTrue(eslesme_bilgisi(plan, fields)["all_terms_matched"])
                    self.assertGreater(aday_kapsami(plan, fields), 0)
            for connector in ("vs", "versus"):
                self.assertEqual(aday_kapsami(sorgu_hazirla(connector), fields), -1)

    def test_baglaci_cikarmak_belgede_olmayan_urun_kapsami_uretmez(self):
        fields = belge_terimleri({"title": "DuckDB Polars CSV", "summary": "", "body": "", "tags": []})
        for query in ("duckdb vs pandas", "duckdb versus pandas"):
            plan = sorgu_hazirla(query)
            self.assertEqual(plan["minimum"], 2)
            self.assertEqual(aday_kapsami(plan, fields), -1)
            self.assertEqual(eslesme_bilgisi(plan, fields)["missing_terms"], ["pandas"])

    def test_veritabani_karsiligi_baska_urun_adi_uretmiyor(self):
        fields = belge_terimleri({"title": "SQLite database indexing", "summary": "", "body": "", "tags": []})
        for query in ("veritabanı", "veri tabanı", "veritabanlarında", "DATABASES"):
            with self.subTest(query=query):
                self.assertGreater(aday_kapsami(sorgu_hazirla(query), fields), 0)
        self.assertEqual(aday_kapsami(sorgu_hazirla("PostgreSQL database indexing"), fields), -1)

    def test_urun_ve_yaziyla_surumu_kismi_eslesmede_atlamaz(self):
        examples = (
            ("ROS 2 Jazzy recording QoS", "ROS 2 Humble recording QoS"),
            ("ROS 2 Jazzy recording QoS", "ROS 2 Kilted recording QoS"),
            ("Firebase notifications delivery", "Android notifications delivery"),
            ("Python cooperative thread work without locks", "CUDA cooperative thread work without locks"),
            ("Python cooperative thread work without locks", "ROCm cooperative thread work without locks"),
            ("SQLite database indexing", "Postgres database indexing"),
        )
        for title, query in examples:
            with self.subTest(query=query):
                fields = belge_terimleri({"title": title, "summary": "", "body": "", "tags": []})
                self.assertEqual(aday_kapsami(sorgu_hazirla(query), fields), -1)
                self.assertEqual(eslesme_bilgisi(sorgu_hazirla(query), fields)["answer_sufficiency"], "not_assessed")
        fields = belge_terimleri({"title": "PostgreSQL database indexing", "summary": "", "body": "", "tags": []})
        self.assertGreater(aday_kapsami(sorgu_hazirla("Postgres database indexing"), fields), 0)

    def test_ad_eslesmesi_dislanan_kapsami_dogrulanmis_yapmaz(self):
        fields = belge_terimleri({"title": "ROS 2 Jazzy", "summary": "", "body": "Humble is not covered.", "tags": []})
        match = eslesme_bilgisi(sorgu_hazirla("ROS 2 Humble"), fields)
        self.assertTrue(match["all_terms_matched"])
        self.assertEqual(match["answer_sufficiency"], "not_assessed")

    def test_sql_sorgu_dili_kullaniciya_acilmaz(self):
        for query in ('" OR * NEAR(x) --', "%' OR 1=1 --", "x'); DROP TABLE reports; --"):
            plan = sorgu_hazirla(query)
            self.assertRegex(plan["match"], r'^"t[0-9a-f]+"(?: OR "t[0-9a-f]+")*$')
        self.assertTrue(sorgu_hazirla("%_")["match"])
        self.assertEqual(sorgu_hazirla("")["match"], "")

    def test_bilinmeyen_belirtec_ve_sinir_korunur(self):
        plan = sorgu_hazirla("Python missing_xyz")
        self.assertEqual(plan["required"], (0, 1))
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

"""Teknik arama sözlüğünü, sınırlarını ve örnek Türkçe İngilizce erişimini sınar."""

import sqlite3
import unittest

from radar.arastirma_arama import GRUP_SINIRI, SECENEK_SINIRI, arama_gruplari
from radar.gelistirici_alani import arama_metni


# Elle oluşturulmuş etiketli örneklerdir; insan araştırmasıyla doğrulanmış bir
# değerlendirme kümesi, üretim erişim ölçümü veya anlamsal başarı iddiası değildir.
CORPUS = (
    ("fts", "SQLite FTS5 BM25 relevance ranking. Full text search ranks matching documents."),
    ("mcp", "MCP Apps review interface. Interactive views in supporting hosts."),
    ("nim", "NVIDIA NIM API prototyping. Hosted endpoints for developers."),
    ("python", "Python package release notes. Changelog and migration notes."),
    ("research", "Technical research source evaluation and ranking."),
    ("cache", "Cache permission rules for public clients."),
    ("turkish", "SQLite araştırma kaynakları: tam metin arama sıralaması."),
    ("unrelated", "A full moon photograph. A text editor theme. A separate ranking article."),
    ("proper-name", "Frank writes about Python graphics."),
)


def eski_gruplar(query):
    return tuple((term,) for term in dict.fromkeys(arama_metni(query).split()))


class ArastirmaAramaTestleri(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute("CREATE TABLE reports (id TEXT PRIMARY KEY, search_text TEXT NOT NULL)")
        self.connection.executemany("INSERT INTO reports VALUES (?,?)",
                                    [(identifier, arama_metni(text)) for identifier, text in CORPUS])

    def bul(self, query, *, old=False):
        groups = eski_gruplar(query) if old else arama_gruplari(query)
        clauses = ["(" + " OR ".join("instr(r.search_text,?)>0" for _ in group) + ")" for group in groups]
        values = [term for group in groups for term in group]
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return {row[0] for row in self.connection.execute("SELECT id FROM reports r" + where, values)}

    def test_bos_sorgu_ve_tekrarlanan_terimler(self):
        self.assertEqual(arama_gruplari("  "), ())
        self.assertEqual(arama_gruplari("SQLite SQLite"), (("sqlite",),))
        self.assertEqual(arama_gruplari("kaynak source kaynaklar"), arama_gruplari("source"))

    def test_turkce_buyuk_harf_ve_nfkc_normalizasyonu(self):
        self.assertEqual(arama_gruplari("İZİN SIRALAMA"), arama_gruplari("izin sıralama"))
        self.assertEqual(arama_gruplari("ＦＴＳ５"), (("fts5",),))
        self.assertEqual(arama_gruplari("araştırma"), arama_gruplari("ARASTIRMA"))

    def test_cok_sozcuklu_terimler_butun_olarak_kalir(self):
        self.assertEqual(arama_gruplari("tam metin"), arama_gruplari("full text"))
        self.assertEqual(arama_gruplari("full-text"), arama_gruplari("fulltext"))
        self.assertEqual(len(arama_gruplari("tam metin arama sıralaması")), 3)
        self.assertNotIn("unrelated", self.bul("tam metin sıralama"))

    def test_turkce_sorgu_ingilizce_bulguya_ulasir(self):
        for query, wanted in (("tam metin arama sıralaması", "fts"),
                              ("araştırma kaynak sıralama", "research"),
                              ("önbellek izin", "cache")):
            with self.subTest(query=query):
                self.assertNotIn(wanted, self.bul(query, old=True))
                self.assertIn(wanted, self.bul(query))

    def test_ingilizce_sorgu_turkce_bulguya_ulasir(self):
        self.assertNotIn("turkish", self.bul("SQLite research source full text search ranking", old=True))
        self.assertIn("turkish", self.bul("SQLite research source full text search ranking"))

    def test_iki_eski_kaybin_yalniz_sozlukte_olani_cozulur(self):
        query = "tam metin arama sıralaması"
        self.assertNotIn("fts", self.bul(query, old=True))
        self.assertIn("fts", self.bul(query))
        general = "finding earlier work with different wording"
        self.assertEqual(self.bul(general, old=True), set())
        self.assertEqual(self.bul(general), set())

    def test_ozel_tanimlayicilar_ve_eski_olumlu_sorgular_korunur(self):
        for query, wanted in (("SQLite FTS5", "fts"), ("sqlite fts5", "fts"),
                              ("FTS5 SQLite", "fts"), ("bm25", "fts"),
                              ("full text search", "fts"), ("MCP Apps", "mcp"),
                              ("NVIDIA API", "nim"), ("Python release", "python")):
            with self.subTest(query=query):
                self.assertIn(wanted, self.bul(query, old=True))
                self.assertIn(wanted, self.bul(query))
        self.assertEqual(arama_gruplari("C++ v1.2 /api/v1"), (("c++",), ("v1.2",), ("/api/v1",)))

    def test_gruplar_arasi_ve_bilinmeyen_terimlerde_ve_kosulu_korunur(self):
        for query in ("source cache", "izin sıralama", "araştırma missing_xyz", "tam metin permission"):
            with self.subTest(query=query):
                self.assertEqual(self.bul(query), set())
        self.assertNotIn("proper-name", self.bul("sıralama"))

    def test_sql_ve_like_isaretleri_yalniz_parametre_olarak_kullanilir(self):
        for query in ("' OR 1=1 --", "%' OR 1=1 --", "%", "_", "x'); DROP TABLE reports; --"):
            with self.subTest(query=query):
                self.assertEqual(self.bul(query), set())
        count = self.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        self.assertEqual(count, len(CORPUS))

    def test_sorgu_tipi_karakter_ve_terim_sinirlari(self):
        for query in (None, True, 1, [], {}, "x" * 201, "a\x00b", "a\nb"):
            with self.subTest(query=query):
                with self.assertRaises(ValueError):
                    arama_gruplari(query)
        self.assertEqual(arama_gruplari("x" * 200), (("x" * 200,),))
        at_limit = " ".join(f"q{index}" for index in range(GRUP_SINIRI))
        self.assertEqual(len(arama_gruplari(at_limit)), GRUP_SINIRI)
        with self.assertRaises(ValueError):
            arama_gruplari(at_limit + " qextra")

    def test_karsiliklar_sinirli_ve_tekrarlar_sonlu(self):
        query = "research tam metin ranking search source cache permission"
        groups = arama_gruplari(query)
        self.assertLessEqual(len(groups), GRUP_SINIRI)
        self.assertTrue(all(len(group) <= SECENEK_SINIRI for group in groups))
        self.assertEqual(arama_gruplari("source " * 28), arama_gruplari("source"))


if __name__ == "__main__":
    unittest.main()

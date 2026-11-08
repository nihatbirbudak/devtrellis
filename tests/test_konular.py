"""Konu genişlemesinin eski kişisel verileri koruduğunu ve yeni alanların aynı akışta çalıştığını doğrular."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from radar.depo import Depo
from radar.katalog import kartlari_listele
from radar.kaynak_havuzu import kaynaklari_oku
from radar.kesif import kesif_kaynaklari
from radar.kisisel import KisiselAlan, tablolari_hazirla, varsayilan_ayarlar
from radar.konular import ESKI_KONULAR, KONULAR, konu, konulari_listele
from radar.proje_profilleri import profil, profiller, on_arastirma_profili
from radar.secim import gunluk_sec


class KonuTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "topics.sqlite3"
        self.source = {"id": "legacy", "name": "Eski kaynak", "topic": "ai", "enabled": True,
                       "interval_hours": 24, "benefit": "Araştırma"}
        self.depo = Depo(self.path, [self.source])

    def legacy_database(self):
        area = KisiselAlan(self.depo)
        collection = area.koleksiyon_olustur(name="Özel koleksiyon", description="Korunacak not")
        settings = {"topic_weights": {key: 2 for key in ESKI_KONULAR}, "daily_limit": 13,
                    "keywords": ["korunacak"], "muted_keywords": ["reklam"]}
        settings["topic_weights"]["ai"] = 5
        with self.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE user_settings SET settings=?,version=7,updated_at='2026-10-01' WHERE id=1",
                               (json.dumps(settings),))
            connection.execute("DROP TABLE saved_searches")
            connection.execute("CREATE TABLE saved_searches(id TEXT PRIMARY KEY,name TEXT NOT NULL,query TEXT NOT NULL,"
                "topic TEXT CHECK(topic IS NULL OR topic IN ('ai','jarvis','robotik','tarim','medya')),"
                "source_id TEXT REFERENCES sources(id),archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),"
                "version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,"
                "collection_id TEXT REFERENCES user_collections(id),read_status TEXT NOT NULL DEFAULT 'all',"
                "sort TEXT NOT NULL DEFAULT 'relevance',legacy_extra TEXT NOT NULL DEFAULT 'korundu')")
            connection.execute("INSERT INTO saved_searches VALUES('old','Eski kayıt','Kaynaklı soru','ai','legacy',1,4,"
                               "'2026-09-01','2026-10-01',?,'unread','oldest','ek veri')", (collection["id"],))
            connection.execute("CREATE INDEX legacy_search_topic ON saved_searches(topic)")
            connection.execute("CREATE TABLE migration_audit(value TEXT)")
            connection.execute("CREATE TRIGGER legacy_search_updated AFTER UPDATE ON saved_searches "
                               "BEGIN INSERT INTO migration_audit(value) VALUES(NEW.id); END")
            old_row = dict(connection.execute("SELECT * FROM saved_searches").fetchone())
        return old_row, collection, settings

    def test_legacy_check_gecisi_satirlari_iliski_ve_ayar_surumunu_korur(self):
        old_row, collection, settings = self.legacy_database()
        reopened = KisiselAlan(Depo(self.path, [self.source]))
        actual = reopened.ayarlar_oku()
        self.assertEqual(actual["version"], 7)
        self.assertEqual(actual["updated_at"], "2026-10-01")
        self.assertEqual(actual["keywords"], settings["keywords"])
        for topic in ESKI_KONULAR:
            self.assertEqual(actual["topic_weights"][topic], settings["topic_weights"][topic])
        for topic in set(KONULAR) - ESKI_KONULAR:
            self.assertEqual(actual["topic_weights"][topic], 3)
        with self.depo.baglanti() as connection:
            self.assertEqual(dict(connection.execute("SELECT * FROM saved_searches WHERE id='old'").fetchone()), old_row)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN "
                                               "('legacy_search_topic','legacy_search_updated')").fetchone()[0], 2)
        self.assertEqual(reopened.snapshot()["collections"][0], collection)
        new_search = reopened.arama_kaydet(name="Güvenlik", topic="security", collection_id=collection["id"])
        self.assertEqual(new_search["topic"], "security")
        reopened.arama_sil("old", expected_version=4)
        with self.depo.baglanti() as connection:
            # Zaten arşivli kaydın aynı isteği yeni olay üretmez.
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM migration_audit").fetchone()[0], 0)
            connection.execute("UPDATE saved_searches SET name='Düzeltilmiş ad' WHERE id='old'")
            self.assertEqual(connection.execute("SELECT value FROM migration_audit").fetchone()[0], "old")
        self.assertEqual(KisiselAlan(Depo(self.path, [self.source])).ayarlar_oku(), actual)

    def test_gecis_cagiran_islemi_bitirmez_hata_olursa_eski_sema_kalir(self):
        old_row, collection, settings = self.legacy_database()
        with self.assertRaises(RuntimeError):
            with self.depo.baglanti(write=True) as connection:
                tablolari_hazirla(connection)
                connection.execute("UPDATE saved_searches SET topic='data' WHERE id='old'")
                raise RuntimeError("Çağıranın işlemi geri alındı")
        with self.depo.baglanti() as connection:
            schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='saved_searches'").fetchone()[0]
            self.assertIn("topic IN", schema)
            self.assertEqual(dict(connection.execute("SELECT * FROM saved_searches").fetchone()), old_row)
            self.assertEqual(json.loads(connection.execute("SELECT settings FROM user_settings").fetchone()[0]), settings)

    def test_eski_bes_agirlik_kabul_edilir_diger_eksikler_reddedilir(self):
        area = KisiselAlan(self.depo)
        settings = varsayilan_ayarlar()
        settings["topic_weights"] = {key: 4 for key in ESKI_KONULAR}
        saved = area.ayarlar_kaydet(settings, expected_version=0)
        self.assertEqual(saved["topic_weights"]["software"], 3)
        invalid = [dict(saved["topic_weights"]), {key: 3 for key in ESKI_KONULAR if key != "ai"},
                   {**saved["topic_weights"], "unknown": 3}, {**saved["topic_weights"], "data": True}]
        invalid[0].pop("data")
        for weights in invalid:
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                area.ayarlar_kaydet({**settings, "topic_weights": weights}, expected_version=1)
        self.assertEqual(area.ayarlar_oku(), saved)

    def test_yeni_konular_katalog_kayitli_arama_ve_gunluk_secimde_calisir(self):
        new_topics = ("software", "security", "data")
        sources = [{**self.source, "id": topic, "topic": topic} for topic in new_topics]
        depo = Depo(self.path, sources)
        area = KisiselAlan(depo)
        for topic in new_topics:
            depo.kaydet(topic, [{"url": f"https://example.org/{topic}", "title": f"Yeni {topic} gelişmesi"}])
            self.assertEqual(kartlari_listele(depo, topic=topic)["total"], 1)
            self.assertEqual(area.arama_kaydet(name=topic, topic=topic)["topic"], topic)
        selected = gunluk_sec(depo.gorunum()["items"], limit=3, now=datetime.now(timezone.utc))
        self.assertEqual({item["topic"] for item in selected}, set(new_topics))

    def test_registry_profilleri_ve_kesifleri_tek_tanimdan_verir(self):
        self.assertEqual(ESKI_KONULAR, {"ai", "jarvis", "robotik", "tarim", "medya"})
        self.assertEqual(set(KONULAR) - ESKI_KONULAR, {"software", "security", "data"})
        self.assertEqual({value["id"] for value in konulari_listele()}, set(KONULAR))
        self.assertTrue(all(set(value) == {"id", "public_id", "aliases", "label_tr", "label_en"} for value in konulari_listele()))
        self.assertEqual({value["topic"] for value in profiller()}, set(KONULAR))
        self.assertEqual({value["topic"] for value in kesif_kaynaklari()}, set(KONULAR))
        from radar.arastirma_icerigi import profil_dogrula
        for topic in KONULAR:
            definition = konu(topic)
            self.assertEqual(profil_dogrula(profil(topic)), profil(topic))
            self.assertEqual(on_arastirma_profili({**profil(topic), "summary": "Özel not"})["summary"], definition["profile"]["summary"])
            definition["profile"]["summary"] = "Çağıran değiştirdi"
            self.assertNotEqual(konu(topic)["profile"]["summary"], definition["profile"]["summary"])

    def test_gelistirici_kaynaklari_tekrarsiz_ve_acik_hostlarla_kayitli(self):
        sources = kaynaklari_oku()
        self.assertLessEqual(len(sources), 200)
        urls = [source["fetch_url"] for source in sources]
        self.assertEqual(len(urls), len(set(urls)))
        expected = {"python_blog": "software", "github_changelog": "software",
                    "postgresql_news": "data", "google_security": "security"}
        for identifier, topic in expected.items():
            source = next(source for source in sources if source["id"] == identifier)
            self.assertEqual(source["topic"], topic)
            self.assertEqual(source["kind"], "rss")
            self.assertFalse(any("*" in host for host in source["fetch_hosts"] + source["item_hosts"]))


if __name__ == "__main__":
    unittest.main()

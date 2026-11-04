"""Kişisel ayarların, okuma durumunun ve koleksiyonların gerçek depoda güvenle korunduğunu doğrular."""

import copy
import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from radar.depo import Depo
from radar.konular import KONULAR
from radar.kisisel import (KisiselAlan, SurumCatismasi, kisisel_puan, kisisel_sirala,
                           tablolari_hazirla, varsayilan_ayarlar)


class KisiselAlanTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sources = [
            {"id": "ai_kaynak", "name": "AI kaynak", "topic": "ai", "enabled": True,
             "interval_hours": 24, "benefit": "Ajan ve yerel model araştırması"},
            {"id": "sera_kaynak", "name": "Sera kaynak", "topic": "tarim", "enabled": True,
             "interval_hours": 24, "benefit": "Sera araştırması"},
        ]
        self.path = Path(temporary.name) / "veri.sqlite3"
        self.depo = Depo(self.path, self.sources)
        self.depo.kaydet("ai_kaynak", [
            {"external_id": str(index), "url": f"https://example.org/haber/{index}",
             "title": f"Ajan araştırması {index}", "excerpt": "Yerel model ve ses desteği", "language": "tr"}
            for index in range(3)])
        self.depo.kaydet("sera_kaynak", [
            {"external_id": "sera", "url": "https://example.org/sera", "title": "Sera ışık yönetimi",
             "excerpt": "Su, bitki besleme ve sulama", "language": "tr"}])
        self.ids = [item["id"] for item in self.depo.gorunum()["items"]]
        self.alan = KisiselAlan(self.depo)

    def ayar(self, **changes):
        settings = varsayilan_ayarlar()
        settings.update(changes)
        return settings

    def yeniden_ac(self):
        return KisiselAlan(Depo(self.path, self.sources))

    def test_varsayilanlar_ve_bagimsiz_kopyalar(self):
        snapshot = self.alan.snapshot()
        self.assertEqual(snapshot["settings"]["topic_weights"], {key: 3 for key in KONULAR})
        self.assertEqual(snapshot["settings"]["daily_limit"], 8)
        self.assertEqual(snapshot["settings"]["version"], 0)
        self.assertEqual(snapshot["read_states"], [])
        self.assertEqual(snapshot["stats"], {"read_count": 0, "unread_count": 4,
                                             "collection_count": 0, "saved_search_count": 0})
        snapshot["settings"]["topic_weights"]["ai"] = 5
        self.assertEqual(self.alan.ayarlar_oku()["topic_weights"]["ai"], 3)
        first = varsayilan_ayarlar()
        first["keywords"].append("sera")
        self.assertEqual(varsayilan_ayarlar()["keywords"], [])

    def test_ayar_kalici_surumlu_ve_tekrar_idempotent(self):
        settings = self.ayar(daily_limit=20, keywords=[" İKLİM ", "iklim", "sera"], muted_keywords=["tanıtım"])
        settings["topic_weights"]["tarim"] = 5
        settings["topic_weights"]["medya"] = 0
        saved = self.alan.ayarlar_kaydet(settings, expected_version=0)
        self.assertEqual(saved["keywords"], ["İKLİM", "sera"])
        self.assertEqual(saved["version"], 1)
        self.assertEqual(self.yeniden_ac().ayarlar_oku(), saved)
        again = self.alan.ayarlar_kaydet(settings, expected_version=1)
        self.assertEqual(again, saved)
        with self.assertRaises(SurumCatismasi):
            self.alan.ayarlar_kaydet(settings, expected_version=0)

    def test_iki_pencere_ayari_birbirini_ezmez(self):
        def change(limit):
            try:
                return self.alan.ayarlar_kaydet(self.ayar(daily_limit=limit), expected_version=0)["version"]
            except SurumCatismasi:
                return "cakisma"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, [4, 20]))
        self.assertCountEqual(results, [1, "cakisma"])
        self.assertEqual(self.alan.ayarlar_oku()["version"], 1)

    def test_ayar_sinir_ve_tur_hatalari_eski_ayari_korur(self):
        invalid = [None, [], {}, {**self.ayar(), "fazla": True}]
        invalid.extend(self.ayar(daily_limit=value) for value in (3, 21, True, 8.0, "8", None))
        invalid.extend(self.ayar(keywords=value) for value in ("sera", ["x"] * 21, [1], [""], ["  "], ["x" * 101], ["x\x00y"]))
        invalid.extend(self.ayar(muted_keywords=value) for value in ([None], ["x"] * 21, ["a\nb"]))
        invalid.extend(self.ayar(topic_weights=value) for value in ({"ai": 3}, [],
                       {**varsayilan_ayarlar()["topic_weights"], "fazla": 2}))
        for value in (-1, 6, True, 3.0, "3", None):
            invalid.append(self.ayar(topic_weights={**varsayilan_ayarlar()["topic_weights"], "ai": value}))
        before = self.alan.ayarlar_oku()
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.alan.ayarlar_kaydet(value, expected_version=0)
        for version in (True, -1, 1.0, "0", None, 2**63):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.alan.ayarlar_kaydet(self.ayar(), expected_version=version)
        self.assertEqual(self.alan.ayarlar_oku(), before)

    def test_okundu_geri_alma_kalicilik_ve_diger_tercihler(self):
        item_id = self.ids[0]
        self.depo.geri_bildirim(item_id, "yararli")
        marked = self.alan.okundu_yaz(item_id, read=True)
        self.assertTrue(marked["read"])
        self.assertEqual(self.yeniden_ac().snapshot()["stats"]["read_count"], 1)
        self.assertEqual(self.alan.okundu_yaz(item_id, read=True), marked)
        self.alan.okundu_yaz(item_id, read=False)
        snapshot = self.yeniden_ac().snapshot()
        self.assertFalse(snapshot["read_states"][0]["read"])
        self.assertEqual(snapshot["stats"]["unread_count"], 4)
        item = next(item for item in self.depo.gorunum()["items"] if item["id"] == item_id)
        self.assertEqual(item["feedback"], "yararli")

    def test_toplu_okundu_tekrarlari_birlestirir_eksikte_tamami_geri_alinir(self):
        result = self.alan.toplu_okundu_yaz([self.ids[1], self.ids[0], self.ids[1]], read=True)
        self.assertEqual([row["item_id"] for row in result], [self.ids[1], self.ids[0]])
        before = self.alan.snapshot()
        with self.assertRaises(KeyError):
            self.alan.toplu_okundu_yaz([*self.ids, "olmayan"], read=False)
        self.assertEqual(self.alan.snapshot(), before)

    def test_toplu_okuma_kapsami_acik_ve_en_fazla_yuz(self):
        for values in ([], "all", None, [self.ids[0]] * 101, [" "], [True], ["x" * 129]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.alan.toplu_okundu_yaz(values, read=True)
        for value in ("false", 1, None, [], 0):
            with self.subTest(read=value), self.assertRaises(ValueError):
                self.alan.okundu_yaz(self.ids[0], read=value)
        self.assertEqual(self.alan.snapshot()["read_states"], [])
        self.depo.kaydet("ai_kaynak", [
            {"external_id": f"bulk-{index}", "url": f"https://example.org/bulk/{index}", "title": f"Toplu haber {index}"}
            for index in range(100)])
        all_ids = [item["id"] for item in self.depo.gorunum()["items"]]
        self.assertEqual(len(self.alan.toplu_okundu_yaz(all_ids[:100], read=True)), 100)
        self.assertEqual(self.alan.snapshot()["stats"]["read_count"], 100)
        self.assertEqual(self.alan.snapshot()["stats"]["unread_count"], 4)

    def test_koleksiyon_uyeleri_arsiv_ve_yeniden_acma_kalici(self):
        created = self.alan.koleksiyon_olustur(name=" Sera  planı ", description="Kontrollü üretim\nİlk denemeler")
        self.assertEqual(created["name"], "Sera planı")
        self.assertEqual(created["version"], 1)
        added = self.alan.koleksiyona_ekle(created["id"], self.ids[:2], expected_version=1)
        self.assertEqual(added["item_count"], 2)
        self.assertEqual(added["version"], 2)
        archived = self.alan.koleksiyon_arsivle(created["id"], archived=True, expected_version=2)
        self.assertTrue(archived["archived"])
        self.assertEqual(self.yeniden_ac().koleksiyonlar(), [])
        self.assertEqual(self.alan.koleksiyonlar(include_archived=True), [archived])
        with self.assertRaises(ValueError):
            self.alan.koleksiyona_ekle(created["id"], self.ids[2:], expected_version=3)
        restored = self.alan.koleksiyon_arsivle(created["id"], archived=False, expected_version=3)
        self.assertEqual(set(restored["item_ids"]), set(self.ids[:2]))
        removed = self.alan.koleksiyondan_cikar(created["id"], [self.ids[0]], expected_version=4)
        self.assertEqual(removed["item_ids"], [self.ids[1]])
        self.assertEqual(len(self.depo.gorunum()["items"]), 4)
        self.assertEqual(self.yeniden_ac().koleksiyonlar(), [removed])

    def test_koleksiyon_adi_turkce_ve_unicode_ile_benzersiz(self):
        for original, duplicate in (("IŞIK", "ışık"), ("İklim", "iklim"), ("Cafe\u0301", "Café")):
            created = self.alan.koleksiyon_olustur(name=original)
            with self.subTest(original=original), self.assertRaises(ValueError):
                self.alan.koleksiyon_olustur(name=duplicate)
            self.alan.koleksiyon_arsivle(created["id"], archived=True, expected_version=1)
            with self.assertRaises(ValueError):
                self.alan.koleksiyon_olustur(name=duplicate)
        self.assertEqual(len(self.alan.koleksiyonlar(include_archived=True)), 3)

    def test_eszamanli_ayni_koleksiyon_adi_tek_kayit_olur(self):
        def create(name):
            try:
                return self.alan.koleksiyon_olustur(name=name)["version"]
            except ValueError:
                return "ayni_ad"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ["İKLİM", "iklim"]))
        self.assertCountEqual(results, [1, "ayni_ad"])
        self.assertEqual(len(self.alan.koleksiyonlar()), 1)

    def test_koleksiyon_duzenleme_ve_uyelik_tekrari_surumu_gereksiz_artirmaz(self):
        collection = self.alan.koleksiyon_olustur(name="İlk")
        identifier = collection["id"]
        edited = self.alan.koleksiyon_duzenle(identifier, name="İkinci", description="Not", expected_version=1)
        self.assertEqual(edited["version"], 2)
        self.assertEqual(self.alan.koleksiyon_duzenle(identifier, name="İkinci", description="Not", expected_version=2), edited)
        added = self.alan.koleksiyona_ekle(identifier, [self.ids[0], self.ids[0]], expected_version=2)
        self.assertEqual(added["version"], 3)
        self.assertEqual(self.alan.koleksiyona_ekle(identifier, [self.ids[0]], expected_version=3), added)
        self.assertEqual(self.alan.koleksiyondan_cikar(identifier, [self.ids[1]], expected_version=3), added)
        self.assertEqual(self.alan.koleksiyon_arsivle(identifier, archived=False, expected_version=3), added)

    def test_koleksiyon_hatalari_kismen_yazmaz(self):
        collection = self.alan.koleksiyon_olustur(name="Plan")
        other = self.alan.koleksiyon_olustur(name="Diğer")
        identifier = collection["id"]
        with self.assertRaises(KeyError):
            self.alan.koleksiyona_ekle(identifier, [self.ids[0], "olmayan"], expected_version=1)
        with self.assertRaises(ValueError):
            self.alan.koleksiyon_duzenle(identifier, name=other["name"], description="Değişmemeli", expected_version=1)
        self.assertEqual(next(row for row in self.alan.koleksiyonlar() if row["id"] == identifier), collection)
        self.alan.koleksiyona_ekle(identifier, [self.ids[0]], expected_version=1)
        for change in (
                lambda: self.alan.koleksiyon_duzenle(identifier, name="Yeni", description="", expected_version=1),
                lambda: self.alan.koleksiyona_ekle(identifier, [self.ids[1]], expected_version=1),
                lambda: self.alan.koleksiyondan_cikar(identifier, [self.ids[0]], expected_version=1),
                lambda: self.alan.koleksiyon_arsivle(identifier, archived=True, expected_version=1)):
            with self.assertRaises(SurumCatismasi):
                change()
        with self.assertRaises(KeyError):
            self.alan.koleksiyondan_cikar(identifier, [self.ids[0], "olmayan"], expected_version=2)
        self.assertEqual(next(row for row in self.alan.koleksiyonlar() if row["id"] == identifier)["item_count"], 1)

    def test_koleksiyon_sinirlari_ve_bulunmayan_kimlikler(self):
        for name in ("", " ", 3, "x" * 61, "bir\nikı", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.alan.koleksiyon_olustur(name=name)
        for description in (None, "x" * 301, "a\x00b"):
            with self.subTest(description=description), self.assertRaises(ValueError):
                self.alan.koleksiyon_olustur(name="Geçersiz", description=description)
        collection = self.alan.koleksiyon_olustur(name="x" * 60, description="a" * 300)
        for values in ([], [self.ids[0]] * 101, "all"):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.alan.koleksiyona_ekle(collection["id"], values, expected_version=1)
        with self.assertRaises(ValueError):
            self.alan.koleksiyon_arsivle(collection["id"], archived="false", expected_version=1)
        with self.assertRaises(KeyError):
            self.alan.koleksiyon_duzenle("olmayan", name="X", description="", expected_version=1)
        with self.assertRaises(KeyError):
            self.alan.koleksiyona_ekle("olmayan", self.ids, expected_version=1)
        self.assertEqual(len(self.alan.koleksiyonlar()), 1)

    def test_eszamanli_koleksiyon_uyeligi_surumu_korur(self):
        identifier = self.alan.koleksiyon_olustur(name="Yarış")["id"]
        def add(item_id):
            try:
                return self.alan.koleksiyona_ekle(identifier, [item_id], expected_version=1)["version"]
            except SurumCatismasi:
                return "cakisma"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add, self.ids[:2]))
        self.assertCountEqual(results, [2, "cakisma"])
        self.assertEqual(self.alan.koleksiyonlar()[0]["item_count"], 1)

    def test_kayitli_arama_kalici_arsivlenir_ve_geri_acilir(self):
        search = self.alan.arama_kaydet(name="Sera", query=" sulama ", topic="tarim", source_id="sera_kaynak")
        self.assertEqual(search["query"], "sulama")
        self.assertEqual(self.yeniden_ac().aramalar(), [search])
        removed = self.alan.arama_sil(search["id"], expected_version=1)
        self.assertTrue(removed["archived"])
        self.assertEqual(removed["version"], 2)
        self.assertEqual(self.alan.aramalar(), [])
        self.assertEqual(self.alan.aramalar(include_archived=True), [removed])
        self.assertEqual(self.alan.arama_sil(search["id"], expected_version=2), removed)
        with self.assertRaises(SurumCatismasi):
            self.alan.arama_sil(search["id"], expected_version=1)
        restored = self.alan.arama_arsivle(search["id"], archived=False, expected_version=2)
        self.assertFalse(restored["archived"])
        self.assertEqual(self.yeniden_ac().aramalar(), [restored])
        self.assertEqual(self.alan.arama_kaydet(name="Hepsi", topic="all", source_id="")["topic"], None)

    def test_kayitli_arama_hatalari_ve_sorgu_veri_olarak_korunur(self):
        for changes in ({"name": "x" * 61}, {"query": "x" * 201}, {"query": None}, {"topic": "yok"},
                        {"topic": []}, {"source_id": []}, {"source_id": "x" * 65}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.alan.arama_kaydet(**{"name": "Arama", **changes})
        with self.assertRaises(KeyError):
            self.alan.arama_kaydet(name="Arama", source_id="olmayan")
        with self.assertRaises(KeyError):
            self.alan.arama_sil("olmayan", expected_version=1)
        self.assertEqual(self.alan.aramalar(), [])
        query = "%' OR 1=1; DROP TABLE items; --"
        saved = self.alan.arama_kaydet(name="Düz metin", query=query)
        self.assertEqual(saved["query"], query)
        self.assertEqual(len(self.depo.gorunum()["items"]), 4)

    def test_kayitli_arama_butun_filtreleri_ve_arsivli_koleksiyonu_korur(self):
        collection = self.alan.koleksiyon_olustur(name="Arşivli araştırma")
        self.alan.koleksiyon_arsivle(collection["id"], archived=True, expected_version=1)
        saved = self.alan.arama_kaydet(name="Tam arama", query="sera", topic="tarim", source_id="sera_kaynak",
                                     collection_id=collection["id"], read_status="unread", sort="oldest")
        self.assertEqual(saved["collection_id"], collection["id"])
        self.assertEqual(saved["read_status"], "unread")
        self.assertEqual(saved["sort"], "oldest")
        self.assertEqual(self.yeniden_ac().snapshot()["saved_searches"], [saved])
        defaults = self.alan.arama_kaydet(name="Eski istemci", collection_id="")
        self.assertIsNone(defaults["collection_id"])
        self.assertEqual(defaults["read_status"], "all")
        self.assertEqual(defaults["sort"], "relevance")
        for changes in ({"read_status": None}, {"read_status": "bad"}, {"sort": []}, {"sort": "bad"},
                        {"collection_id": []}, {"collection_id": "x" * 129}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.alan.arama_kaydet(name="Geçersiz", **changes)
        with self.assertRaises(KeyError):
            self.alan.arama_kaydet(name="Eksik koleksiyon", collection_id="olmayan")
        with self.depo.baglanti(write=True) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE saved_searches SET collection_id='olmayan' WHERE id=?", (saved["id"],))

    def test_eski_arama_semasi_veri_kaybetmeden_tekrar_acilir(self):
        with self.depo.baglanti(write=True) as connection:
            connection.execute("DROP TABLE saved_searches")
            connection.execute("CREATE TABLE saved_searches (id TEXT PRIMARY KEY,name TEXT NOT NULL,query TEXT NOT NULL,"
                               "topic TEXT,source_id TEXT REFERENCES sources(id),archived INTEGER NOT NULL DEFAULT 0,"
                               "version INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
            connection.execute("INSERT INTO saved_searches VALUES('old','Eski arama','ışık','tarim','sera_kaynak',1,3,'once','sonra')")
        reopened = self.yeniden_ac()
        saved = reopened.snapshot()["saved_searches"][0]
        self.assertEqual(saved["id"], "old")
        self.assertEqual(saved["query"], "ışık")
        self.assertTrue(saved["archived"])
        self.assertEqual(saved["version"], 3)
        self.assertIsNone(saved["collection_id"])
        self.assertEqual(saved["read_status"], "all")
        self.assertEqual(saved["sort"], "relevance")
        self.assertEqual(self.yeniden_ac().snapshot()["saved_searches"], [saved])
        with self.depo.baglanti() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            foreign_keys = connection.execute("PRAGMA foreign_key_list(saved_searches)").fetchall()
            self.assertTrue(any(row[2] == "user_collections" and row[3] == "collection_id" for row in foreign_keys))

    def test_snapshot_tum_kisisel_veriyi_ve_arsivi_json_olarak_korur(self):
        self.alan.ayarlar_kaydet(self.ayar(keywords=["sera"]), expected_version=0)
        self.alan.okundu_yaz(self.ids[0], read=True)
        collection = self.alan.koleksiyon_olustur(name="Özel plan")
        self.alan.koleksiyona_ekle(collection["id"], self.ids[:2], expected_version=1)
        self.alan.koleksiyon_arsivle(collection["id"], archived=True, expected_version=2)
        search = self.alan.arama_kaydet(name="Geçmiş arama", query="ışık")
        self.alan.arama_sil(search["id"], expected_version=1)
        snapshot = self.alan.snapshot()
        self.assertEqual(json.loads(json.dumps(snapshot, ensure_ascii=False)), snapshot)
        self.assertEqual(snapshot, self.yeniden_ac().snapshot())
        self.assertEqual(len(snapshot["collections"]), 1)
        self.assertEqual(len(snapshot["saved_searches"]), 1)
        self.assertEqual(snapshot["stats"]["collection_count"], 0)
        self.assertEqual(snapshot["stats"]["saved_search_count"], 0)
        self.assertEqual(snapshot["ranking"]["method"], "rules")

    def test_yabanci_anahtarlar_kirlamaz_ve_sema_kurulumu_islemi_bitirmez(self):
        with self.depo.baglanti(write=True) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO item_read_states VALUES('yok',1,'2026-01-01')")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO collection_items VALUES('yok','yok','2026-01-01')")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(RuntimeError):
            with self.depo.baglanti(write=True) as connection:
                connection.execute("UPDATE user_settings SET version=999 WHERE id=1")
                tablolari_hazirla(connection)
                raise RuntimeError("çağıranın işlemi geri alınacak")
        self.assertEqual(self.alan.ayarlar_oku()["version"], 0)


class KisiselSiralamaTestleri(unittest.TestCase):
    def test_varsayilan_puan_nortur_ve_giris_degismez(self):
        items = [{"id": "a", "topic": "ai", "score": 60}, {"id": "b", "topic": "tarim", "score": 50}]
        before = copy.deepcopy(items)
        result = kisisel_sirala(items)
        self.assertEqual([item["personal_score"] for item in result], [60, 50])
        self.assertEqual(items, before)
        self.assertIn("yapay zekâ değerlendirmesi değildir", result[0]["personal_rank"]["label"])

    def test_konu_agirligi_ve_ilgi_kelimeleri_siralamayi_aciklar(self):
        settings = varsayilan_ayarlar()
        settings["topic_weights"]["tarim"] = 5
        settings["keywords"] = ["İKLİM", "ışık", "sulama"]
        items = [{"id": "a", "topic": "ai", "score": 80, "title": "Model sürümü"},
                 {"id": "b", "topic": "tarim", "score": 50, "title": "iklim ve IŞIK", "summary": "Sulama ölçümü"}]
        result = kisisel_sirala(items, settings)
        self.assertEqual(result[0]["id"], "b")
        self.assertEqual(result[0]["personal_score"], 104)
        self.assertEqual(result[0]["personal_rank"]["keyword_matches"], settings["keywords"])

    def test_sessiz_kelime_onceliklidir_ve_gunluk_sinir_buradan_kesilmez(self):
        settings = varsayilan_ayarlar()
        settings.update(keywords=["sera"], muted_keywords=["sponsor"], daily_limit=4)
        items = [{"id": str(index), "score": 60, "topic": "tarim", "title": "Sera araştırması"}
                 for index in range(6)]
        items.append({"id": "sessiz", "score": 100, "topic": "tarim", "title": "Sponsor sera haberi"})
        ranked = kisisel_sirala(items, settings)
        self.assertEqual(len(ranked), 6)
        self.assertEqual([item["id"] for item in ranked], [str(index) for index in range(6)])
        all_items = kisisel_sirala(items, settings, include_muted=True)
        self.assertEqual(all_items[-1]["id"], "sessiz")
        self.assertTrue(all_items[-1]["personal_rank"]["muted"])

    def test_anahtar_sozcukler_literal_ingilizce_ve_turkce_eslesir(self):
        settings = varsayilan_ayarlar()
        settings["keywords"] = ["irrigation", "ışık", "[cuda]", "a+b"]
        rank = kisisel_puan({"score": 50, "title": "Irrigation IŞIK [CUDA] A+B"}, settings)
        self.assertEqual(rank["keyword_matches"], settings["keywords"])
        self.assertEqual(rank["score"], 82)

    def test_eslesme_etkisi_sinirli_ve_sayisal_girdiler_guvenli(self):
        settings = varsayilan_ayarlar()
        settings["keywords"] = [f"kelime{index}" for index in range(20)]
        rank = kisisel_puan({"score": 50, "title": " ".join(settings["keywords"])}, settings)
        self.assertEqual(rank["score"], 90)
        for value, expected in ((float('nan'), 0), (float('inf'), 0), (True, 0), ("80", 0), (-100, 0), (2**4096, 100)):
            with self.subTest(value=type(value)):
                self.assertEqual(kisisel_puan({"score": value})["score"], expected)

    def test_siralama_gecersiz_girdi_ve_ayar_metadata_destegi(self):
        for items in (None, "a", [{} , None]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                kisisel_sirala(items)
        with self.assertRaises(ValueError):
            kisisel_puan(None)
        with self.assertRaises(ValueError):
            kisisel_sirala([], include_muted="false")
        settings = {**varsayilan_ayarlar(), "version": 2, "updated_at": "2026-10-26"}
        self.assertEqual(kisisel_puan({"score": 60}, settings)["score"], 60)


if __name__ == "__main__":
    unittest.main()

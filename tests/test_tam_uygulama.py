"""Kişisel alan, kaynak yönetimi, okuma ve yapay zekâ işlerini geçici gerçek depoda uçtan uca doğrular."""

import json
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from fastapi.testclient import TestClient

from radar.depo import Depo, simdi, tarih
from radar.marka import VERSION
from radar.okuma import OkumaIsleri, OkumaMesgul, OkumaSiniri, OkumaHazirDegil
from radar.sunucu import uygulama
from radar.toplayici import FetchResult
from radar.zeka import ZekaMesgul, ZekaSiniri, ZekaHazirDegil


ADRES = "http://127.0.0.1:8781"
METIN = ("Local voice agents use memory models for reliable speech processing. "
         "The published article explains how sensors and local computers work together. "
         "The source describes a practical method and states its current limitations. "
         "Independent testing is still needed before choosing hardware for a greenhouse.")


def kaynak(identifier="ai_source", topic="ai"):
    return {"id": identifier, "name": identifier, "topic": topic, "publisher": identifier,
            "url": "https://example.org/", "fetch_url": "https://example.org/feed.xml",
            "kind": "rss", "interval_hours": 24, "benefit": "Kaynak metnini incele.", "enabled": True,
            "fetch_hosts": ["example.org"], "item_hosts": ["example.org"]}


def haber(number, **changes):
    return {"external_id": str(number), "url": f"https://example.org/article-{number}",
            "title": f"Local voice agents and memory models {number}", "excerpt": METIN,
            "language": "en", "evidence_type": "duyuru", **changes}


def model_yaniti(messages, **kwargs):
    sources = json.loads(messages[1]["content"])["sources"]
    return json.dumps({"title_tr": "Yerel ses ajanları için yöntemler",
                       "summary_tr": "Kaynak metni yerel ses ajanlarının bellek modellerini ve sensörlerle çalışmasını anlatıyor.",
                       "benefit_tr": "Sera için yerel donanım seçeneklerini değerlendirmeye yardımcı olabilir.",
                       "limitations": ["Bağımsız donanım testi yapılmadı."],
                       "next_steps": ["Özgün yöntem ve donanım gereksinimlerini inceleyin."],
                       "evidence": [{"item_id": row["item_id"], "quote": " ".join(row["public_text"].split())[:100]}
                                    for row in sources]}, ensure_ascii=False)


class TamUygulamaTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "test.sqlite3"
        self.collector = Mock(return_value={"items": [], "etag": None, "modified": None, "not_modified": False})
        self.fetcher = Mock(side_effect=lambda url, source: FetchResult(
            200, f"<article><h1>Local voice agents and memory models</h1><p>{METIN}</p></article>".encode(),
            {"content-type": "text/html; charset=utf-8"}, url))
        self.provider = Mock(side_effect=model_yaniti)
        self.credentials = Mock(return_value="LOCAL-FAKE-TEST-KEY")
        self.open()
        self.addCleanup(self.close)
        self.raw = haber(1)
        self.app.state.depo.kaydet("ai_source", [self.raw, haber(2)])
        self.app.state.depo.kaydet("farm_source", [haber(3, title="Greenhouse irrigation sensors")])
        self.ids = {item["url"].rsplit("-", 1)[-1]: item["id"] for item in self.state()["items"]}

    def open(self):
        self.app = uygulama(self.path, sources=[kaynak(), kaynak("farm_source", "tarim")], seed=False,
                            collector=self.collector, article_fetcher=self.fetcher, ai_provider=self.provider,
                            credential_provider=self.credentials)
        self.client = TestClient(self.app, base_url=ADRES)
        self.client.__enter__()
        self.headers = {"X-Radar-CSRF": self.state()["csrf_token"], "Origin": ADRES}

    def close(self):
        if self.client is not None:
            self.client.__exit__(None, None, None)
            self.client = None

    def state(self):
        response = self.client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def post(self, path, value=None):
        return self.client.post(path, json={} if value is None else value, headers=self.headers)

    def settings(self, **changes):
        current = self.client.get("/api/personal").json()["settings"]
        return {key: current[key] for key in ("topic_weights", "daily_limit", "keywords", "muted_keywords")} | {
            "expected_version": current["version"], **changes}

    def finish(self, worker):
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())

    def test_acilis_ag_ve_model_cagirmaz_hizmetleri_durustce_bildirir(self):
        state = self.state()
        self.assertFalse(state["reading"]["running"])
        self.assertFalse(state["ai"]["running"])
        self.assertTrue(state["ai"]["configured"])
        self.assertTrue(state["settings"]["manual_ai"])
        self.assertIsNone(state["settings"]["paid_models"])
        self.assertEqual(self.client.get("/api/health").json()["version"], VERSION)
        for operation in (self.provider, self.fetcher, self.collector):
            operation.assert_not_called()

    def test_ayar_surum_cakismasi_siralama_ve_kalici_yeniden_acilis(self):
        payload = self.settings(daily_limit=13, topic_weights={"ai": 0, "jarvis": 3, "medya": 3, "robotik": 3, "tarim": 5},
                                keywords=["irrigation"], muted_keywords=["memory"])
        saved = self.post("/api/personal/settings", payload)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.post("/api/personal/settings", {**payload, "daily_limit": 14}).status_code, 409)
        self.assertEqual(self.state()["settings"]["daily_card_limit"], 13)
        state_cards = self.state()["items"]
        page = self.client.get("/api/items?sort=relevance").json()
        self.assertEqual(page["items"][0]["id"], self.ids["3"])
        self.assertEqual(state_cards[0]["id"], self.ids["3"])
        self.assertTrue(next(item for item in page["items"] if item["id"] == self.ids["1"])["personal_rank"]["muted"])
        self.assertEqual({item["id"]: item["personal_score"] for item in state_cards},
                         {item["id"]: item["personal_score"] for item in page["items"]})
        self.close()
        self.open()
        self.assertEqual(self.client.get("/api/personal").json()["settings"]["daily_limit"], 13)

    def test_ayar_tipleri_ve_uzun_izinli_anahtar_listesi(self):
        for change in ({"daily_limit": True}, {"daily_limit": "8"}, {"topic_weights": {"ai": True}},
                       {"keywords": ["x"] * 21}, {"keywords": ["x" * 101]}, {"unknown": "value"}):
            with self.subTest(change=change):
                self.assertEqual(self.post("/api/personal/settings", self.settings(**change)).status_code, 422)
        # Türkçe JSON gövdesi eski 4 KiB sınırından uzundur; alan sınırları korunur.
        valid = self.settings(keywords=[f"ilgi{number}" + "ş" * 90 for number in range(20)],
                              muted_keywords=[f"sessiz{number}" + "ğ" * 88 for number in range(20)])
        self.assertEqual(self.post("/api/personal/settings", valid).status_code, 200)

    def test_kisisel_oncelik_500_sinirindan_once_butun_havuza_uygulanir(self):
        self.app.state.depo.kaydet("ai_source", [haber(number) for number in range(10, 520)])
        with self.app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET score=100,is_archive=0 WHERE topic='ai'")
            connection.execute("UPDATE items SET score=90,is_archive=1 WHERE topic='tarim'")
        payload = self.settings(topic_weights={"ai": 0, "jarvis": 3, "medya": 3, "robotik": 3, "tarim": 5})
        self.assertEqual(self.post("/api/personal/settings", payload).status_code, 200)
        state = self.state()
        self.assertEqual(len(state["items"]), 500)
        self.assertEqual(state["items"][0]["id"], self.ids["3"])
        self.assertEqual(self.client.get("/api/items?limit=1").json()["items"][0]["id"], self.ids["3"])

    def test_kisisel_puan_esitliginde_ana_gorunum_ve_sayfalar_ayni_siradadir(self):
        with self.app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE items SET score=80,published_at='2026-10-20T00:00:00+00:00' WHERE topic='ai'")
            connection.execute("UPDATE items SET score=20,published_at='2026-10-21T00:00:00+00:00' WHERE topic='tarim'")
        payload = self.settings(topic_weights={"ai": 1, "jarvis": 3, "medya": 3, "robotik": 3, "tarim": 5})
        self.assertEqual(self.post("/api/personal/settings", payload).status_code, 200)
        state = self.state()["items"]
        page = self.client.get("/api/items").json()["items"]
        self.assertEqual([item["personal_score"] for item in state], [50, 50, 50])
        self.assertEqual(state[0]["id"], self.ids["3"])
        self.assertEqual([item["id"] for item in state], [item["id"] for item in page])

    def test_toplu_okundu_tamami_dogrulanir_ve_filtreler_tutarlidir(self):
        first = self.ids["1"]
        self.assertEqual(self.post("/api/read/batch", {"item_ids": [first, "missing"], "read": True}).status_code, 404)
        self.assertEqual(self.client.get("/api/personal").json()["read_states"], [])
        response = self.post(f"/api/items/{first}/read", {"read": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["read"])
        read = self.client.get("/api/items?read_status=read").json()
        self.assertEqual(read["total"], 1)
        self.assertEqual(read["items"][0]["id"], first)
        self.assertTrue(read["items"][0]["is_read"])
        self.assertEqual(self.client.get("/api/items?read_status=unread").json()["total"], 2)
        self.assertEqual(self.post("/api/read/batch", {"item_ids": list(self.ids.values()), "read": False}).status_code, 200)
        self.assertEqual(self.client.get("/api/items?read_status=unread").json()["total"], 3)
        self.assertEqual(self.post(f"/api/items/{first}/read", {"read": "true"}).status_code, 422)

    def test_koleksiyon_surumu_uyelik_arsiv_geri_alma_ve_sayfalar(self):
        response = self.post("/api/collections", {"name": "Sera", "description": "Sensör araştırması"})
        self.assertEqual(response.status_code, 201)
        group = response.json()
        url = f"/api/collections/{group['id']}"
        self.assertEqual(self.post(url + "/items", {"item_ids": list(self.ids.values()), "expected_version": 999}).status_code, 409)
        group = self.post(url + "/items", {"item_ids": list(self.ids.values()), "expected_version": group["version"]}).json()
        page = self.client.get("/api/items", params={"collection_id": group["id"], "limit": 2}).json()
        second = self.client.get("/api/items", params={"collection_id": group["id"], "limit": 2, "offset": 2}).json()
        self.assertEqual(page["total"], 3)
        self.assertEqual(len({item["id"] for item in page["items"] + second["items"]}), 3)
        group = self.post(url, {"name": "Sera planı", "description": "Yeni açıklama", "expected_version": group["version"]}).json()
        group = self.post(url + "/archive", {"archived": True, "expected_version": group["version"]}).json()
        self.assertTrue(group["archived"])
        group = self.post(url + "/archive", {"archived": False, "expected_version": group["version"]}).json()
        group = self.post(url + "/items", {"item_ids": [self.ids["1"]], "remove": True, "expected_version": group["version"]}).json()
        self.assertEqual(group["item_count"], 2)
        self.assertEqual(self.client.get("/api/items?collection_id=missing").status_code, 404)
        self.assertEqual(self.client.get("/api/items", params={"collection_id": "' OR 1=1 --"}).status_code, 404)

    def test_kayitli_arama_suzgeci_ve_arsiv_kaybolmaz(self):
        response = self.post("/api/searches", {"name": "Yerel ses", "query": "voice", "topic": "ai", "source_id": "ai_source"})
        self.assertEqual(response.status_code, 201)
        search = response.json()
        path = f"/api/searches/{search['id']}/archive"
        search = self.post(path, {"archived": True, "expected_version": search["version"]}).json()
        self.assertTrue(search["archived"])
        self.assertEqual(self.post(path, {"archived": False, "expected_version": 1}).status_code, 409)
        self.assertEqual(self.post(path, {"archived": False, "expected_version": search["version"]}).status_code, 200)
        self.assertEqual(self.client.get("/api/personal").json()["saved_searches"][0]["query"], "voice")
        self.assertEqual(self.post("/api/searches", {"name": "Geçersiz", "source_id": "missing"}).status_code, 404)

    def test_tarih_siralamasinda_bilinmeyen_tarih_sona_kalir(self):
        self.app.state.depo.kaydet("ai_source", [{**self.raw, "published_at": "2026-10-20T12:00:00+00:00"},
                                                haber(2, published_at="2026-10-22T12:00:00+00:00")])
        for sort, expected in (("newest", ["2", "1", "3"]), ("oldest", ["1", "2", "3"])):
            page = self.client.get("/api/items", params={"sort": sort}).json()
            self.assertEqual([item["id"] for item in page["items"]], [self.ids[key] for key in expected])
        self.assertEqual(self.client.get("/api/items?sort=drop-table").status_code, 422)
        self.assertEqual(self.client.get("/api/items?read_status=anything").status_code, 422)

    def test_kayitli_aramanin_tum_filtreleri_api_uzerinden_ayni_sonucu_verir(self):
        group = self.post("/api/collections", {"name": "Tam filtre"}).json()
        group = self.post(f"/api/collections/{group['id']}/items",
                          {"item_ids": list(self.ids.values()), "expected_version": group["version"]}).json()
        self.post(f"/api/collections/{group['id']}/archive", {"archived": True, "expected_version": group["version"]})
        self.post(f"/api/items/{self.ids['1']}/read", {"read": True})
        response = self.post("/api/searches", {"name": "Tam görünüm", "query": "voice", "topic": "ai",
                             "source_id": "ai_source", "collection_id": group["id"], "read_status": "unread", "sort": "oldest"})
        self.assertEqual(response.status_code, 201)
        saved = response.json()
        self.assertEqual(self.client.get("/api/personal").json()["saved_searches"], [saved])
        result = self.client.get("/api/items", params={"query": saved["query"], "topic": saved["topic"],
                                "source": saved["source_id"], "collection_id": saved["collection_id"],
                                "read_status": saved["read_status"], "sort": saved["sort"]}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], self.ids["2"])
        self.assertEqual(self.post("/api/searches", {"name": "Hata", "collection_id": "missing"}).status_code, 404)
        self.assertEqual(self.post("/api/searches", {"name": "Hata", "read_status": "missing"}).status_code, 422)
        self.assertEqual(self.post("/api/searches", {"name": "Hata", "sort": "missing"}).status_code, 422)

    def test_ozel_kaynak_dogrulama_host_yetkisi_ve_tercih_kalici(self):
        payload = {"name": "Özel araştırma", "fetch_url": "https://updates.example.org/feed.xml", "kind": "rss",
                   "topic": "robotik", "publisher": "Örnek kurum", "branches": ["Sensörler"],
                   "interval_hours": 48, "language": "tr", "source_type": "Kurum"}
        with patch("radar.sunucu.kaynaklari_yetkilendir") as authorize:
            response = self.post("/api/sources", payload)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(authorize.call_args.args[0][0]["fetch_hosts"], ["updates.example.org"])
        source = response.json()
        url = f"/api/sources/{source['id']}"
        saved = self.post(url, {"enabled": False, "interval_hours": 72, "expected_version": 0})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.post(url, {"enabled": True, "interval_hours": 72, "expected_version": 0}).status_code, 409)
        self.assertFalse(self.client.get(url).json()["enabled"])
        self.assertEqual(self.post("/api/sources", payload).status_code, 422)
        self.assertEqual(self.post("/api/sources", {**payload, "fetch_url": "http://127.0.0.1/private"}).status_code, 422)
        self.close()
        with patch("radar.sunucu.kaynaklari_yetkilendir") as authorize:
            self.open()
            self.assertIn(source["id"], [row["id"] for row in authorize.call_args.args[0]])
        self.assertFalse(self.client.get(url).json()["enabled"])
        self.assertEqual(self.client.get("/api/sources/export").status_code, 200)
        self.collector.assert_not_called()

    def fail_source(self, source_id="ai_source", *, recent=False):
        checked = simdi() if recent else (tarih(simdi()) - timedelta(hours=1)).isoformat()
        future = (tarih(simdi()) + timedelta(days=3)).isoformat()
        with self.app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET failures=3,last_error='deneme',last_checked_at=?,next_due_at=? WHERE id=?",
                               (checked, future, source_id))

    def test_yeniden_deneme_bekleyen_bilet_tektir_ve_uc_hata_engelini_bir_kez_gecer(self):
        self.fail_source()
        with patch.object(self.app.state.collection, "baslat", return_value=False):
            response = self.post("/api/sources/ai_source/retry", {"expected_version": 0})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()["queued"])
        source = response.json()["source"]
        again = self.post("/api/sources/ai_source/retry", {"expected_version": source["preference_version"]})
        self.assertEqual(again.status_code, 429)
        self.assertIn("Retry-After", again.headers)
        self.assertTrue(self.app.state.collection.calistir("ai_source"))
        self.assertEqual(self.collector.call_count, 1)
        source = self.client.get("/api/sources/ai_source").json()
        self.assertFalse(source["retry_pending"])
        self.assertEqual(source["failures"], 0)
        self.app.state.collection.calistir("ai_source")
        self.assertEqual(self.collector.call_count, 1)

    def test_yeniden_deneme_bekleme_ve_kapatma_sinirlarini_asmaz(self):
        self.fail_source(recent=True)
        response = self.post("/api/sources/ai_source/retry", {"expected_version": 0})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers["Retry-After"]), 0)
        source = self.post("/api/sources/ai_source", {"enabled": False, "interval_hours": 24, "expected_version": 0}).json()
        self.assertEqual(self.post("/api/sources/ai_source/retry", {"expected_version": source["preference_version"]}).status_code, 422)
        self.app.state.collection.calistir("ai_source")
        self.collector.assert_not_called()

    def test_basarisiz_yeniden_deneme_sonraki_turlara_yetki_birakmaz(self):
        self.fail_source()
        self.collector.side_effect = ValueError("Kaynak hâlâ erişilemiyor.")
        with patch.object(self.app.state.collection, "baslat", return_value=False):
            self.assertEqual(self.post("/api/sources/ai_source/retry", {"expected_version": 0}).status_code, 202)
        self.app.state.collection.calistir("ai_source")
        self.assertEqual(self.collector.call_count, 1)
        with self.app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE sources SET next_due_at=NULL WHERE id='ai_source'")
        self.app.state.collection.calistir("ai_source")
        self.assertEqual(self.collector.call_count, 1)
        source = self.client.get("/api/sources/ai_source").json()
        self.assertFalse(source["retry_pending"])
        self.assertEqual(source["failures"], 4)

    def test_tarama_sirasinda_degisen_kaynak_kapatmasi_sonraki_istegi_engeller(self):
        def collect(source, **kwargs):
            self.app.state.source_manager.kaydet("farm_source", enabled=False, interval_hours=24, expected_version=0)
            return {"items": [], "not_modified": False}
        self.collector.side_effect = collect
        self.app.state.collection.calistir()
        self.assertEqual(self.collector.call_count, 1)
        self.assertEqual(self.collector.call_args.args[0]["id"], "ai_source")

    def test_yeni_kontrol_vadesi_etkin_kullanici_araligini_kullanir(self):
        self.post("/api/sources/ai_source", {"enabled": True, "interval_hours": 72, "expected_version": 0})
        self.app.state.collection.calistir("ai_source")
        source = self.client.get("/api/sources/ai_source").json()
        self.assertEqual(tarih(source["next_due_at"]) - tarih(source["last_checked_at"]), timedelta(hours=72))

    def test_makale_oku_api_kalici_govde_ve_yeniden_deneme_beklemesi(self):
        path = f"/api/items/{self.ids['1']}/article"
        self.assertEqual(self.client.get(path).json()["status"], "okunmadi")
        self.fetcher.assert_not_called()
        response = self.post(path)
        self.assertEqual(response.status_code, 202)
        self.finish(self.app.state.reading.worker)
        result = self.client.get(path).json()
        self.assertEqual(result["status"], "hazir")
        self.assertEqual(result["body"], METIN)
        self.assertFalse(result["stale"])
        self.assertEqual(self.post(path).status_code, 429)
        self.close()
        self.open()
        self.assertEqual(self.client.get(path).json()["body"], METIN)
        self.assertEqual(self.fetcher.call_count, 1)

    def test_ai_hazir_ozet_kartlarda_ayridir_kaynak_degiserse_gosterilmez(self):
        item_id = self.ids["1"]
        original = next(item for item in self.state()["items"] if item["id"] == item_id)
        path = f"/api/items/{item_id}/ai"
        self.assertEqual(self.client.get(path).json()["status"], "yapilmadi")
        self.provider.assert_not_called()
        self.assertEqual(self.post(path, {"kind": "summary"}).status_code, 202)
        self.finish(self.app.state.ai.worker)
        result = self.client.get(path).json()
        self.assertEqual(result["status"], "hazir", result.get("last_error"))
        for cards in (self.state()["items"], self.client.get("/api/items").json()["items"]):
            item = next(row for row in cards if row["id"] == item_id)
            self.assertIn("ai_summary", item)
            self.assertEqual(item["title"], original["title"])
            self.assertEqual(item["summary"], original["summary"])
            self.assertEqual(item["source_id"], original["source_id"])
        self.app.state.depo.kaydet("ai_source", [{**self.raw, "excerpt": METIN + " Updated source evidence."}])
        self.assertTrue(self.client.get(path).json()["stale"])
        for cards in (self.state()["items"], self.client.get("/api/items").json()["items"]):
            self.assertNotIn("ai_summary", next(row for row in cards if row["id"] == item_id))

    def test_model_yapilandirilamadiysa_503_ve_bos_kayit_404(self):
        self.credentials.return_value = None
        self.assertFalse(self.client.get("/api/health").json()["provider_configured"])
        self.assertEqual(self.post(f"/api/items/{self.ids['1']}/ai", {"kind": "summary"}).status_code, 503)
        self.assertEqual(self.client.get("/api/items/missing/ai").status_code, 404)
        self.assertEqual(self.client.get("/api/items/missing/article").status_code, 404)
        self.assertEqual(self.post("/api/items/missing/article").status_code, 404)
        self.assertEqual(self.post(f"/api/items/{self.ids['1']}/ai", {"kind": "anything"}).status_code, 422)
        self.provider.assert_not_called()

    def test_makale_indirme_kayitli_govdeyi_korur_ve_eski_surumu_bildirir(self):
        path = f"/api/items/{self.ids['1']}/article"
        self.assertEqual(self.post(path).status_code, 202)
        self.finish(self.app.state.reading.worker)
        saved = self.client.get(path).json()
        self.fetcher.reset_mock()
        before = self.app.state.depo.disa_aktar()
        response = self.client.get(path + "/download")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-disposition"], 'attachment; filename="ufuk-makale.txt"')
        self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertTrue(response.text.endswith("\n\n" + METIN + "\n"))
        self.assertIn(saved["title"], response.text)
        self.assertIn(saved["source_url"], response.text)
        after = self.app.state.depo.disa_aktar()
        for table in ("article_reads", "article_read_attempts", "ai_results", "ai_attempts"):
            self.assertEqual(before[table], after[table])
        self.app.state.depo.kaydet("ai_source", [{**self.raw, "excerpt": METIN + " Updated evidence."}])
        stale = self.client.get(path + "/download")
        self.assertEqual(stale.status_code, 200)
        self.assertIn("bu kayıt eski bir sürüme aittir", stale.text)
        self.assertIn(METIN, stale.text)
        for operation in (self.provider, self.fetcher, self.collector):
            operation.assert_not_called()

    def test_ai_indirme_iki_is_turunde_metni_alintilari_ve_kaynaklari_korur(self):
        Depo(self.path, [kaynak("second_source")]).kaydet("second_source", [haber(4)])
        for number, kind, filename in (("1", "summary", "ufuk-ozet.txt"), ("2", "research", "ufuk-arastirma.txt")):
            with self.subTest(kind=kind):
                path = f"/api/items/{self.ids[number]}/ai"
                self.assertEqual(self.post(path, {"kind": kind}).status_code, 202)
                self.finish(self.app.state.ai.worker)
                saved = self.client.get(path, params={"kind": kind}).json()
                self.assertEqual(saved["status"], "hazir", saved.get("last_error"))
                self.provider.reset_mock()
                before = self.app.state.depo.disa_aktar()
                response = self.client.get(path + "/download", params={"kind": kind})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-disposition"], f'attachment; filename="{filename}"')
                self.assertEqual(response.headers["content-type"], "text/plain; charset=utf-8")
                self.assertIn(saved["text"], response.text)
                for evidence in saved["result"]["evidence"]:
                    self.assertIn(evidence["quote"], response.text)
                for source in saved["sources"]:
                    self.assertIn(source["title"], response.text)
                    self.assertIn(source["url"], response.text)
                after = self.app.state.depo.disa_aktar()
                for table in ("article_reads", "article_read_attempts", "ai_results", "ai_attempts"):
                    self.assertEqual(before[table], after[table])
                self.app.state.depo.kaydet("ai_source", [{**haber(int(number)), "excerpt": METIN + " Changed evidence."}])
                stale = self.client.get(path + "/download", params={"kind": kind})
                self.assertEqual(stale.status_code, 200)
                self.assertIn("bu kayıt eski bir sürüme aittir", stale.text)
                self.assertIn(saved["text"], stale.text)
                for operation in (self.provider, self.fetcher, self.collector):
                    operation.assert_not_called()

    def test_indirme_bos_sonuc_ve_kayitta_404_gecersiz_turde_422_verir(self):
        for item_id in (self.ids["1"], "missing"):
            for suffix in ("article/download", "ai/download?kind=summary", "ai/download?kind=research"):
                with self.subTest(item_id=item_id, suffix=suffix):
                    self.assertEqual(self.client.get(f"/api/items/{item_id}/{suffix}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/items/{self.ids['1']}/ai/download?kind=anything").status_code, 422)
        for operation in (self.provider, self.fetcher, self.collector):
            operation.assert_not_called()

    def test_guncel_ai_basligi_ozeti_ve_yarari_aranir_eski_uretim_eslesmez(self):
        path = f"/api/items/{self.ids['1']}/ai"
        queries = ("yöntemler", "anlatıyor", "değerlendirmeye")
        for query in queries:
            self.assertEqual(self.client.get("/api/items", params={"query": query}).json()["total"], 0)
        self.assertEqual(self.post(path, {"kind": "summary"}).status_code, 202)
        self.finish(self.app.state.ai.worker)
        self.assertEqual(self.client.get(path).json()["status"], "hazir")
        for query in queries:
            with self.subTest(query=query):
                found = self.client.get("/api/items", params={"query": query, "limit": 1}).json()
                self.assertEqual(found["total"], 1)
                self.assertEqual(found["items"][0]["id"], self.ids["1"])
        self.app.state.depo.kaydet("ai_source", [{**self.raw, "excerpt": METIN + " Changed source."}])
        for query in queries:
            self.assertEqual(self.client.get("/api/items", params={"query": query}).json()["total"], 0)

    def test_isci_hatalari_http_durumuna_ve_bekleme_basligina_eslenir(self):
        for worker, suffix, error, code in (
            (self.app.state.reading, "article", OkumaMesgul("Meşgul"), 409),
            (self.app.state.reading, "article", OkumaSiniri("Bekle", 17), 429),
            (self.app.state.reading, "article", OkumaHazirDegil("Hazır değil"), 503),
            (self.app.state.ai, "ai", ZekaMesgul("Meşgul"), 409),
            (self.app.state.ai, "ai", ZekaSiniri("Bekle", 19), 429),
            (self.app.state.ai, "ai", ZekaHazirDegil("Hazır değil"), 503),
        ):
            with self.subTest(error=type(error).__name__), patch.object(worker, "baslat", side_effect=error):
                response = self.post(f"/api/items/{self.ids['1']}/{suffix}")
                self.assertEqual(response.status_code, code)
                if code == 429:
                    self.assertEqual(response.headers["Retry-After"], str(error.retry_after_seconds))

    def test_disa_aktarim_tum_yeni_tablolari_ve_verileri_tasir(self):
        self.post(f"/api/items/{self.ids['1']}/read", {"read": True})
        group = self.post("/api/collections", {"name": "Okuma"}).json()
        self.post(f"/api/collections/{group['id']}/items", {"item_ids": [self.ids["1"]], "expected_version": group["version"]})
        self.post("/api/searches", {"name": "Ses", "query": "voice"})
        self.post("/api/sources/ai_source", {"enabled": True, "interval_hours": 48, "expected_version": 0})
        self.post(f"/api/items/{self.ids['1']}/article")
        self.finish(self.app.state.reading.worker)
        self.post(f"/api/items/{self.ids['1']}/ai", {"kind": "summary"})
        self.finish(self.app.state.ai.worker)
        exported = self.client.get("/api/export").json()
        self.assertEqual(exported["schema_version"], 2)
        for table in ("user_settings", "item_read_states", "user_collections", "collection_items", "saved_searches",
                      "source_preferences", "article_reads", "article_read_attempts", "ai_results", "ai_attempts"):
            with self.subTest(table=table):
                self.assertEqual(len(exported[table]), 2 if table == 'ai_attempts' else 1)
        self.assertEqual({row['kind'] for row in exported['ai_attempts']}, {'summary', 'summary:verify'})
        self.assertIn("custom_sources", exported)
        self.assertNotIn("LOCAL-FAKE-TEST-KEY", json.dumps(exported))

    def test_kapanista_butun_isci_yasam_donguleri_sonlanir(self):
        app = self.app
        self.close()
        self.assertTrue(app.state.reading.durum()["closed"])
        self.assertTrue(app.state.ai.durum()["closed"])
        self.assertTrue(app.state.collection.stop.is_set())
        self.open()
        self.assertFalse(self.app.state.ai.durum()["closed"])

    def test_yapay_zeka_acilisi_hata_verirse_okuma_sahipligi_serbest_kalir(self):
        self.close()
        with patch("radar.sunucu.ZekaIsleri", side_effect=ZekaMesgul("Başka hizmet açık")):
            with self.assertRaises(ZekaMesgul):
                uygulama(self.path, sources=[kaynak()], seed=False)
        reading = OkumaIsleri(self.app.state.depo, fetcher=self.fetcher)
        self.addCleanup(reading.kapan)
        self.assertFalse(reading.durum()["closed"])

    def test_yedek_baslangici_hata_verirse_tum_sahiplikler_serbest_kalir(self):
        self.close()
        failed_app = uygulama(self.path, sources=[kaynak()], seed=False, automatic_backup=True,
                              credential_provider=lambda: None)
        with patch.object(failed_app.state.backup, "baslat", side_effect=RuntimeError("Test hata")):
            with self.assertRaises(RuntimeError):
                with TestClient(failed_app, base_url=ADRES):
                    pass
        self.assertTrue(failed_app.state.reading.durum()["closed"])
        self.assertTrue(failed_app.state.ai.durum()["closed"])
        self.open()


if __name__ == "__main__":
    unittest.main()

"""Yerel web arayüzünün erişim sınırlarını ve veri akışını dış ağ olmadan doğrular."""

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from fastapi.testclient import TestClient

from radar.konular import KONULAR
from radar.sunucu import uygulama


ADRES = "http://127.0.0.1:8781"


def kaynak():
    return {
        "id": "deneme", "name": "Deneme kaynağı", "topic": "ai",
        "url": "https://example.org/", "fetch_url": "https://example.org/feed.xml",
        "kind": "rss", "interval_hours": 12, "benefit": "Yerel model araştırmasına katkı.",
        "enabled": True,
    }


def haber(**degisiklikler):
    sonuc = {
        "external_id": "haber-1", "url": "https://example.org/haber-1",
        "title": "Yerel araştırma denemesi", "excerpt": "Kaynağın kısa açıklaması.",
        "published_at": datetime.now(timezone.utc).isoformat(), "updated_at": None,
        "event_at": None, "evidence_type": "Birincil araştırma", "language": "tr",
    }
    sonuc.update(degisiklikler)
    return sonuc


class SunucuKabulTestleri(unittest.TestCase):
    def setUp(self):
        self.gecici = tempfile.TemporaryDirectory()
        self.addCleanup(self.gecici.cleanup)
        self.collector = Mock(return_value={"items": [haber()], "etag": '"deneme"',
                                           "modified": None, "not_modified": False})
        self.app = uygulama(Path(self.gecici.name) / "radar.sqlite3", sources=[kaynak()],
                            seed=False, collector=self.collector)
        self.client = TestClient(self.app, base_url=ADRES)
        self.client.__enter__()
        self.addCleanup(self.kapat)
        self.token = self.client.get("/api/state").json()["csrf_token"]
        self.headers = {"X-Radar-CSRF": self.token, "Origin": ADRES}

    def kapat(self):
        self.app.state.collection.kapan()
        worker = self.app.state.collection.thread
        if worker:
            worker.join(timeout=3)
        self.client.__exit__(None, None, None)

    def ekle(self, **degisiklikler):
        self.app.state.depo.kaydet("deneme", [haber(**degisiklikler)])
        return self.client.get("/api/state").json()["items"][0]

    def post(self, path, payload=None, **kwargs):
        return self.client.post(path, headers=self.headers, json={} if payload is None else payload, **kwargs)

    def test_bos_pilot_kendi_basinayken_ag_veya_arastirma_baslatmaz(self):
        state = self.client.get("/api/state").json()
        self.assertEqual(state["items"], [])
        self.assertEqual(state["jobs"], [])
        self.assertFalse(state["collection"]["running"])
        self.assertEqual(state["settings"]["mode"], "local")
        for alan in ("automatic_research", "paid_models", "telegram"):
            self.assertFalse(state["settings"][alan])
        self.collector.assert_not_called()

    def test_yazma_csrf_olmadan_ve_yanlis_tokenle_reddedilir(self):
        kart = self.ekle()
        path = f"/api/items/{kart['id']}/feedback"
        for headers in ({}, {"X-Radar-CSRF": "yanlis"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.post(path, json={"value": "yararli"}, headers=headers).status_code, 403)
        self.assertEqual(self.app.state.depo.disa_aktar()["feedback_events"], [])

    def test_yabanci_host_origin_ve_site_baglami_okuma_ve_yazmada_reddedilir(self):
        for headers in ({"Host": "radar.example:8781"}, {"Host": "127.0.0.1.attacker.test:8781"},
                        {"Origin": "https://attacker.test"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get("/api/state", headers=headers).status_code, 403)
                response = self.client.post("/api/collect", json={}, headers={**self.headers, **headers})
                self.assertEqual(response.status_code, 403)
        self.collector.assert_not_called()

    def test_json_disindaki_ve_fazla_buyuk_govdeler_reddedilir(self):
        for media_type in ("text/plain", "application/x-www-form-urlencoded", "text/application/json"):
            with self.subTest(media_type=media_type):
                response = self.client.post("/api/collect", content="{}",
                                            headers={**self.headers, "Content-Type": media_type})
                self.assertEqual(response.status_code, 415)
        response = self.client.post("/api/collect", content=json.dumps({"source_id": "x" * 5000}),
                                    headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.collector.assert_not_called()

    def test_tercih_girdileri_ve_bilinmeyen_kartlar_dogrulanir(self):
        kart = self.ekle()
        path = f"/api/items/{kart['id']}/feedback"
        for body in ({"value": "dagit"}, {"value": "yararli", "note": "a" * 501},
                     {"value": "yararli", "execute": True}, {"value": 1}, {}):
            with self.subTest(body=str(body)[:80]):
                self.assertEqual(self.post(path, body).status_code, 422)
        self.assertEqual(self.post("/api/items/olmayan/feedback", {"value": "yararli"}).status_code, 404)
        self.assertEqual(self.post("/api/items/olmayan/research").status_code, 404)
        self.assertEqual(self.post("/api/collect", {"source_id": "olmayan"}).status_code, 404)
        self.assertEqual(self.post("/api/collect", {"force": True}).status_code, 422)
        self.assertEqual(self.app.state.depo.disa_aktar()["feedback_events"], [])
        self.collector.assert_not_called()

    def test_arastirma_yalniz_bos_komut_govdesini_kabul_eder(self):
        kart = self.ekle()
        path = f"/api/items/{kart['id']}/research"
        self.assertEqual(self.post(path, {"execute": True, "owner": "claude"}).status_code, 422)
        response = self.client.post(path, content="bu-json-degil",
                                    headers={**self.headers, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.app.state.depo.gorunum()["jobs"], [])

    def test_arastirma_tekrari_tek_bekleyen_is_olusturur(self):
        kart = self.ekle()
        path = f"/api/items/{kart['id']}/research"
        ilk = self.post(path)
        ikinci = self.post(path)
        self.assertEqual(ilk.status_code, 200)
        self.assertEqual(ikinci.json()["id"], ilk.json()["id"])
        self.assertEqual(ilk.json()["status"], "bekliyor")
        state = self.client.get("/api/state").json()
        self.assertEqual(len(state["jobs"]), 1)
        self.assertTrue(state["items"][0]["selected"])
        self.collector.assert_not_called()

    def test_sonraya_birakma_ve_geri_alma_api_uzerinden_kalici(self):
        kart = self.ekle()
        path = f"/api/items/{kart['id']}/feedback"
        self.assertEqual(self.post(path, {"value": "sonra", "note": "Haftaya."}).status_code, 200)
        self.assertIsNotNone(self.client.get("/api/state").json()["items"][0]["snoozed_until"])
        self.assertEqual(self.post(path, {"value": "yok"}).status_code, 200)
        self.assertIsNone(self.client.get("/api/state").json()["items"][0]["snoozed_until"])
        self.assertEqual(len(self.app.state.depo.disa_aktar()["feedback_events"]), 2)

    def test_inceleme_notu_sonuc_ve_dosya_api_uzerinden_kalici(self):
        kart = self.ekle()
        job = self.post(f"/api/items/{kart['id']}/research").json()
        path = f"/api/jobs/{job['id']}"
        self.assertEqual(self.client.get(path).json()["version"], 0)
        payload = {"note": "Kaynağı okudum; iddia bağımsız bir deneyle doğrulanmadı.",
                   "status": "tamamlandi", "expected_version": 0, "source_revision": 1}
        response = self.post(path + "/note", payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "tamamlandi")
        self.assertEqual(self.client.get(path).json()["note"], payload["note"])
        self.assertEqual(self.post(path + "/note", payload).status_code, 409)
        report = self.client.get(path + "/export")
        self.assertIn("attachment", report.headers["content-disposition"])
        self.assertIn(payload["note"], report.text)
        self.assertIn(kart["url"], report.text)
        self.assertEqual(len(self.client.get("/api/export").json()["research_notes"]), 1)

    def test_inceleme_notu_yazma_yetkisi_ve_icerik_sinirlari(self):
        kart = self.ekle()
        job = self.post(f"/api/items/{kart['id']}/research").json()
        path = f"/api/jobs/{job['id']}/note"
        payload = {"note": "a" * 20, "status": "bekliyor", "expected_version": 0, "source_revision": 1}
        self.assertEqual(self.client.post(path, json=payload).status_code, 403)
        for change in ({"status": "calistir"}, {"note": "" , "status": "tamamlandi"},
                       {"expected_version": -1}, {"source_revision": True}, {"note": "a" * 2001},
                       {"command": "run"}):
            with self.subTest(change=str(change)[:60]):
                self.assertEqual(self.post(path, {**payload, **change}).status_code, 422)
        self.assertEqual(self.post("/api/jobs/olmayan/note", payload).status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/olmayan").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/olmayan/export").status_code, 404)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}").json()["version"], 0)

    def test_arsivin_tam_aranmasi_ve_sayfa_sinirlari(self):
        self.ekle(title="İZMİR için sera sensörü")
        result = self.client.get("/api/items", params={"query": "izmir", "limit": 1}).json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.client.get("/api/items", params={"offset": 1}).json()["items"], [])
        self.assertEqual(self.client.get("/api/items", params={"include_archive": "false"}).json()["total"], 0)
        for query in ({"limit": 101}, {"limit": 0}, {"offset": -1}, {"topic": "bilinmiyor"}, {"query": "a" * 201}):
            self.assertEqual(self.client.get("/api/items", params=query).status_code, 422)

    def test_kaynak_kutuphanesi_indirme_ve_kaynaga_gore_kayit_suzme(self):
        kart = self.ekle()
        self.app.state.depo.kaydet('deneme', [haber(external_id='ayni-haber-yeni-kimlik')])
        result = self.client.get('/api/sources/export')
        self.assertEqual(result.status_code, 200)
        self.assertIn('attachment', result.headers['content-disposition'])
        self.assertTrue(result.json()['exported_at'])
        self.assertEqual(result.json()['sources'][0]['item_count'], 1)
        self.assertEqual(result.json()['sources'][0]['id'], 'deneme')
        filtered = self.client.get('/api/items', params={'source': 'deneme'}).json()
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['items'][0]['id'], kart['id'])
        self.assertEqual(self.client.get('/api/items', params={'source': 'olmayan'}).status_code, 422)
        self.assertEqual(self.client.get('/api/items', params={'source': 'a' * 65}).status_code, 422)
        settings = self.client.get('/api/state').json()['settings']
        self.assertEqual(settings['source_limit'], 1 + len(KONULAR))  # Beslemeye her konunun keşif kaynağı eklenir.
        self.assertEqual(settings['scan_batch_limit'], 12)

    def test_yedek_api_tutarli_indirilebilir_dosya_ve_hata_durumu(self):
        kart = self.ekle()
        self.post(f"/api/items/{kart['id']}/feedback", {"value": "yararli"})
        self.assertEqual(self.client.get("/api/backup/download").status_code, 404)
        self.assertEqual(self.client.post("/api/backup", json={}).status_code, 403)
        result = self.post("/api/backup")
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()["available"])
        downloaded = self.client.get("/api/backup/download")
        self.assertEqual(downloaded.status_code, 200)
        path = Path(self.gecici.name) / "indirilen.sqlite3"
        path.write_bytes(downloaded.content)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT feedback FROM items WHERE id=?", (kart["id"],)).fetchone()[0], "yararli")
        finally:
            connection.close()
        with patch.object(self.app.state.backup, "yedekle", return_value=False):
            self.assertEqual(self.post("/api/backup").status_code, 409)
        with patch.object(self.app.state.backup, "yedekle", side_effect=sqlite3.DatabaseError("Özel teknik ayrıntı")):
            response = self.post("/api/backup")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("Özel teknik ayrıntı", response.text)

    def test_html_benzeri_dis_metin_json_olarak_tasinir_ve_sayfaya_gomulmez(self):
        metin = '</script><img src=x onerror="alert(1)"><script>alert(2)</script>'
        self.ekle(title=metin, excerpt=metin)
        response = self.client.get("/api/state")
        self.assertTrue(response.headers["content-type"].startswith("application/json"))
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.json()["items"][0]["title"], metin)
        self.assertIn("script-src 'self'", response.headers["content-security-policy"])
        self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertNotIn(metin, home.text)
        self.assertEqual(home.headers["cache-control"], "no-store")
        self.assertEqual(home.headers["referrer-policy"], "no-referrer")

    def test_disa_aktarim_kanit_tercih_is_ve_denetim_izini_tam_tasir(self):
        kart = self.ekle()
        self.app.state.depo.kaydet("deneme", [haber(title="Düzeltilmiş başlık")])
        self.post(f"/api/items/{kart['id']}/feedback", {"value": "yararli", "note": "İncelensin."})
        self.post(f"/api/items/{kart['id']}/research")
        revisions = self.client.get(f"/api/items/{kart['id']}/revisions").json()["revisions"]
        self.assertEqual(len(revisions), 2)
        response = self.client.get("/api/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response.headers["content-disposition"])
        aktarim = response.json()
        for alan in ("sources", "items", "source_entries", "revisions", "feedback_events", "jobs", "audit"):
            self.assertGreater(len(aktarim[alan]), 0, alan)
        self.assertEqual(aktarim["schema_version"], 2)
        self.assertEqual(aktarim["feedback_events"][0]["note"], "İncelensin.")
        self.assertEqual(aktarim["jobs"][0]["item_id"], kart["id"])
        self.assertEqual(len(aktarim["revisions"]), 2)
        self.assertNotIn("csrf_token", aktarim)

    def test_mock_toplama_arsiv_olusturur_ve_vadesinden_once_yeniden_okumaz(self):
        self.assertEqual(self.post("/api/collect").status_code, 202)
        self.app.state.collection.thread.join(timeout=3)
        self.assertFalse(self.app.state.collection.thread.is_alive())
        state = self.client.get("/api/state").json()
        self.assertEqual(state["stats"]["total"], 1)
        self.assertTrue(state["items"][0]["is_archive"])
        self.assertFalse(state["collection"]["running"])
        self.assertIsNotNone(state["sources"][0]["last_success_at"])
        self.assertEqual(self.collector.call_count, 1)
        self.assertEqual(self.post("/api/collect").status_code, 202)
        self.app.state.collection.thread.join(timeout=3)
        self.assertEqual(self.collector.call_count, 1)

    def test_eszamanli_toplama_ikinci_isci_baslatmaz(self):
        basladi = threading.Event()
        devam = threading.Event()
        self.addCleanup(devam.set)

        def beklet(*args, **kwargs):
            basladi.set()
            devam.wait(timeout=3)
            return {"items": [haber()], "not_modified": False}

        self.collector.side_effect = beklet
        self.assertEqual(self.post("/api/collect").status_code, 202)
        self.assertTrue(basladi.wait(timeout=2))
        self.assertEqual(self.post("/api/collect").status_code, 409)
        devam.set()
        self.app.state.collection.thread.join(timeout=3)
        self.assertEqual(self.collector.call_count, 1)

    def test_toplayici_hatasi_kaynakta_gorunur_ve_kart_uydurmaz(self):
        self.collector.side_effect = ValueError("Deneme kaynağı okunamadı.")
        self.assertEqual(self.post("/api/collect").status_code, 202)
        self.app.state.collection.thread.join(timeout=3)
        state = self.client.get("/api/state").json()
        self.assertFalse(state["collection"]["running"])
        self.assertEqual(state["items"], [])
        self.assertEqual(state["sources"][0]["last_error"], "Deneme kaynağı okunamadı.")
        self.assertEqual(state["sources"][0]["failures"], 1)
        self.assertTrue(any(row["kind"] == "tarama_hata" for row in self.app.state.depo.disa_aktar()["audit"]))


if __name__ == "__main__":
    unittest.main()

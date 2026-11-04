"""Yerel platformun HTTP sınırlarını, seçili paylaşımını ve kalıcı kayıtlarını ağ olmadan doğrular."""

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".deps"))

from fastapi.testclient import TestClient

from radar.sunucu import uygulama


ADRES = "http://127.0.0.1:8781"
PACKET_FIELDS = {
    "schema", "schema_version", "kind", "title", "summary", "body", "url", "tags",
    "sources", "author", "license", "created_at", "updated_at",
}


def kayit(**changes):
    return {
        "kind": "research",
        "title": "Yerel sensör araştırması",
        "summary": "Açık sensör belgelerinden hazırlanan araştırma özeti.",
        "body": "Kaynakta açıklanan sensörler karşılaştırıldı. Donanım üzerinde bağımsız deney yapılmadı.",
        "url": "https://example.org/research",
        "tags": ["robotik", "sensör"],
        "sources": [{"title": "Sensörün açık belgesi", "url": "https://example.org/docs"}],
        "author": "Deneme araştırmacısı",
        "license": "CC-BY-4.0",
        **changes,
    }


def kaynak(topic):
    return {
        "id": f"{topic}_source", "name": f"{topic} kaynağı", "topic": topic,
        "publisher": "Örnek yayıncı", "url": "https://example.org/",
        "fetch_url": f"https://example.org/{topic}.xml", "kind": "rss",
        "interval_hours": 24, "benefit": "Kaynak belgesini incele.", "enabled": True,
        "fetch_hosts": ["example.org"], "item_hosts": ["example.org"],
    }


def paket(number=1, **changes):
    stamp = f"2026-10-{number:02d}T12:00:00+00:00"
    return {
        **kayit(title=f"Ortak araştırma {number}", url=f"https://example.org/research-{number}"),
        "schema": "ufuk-share", "schema_version": 1,
        "created_at": stamp, "updated_at": stamp,
        **changes,
    }


class PlatformApiTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "platform.sqlite3"
        self.collector = Mock(side_effect=AssertionError("Platform kaynak toplamamalı."))
        self.fetcher = Mock(side_effect=AssertionError("Platform makale indirmemeli."))
        self.provider = Mock(side_effect=AssertionError("Platform model çağırmamalı."))
        self.discovery = Mock(side_effect=AssertionError("Platform ağ keşfi yapmamalı."))
        self.scout = Mock(side_effect=AssertionError("Platform kaynak aramamalı."))
        self.credentials = Mock(return_value="PLATFORM-LOCAL-TEST-CREDENTIAL")
        self.client = None
        self.open()
        self.addCleanup(self.close)

    def open(self):
        self.app = uygulama(
            self.path, sources=[kaynak("robotik"), kaynak("tarim")], seed=False,
            collector=self.collector, article_fetcher=self.fetcher,
            ai_provider=self.provider, credential_provider=self.credentials,
            discovery_fetcher=self.discovery, source_scout_fetcher=self.scout,
        )
        self.client = TestClient(self.app, base_url=ADRES)
        self.client.__enter__()
        state = self.client.get("/api/state")
        self.assertEqual(state.status_code, 200, state.text)
        self.headers = {"X-Radar-CSRF": state.json()["csrf_token"], "Origin": ADRES}

    def close(self):
        if self.client is not None:
            self.client.__exit__(None, None, None)
            self.client = None

    def post(self, path, value):
        return self.client.post(path, json=value, headers=self.headers)

    def state(self):
        response = self.client.get("/api/platform")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def snapshot(self):
        value = self.app.state.depo.disa_aktar()
        value.pop("exported_at")
        return value

    def create(self, **changes):
        response = self.post("/api/platform/entries", kayit(**changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def import_packet(self, value):
        response = self.post("/api/platform/import", {"packet": value})
        self.assertIn(response.status_code, (200, 201), response.text)
        return response.json()

    def assert_no_external_work(self):
        for operation in (self.collector, self.fetcher, self.provider, self.discovery, self.scout):
            operation.assert_not_called()

    def test_indirme_onizlenen_surum_ve_alanlarla_sinirlidir(self):
        entry = self.create()
        preview = self.post(f"/api/platform/entries/{entry['id']}/preview", {"expected_version": 1}).json()
        url = f"/api/platform/entries/{entry['id']}/download?expected_version=1"
        downloaded = self.client.get(url)
        self.assertEqual(downloaded.status_code, 200)
        self.assertIn('attachment;', downloaded.headers['content-disposition'])
        self.assertEqual(downloaded.json(), preview['packet'])
        self.assertEqual(set(downloaded.json()), PACKET_FIELDS)
        self.post(f"/api/platform/entries/{entry['id']}", {**kayit(title="Changed after preview"), "expected_version": 1})
        self.assertEqual(self.client.get(url).status_code, 409)
        self.assertEqual(self.client.get(url, headers={"Origin": "https://example.org"}).status_code, 403)
        self.assert_no_external_work()

    def test_acilis_html_ve_okumalar_ag_ve_model_cagirmaz(self):
        response = self.client.get("/platform")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/html", response.headers["content-type"])
        state = self.state()
        self.assertEqual(state["mode"], "local")
        self.assertEqual(state["csrf_token"], self.headers["X-Radar-CSRF"])
        self.assertIn("interests", state["settings"])
        self.assertIsInstance(state["settings"]["version"], int)
        self.assertEqual(state["entries"], [])
        self.assertIn("coverage", state)
        response = self.client.get("/api/platform/feed", params={"interest": "robotik", "offset": 0, "limit": 2})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])
        self.assert_no_external_work()

    def test_host_origin_ve_csrf_yeni_uclarda_korunur(self):
        entry = self.create()
        entry_url = f"/api/platform/entries/{entry['id']}"
        writes = [
            ("/api/platform/settings", {"interests": ["robotik"], "expected_version": 0}),
            ("/api/platform/entries", kayit()),
            (entry_url, {**kayit(), "expected_version": entry["version"]}),
            (entry_url + "/archive", {"archived": True, "expected_version": entry["version"]}),
            (entry_url + "/preview", {"expected_version": entry["version"]}),
            ("/api/platform/import/preview", {"packet": paket()}),
            ("/api/platform/import", {"packet": paket()}),
        ]
        before = self.snapshot()
        for headers in ({"Host": "ufuk.attacker.test"}, {"Host": "127.0.0.1.attacker.test:8781"},
                        {"Origin": "https://attacker.test"}, {"Origin": "null"},
                        {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                for route in ("/platform", "/api/platform", "/api/platform/feed"):
                    self.assertEqual(self.client.get(route, headers=headers).status_code, 403)
                for route, value in writes:
                    response = self.client.post(route, json=value, headers={**self.headers, **headers})
                    self.assertEqual(response.status_code, 403, (route, response.text))
        for headers in ({"Origin": ADRES}, {"Origin": ADRES, "X-Radar-CSRF": "wrong"}):
            for route, value in writes:
                with self.subTest(route=route, headers=headers):
                    self.assertEqual(self.client.post(route, json=value, headers=headers).status_code, 403)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_external_work()

    def test_olusturma_duzenleme_eski_surum_ve_arsivleme(self):
        entry = self.create(kind="project")
        self.assertEqual(entry["origin"], "local")
        self.assertFalse(entry["archived"])
        url = f"/api/platform/entries/{entry['id']}"
        edit = {**kayit(kind="project", title="Güncellenmiş sensör projesi"), "expected_version": entry["version"]}
        response = self.post(url, edit)
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()
        self.assertEqual(updated["title"], edit["title"])
        self.assertGreater(updated["version"], entry["version"])
        before = self.snapshot()
        self.assertEqual(self.post(url, edit).status_code, 409)
        self.assertEqual(self.post(url + "/preview", {"expected_version": entry["version"]}).status_code, 409)
        self.assertEqual(self.post(url + "/archive", {"archived": True, "expected_version": entry["version"]}).status_code, 409)
        self.assertEqual(self.snapshot(), before)
        response = self.post(url + "/archive", {"archived": True, "expected_version": updated["version"]})
        self.assertEqual(response.status_code, 200, response.text)
        archived = response.json()
        self.assertTrue(archived["archived"])
        self.assertGreater(archived["version"], updated["version"])
        response = self.post(url + "/archive", {"archived": False, "expected_version": archived["version"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["archived"])

    def test_ayar_surum_korumasi_ve_yeniden_acilista_kalici_veri(self):
        initial = self.state()["settings"]
        payload = {"interests": ["robotik", "tarım"], "expected_version": initial["version"]}
        response = self.post("/api/platform/settings", payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.post("/api/platform/settings", payload).status_code, 409)
        project = self.create(kind="project", title="Yerel sera projesi")
        research = self.create(title="Yerel sera araştırması")
        saved = self.state()["settings"]
        self.assertEqual(saved["interests"], payload["interests"])
        self.assertGreater(saved["version"], initial["version"])
        self.close()
        self.open()
        state = self.state()
        self.assertEqual(state["settings"], saved)
        found = {entry["id"]: entry for entry in state["entries"]}
        self.assertEqual(found[project["id"]]["kind"], "project")
        self.assertEqual(found[research["id"]]["kind"], "research")
        self.assert_no_external_work()

    def test_bilinmeyen_alan_ve_yanlis_tip_kalici_veriyi_degistirmez(self):
        entry = self.create()
        url = f"/api/platform/entries/{entry['id']}"
        settings = self.state()["settings"]
        invalid = [
            ("/api/platform/entries", kayit(unknown="extra")),
            ("/api/platform/entries", kayit(tags="robotik")),
            ("/api/platform/entries", kayit(sources=[{"title": "Kaynak", "url": "https://example.org/", "token": "extra"}])),
            (url, {**kayit(), "expected_version": entry["version"], "unknown": True}),
            (url + "/archive", {"archived": "true", "expected_version": entry["version"]}),
            (url + "/preview", {"expected_version": entry["version"], "include_private": True}),
            ("/api/platform/settings", {"interests": ["robotik"], "expected_version": settings["version"], "unknown": True}),
            ("/api/platform/import/preview", {"packet": paket(), "unknown": True}),
            ("/api/platform/import", {"packet": paket(), "unknown": True}),
        ]
        before = self.snapshot()
        for route, value in invalid:
            with self.subTest(route=route, value=value):
                response = self.post(route, value)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.snapshot(), before)

    def test_ozel_ag_ve_kimlik_bilgili_url_reddedilir(self):
        before = self.snapshot()
        for url in ("http://127.0.0.1:8781/private", "https://localhost/private",
                    "https://192.168.1.10/private", "https://[::1]/private",
                    "https://user:password@example.org/private", "file:///C:/private.txt"):
            for value in (kayit(url=url), kayit(sources=[{"title": "Kaynak", "url": url}])):
                with self.subTest(url=url, value=value):
                    response = self.post("/api/platform/entries", value)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.snapshot(), before)
        self.assert_no_external_work()

    def test_platform_yazmalarinda_32_kib_govde_siniri(self):
        entry = self.create()
        url = f"/api/platform/entries/{entry['id']}"
        body = json.dumps({"body": "ş" * 20000}, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(body), 32 * 1024)
        before = self.snapshot()
        for route in ("/api/platform/settings", "/api/platform/entries", url, url + "/archive",
                      url + "/preview", "/api/platform/import/preview", "/api/platform/import"):
            with self.subTest(route=route):
                response = self.client.post(route, content=body,
                                            headers={**self.headers, "Content-Type": "application/json"})
                self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(self.snapshot(), before)

    def test_secili_onizleme_ozel_kayit_ayar_ve_token_tasimaz(self):
        public = self.create()
        self.create(title="Özel çalışma", body="PRIVATE-RESEARCH-NOTE-KEEP-LOCAL", license=None)
        current = self.client.get("/api/personal").json()["settings"]
        private_settings = {key: current[key] for key in ("topic_weights", "daily_limit", "keywords", "muted_keywords")}
        private_settings.update(keywords=["PRIVATE-INTEREST-KEEP-LOCAL"], expected_version=current["version"])
        response = self.post("/api/personal/settings", private_settings)
        self.assertEqual(response.status_code, 200, response.text)
        before = self.snapshot()
        response = self.post(f"/api/platform/entries/{public['id']}/preview", {"expected_version": public["version"]})
        self.assertEqual(response.status_code, 200, response.text)
        preview = response.json()
        self.assertTrue(preview["notice"])
        shared = preview["packet"]
        self.assertEqual(set(shared), PACKET_FIELDS)
        self.assertEqual(shared["schema"], "ufuk-share")
        self.assertEqual(shared["schema_version"], 1)
        for field, value in kayit().items():
            self.assertEqual(shared[field], value)
        serialized = json.dumps(shared, ensure_ascii=False)
        for private_value in ("PRIVATE-RESEARCH-NOTE-KEEP-LOCAL", "PRIVATE-INTEREST-KEEP-LOCAL",
                              self.headers["X-Radar-CSRF"], "PLATFORM-LOCAL-TEST-CREDENTIAL"):
            self.assertNotIn(private_value, serialized)
        self.assertEqual(self.snapshot(), before)

    def test_ice_aktarma_onizlemesi_yazmaz_tekrar_aktarim_yinelenmez(self):
        value = paket()
        before = self.snapshot()
        response = self.post("/api/platform/import/preview", {"packet": value})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.snapshot(), before)
        imported = self.import_packet(value)
        self.assertFalse(imported["duplicate"])
        self.assertEqual(imported["entry"]["title"], value["title"])
        after_first = self.snapshot()
        repeated = self.import_packet(value)
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["entry"]["id"], imported["entry"]["id"])
        self.assertEqual(self.snapshot(), after_first)
        self.assertEqual(len(self.state()["entries"]), 1)
        self.assert_no_external_work()

    def test_bozuk_aktarim_onizleme_ve_kayitta_atomik_reddedilir(self):
        self.create()
        missing = paket()
        del missing["author"]
        variants = [
            paket(schema="unknown"), paket(schema_version=2), paket(schema_version=True),
            paket(unknown="not allowed"), paket(license="all-rights-reserved"),
            paket(url="https://127.0.0.1/private"), paket(tags="robotik"),
            paket(created_at="not a date"), missing,
        ]
        before = self.snapshot()
        for value in variants:
            for route in ("/api/platform/import/preview", "/api/platform/import"):
                with self.subTest(route=route, value=value):
                    response = self.post(route, {"packet": copy.deepcopy(value)})
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.snapshot(), before)

    def test_ilgi_filtresi_sayfalamadan_once_uygulanir_ve_tarih_sirasi_korunur(self):
        for number in range(1, 7):
            topic = "robotik" if number % 2 else "tarim"
            self.app.state.depo.kaydet(f"{topic}_source", [{
                "external_id": str(number), "url": f"https://example.org/news-{number}",
                "title": f"Kaynak haberi {number}", "excerpt": "Kaynakta yayımlanan araştırma açıklaması.",
                "published_at": (datetime.now(timezone.utc) - timedelta(days=7 - number)).isoformat(),
                "language": "tr", "evidence_type": "duyuru",
            }])
        self.create(title="Haber akışına karışmaması gereken yerel araştırma")
        first = self.client.get("/api/platform/feed", params={"interest": "robotik", "offset": 0, "limit": 2})
        second = self.client.get("/api/platform/feed", params={"interest": "robotik", "offset": 2, "limit": 2})
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["total"], 3)
        self.assertEqual(second.json()["total"], 3)
        entries = first.json()["items"] + second.json()["items"]
        self.assertEqual([entry["title"] for entry in entries], ["Kaynak haberi 5", "Kaynak haberi 3", "Kaynak haberi 1"])
        self.assertEqual(len({entry["id"] for entry in entries}), 3)
        self.assert_no_external_work()


if __name__ == "__main__":
    unittest.main()

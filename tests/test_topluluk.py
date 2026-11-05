"""Topluluk oturumlarını, sahipliği, moderasyon sürümlerini ve geri alınabilir yedekleri sınar."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from radar.topluluk_api import COOKIE, uygulama
from radar.topluluk_depo import ToplulukDeposu, parola_dogrula, veritabani_kopyala


PASSWORD = "example-test-passphrase"
ORIGIN = "https://community.example.org"


class ToplulukTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = [1793779200]
        self.app = uygulama(self.root / "community.sqlite3", public_url=ORIGIN, sources=[], clock=lambda: self.now[0])
        self.store = self.app.state.community
        self.author = self.store.hesap_ekle("author", "Gerçek hesap adı", "contributor", PASSWORD)
        self.other = self.store.hesap_ekle("other", "Diğer katkıcı", "contributor", PASSWORD)
        self.moderator = self.store.hesap_ekle("reviewer", "Moderatör hesabı", "moderator", PASSWORD)
        self.client = self.client_for()

    def client_for(self):
        client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(client.close)
        return client

    def session(self, client=None):
        return (client or self.client).get("/api/community/session").json()

    def post(self, path, payload, client=None, csrf=None):
        client = client or self.client
        token = csrf if csrf is not None else self.session(client)["csrf_token"]
        return client.post(path, json=payload, headers={"Origin": ORIGIN, "X-Ufuk-CSRF": token})

    def login(self, username="author", client=None):
        client = client or self.client
        response = self.post("/api/community/login", {"username": username, "password": PASSWORD}, client)
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def packet(self, **changes):
        value = {"schema": "ufuk-share", "schema_version": 1, "kind": "research", "title": "Kaynaklı araştırma",
                 "summary": "Özet", "body": "Kaynakta açıklanan ölçümler.", "url": "https://example.org/project",
                 "tags": ["Python", "Araştırma"], "sources": [{"title": "Kaynak", "url": "https://example.org/docs"}],
                 "author": "Paketin doğrulanmamış yazar iddiası", "license": "CC-BY-4.0",
                 "created_at": "2026-11-01T12:00:00+00:00", "updated_at": "2026-11-01T12:00:00+00:00"}
        value.update(changes)
        return value

    def submit(self, packet=None, key="example-request-0001", client=None):
        response = self.post("/api/community/submissions", {"packet": packet or self.packet(), "expected_version": 0,
                                                            "idempotency_key": key}, client)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["card"]

    def review(self, card, decision="publish", note="", client=None):
        client = client or self.login("reviewer", self.client_for())
        response = self.post(f'/api/community/submissions/{card["id"]}/decision',
                             {"decision": decision, "expected_version": card["version"], "note": note}, client)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["card"]

    def test_cookie_guvenligi_oturum_yenilenmesi_ve_cikis(self):
        initial = self.client.get("/api/community/session")
        cookie = initial.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=strict", cookie)
        old_token = self.client.cookies.get(COOKIE)
        old_csrf = initial.json()["csrf_token"]
        self.login()
        self.assertNotEqual(old_token, self.client.cookies.get(COOKIE))
        self.assertIsNone(self.store.oturum_oku(old_token))
        self.assertEqual(self.post("/api/community/logout", {}, csrf=old_csrf).status_code, 403)
        token = self.client.cookies.get(COOKIE)
        self.assertEqual(self.post("/api/community/logout", {}).status_code, 200)
        self.assertIsNone(self.store.oturum_oku(token))
        self.assertFalse(self.session()["authenticated"])

    def test_anonim_yalniz_yayini_okur_hesap_izleri_aciga_cikmaz(self):
        self.assertEqual(self.client.get("/api/community/mine").status_code, 401)
        self.assertEqual(self.post("/api/community/submissions", {"packet": self.packet(), "expected_version": 0,
                                                                 "idempotency_key": "anonymous-0001"}).status_code, 401)
        self.login()
        card = self.submit()
        anonymous = self.client_for()
        self.assertEqual(anonymous.get("/api/community/cards").json()["total"], 0)
        self.assertEqual(anonymous.get(f'/api/community/cards/{card["id"]}').status_code, 404)
        self.assertEqual(anonymous.get(f'/api/community/cards/{card["id"]}/packet').status_code, 404)
        self.review(card)
        result = anonymous.get(f'/api/community/cards/{card["id"]}').json()
        self.assertEqual(result["owner"], {"username": "author", "display_name": "Gerçek hesap adı"})
        self.assertEqual(result["packet"]["author"], "Paketin doğrulanmamış yazar iddiası")
        self.assertNotIn("password_hash", json.dumps(result))
        exported = anonymous.get(f'/api/community/cards/{card["id"]}/packet')
        self.assertIn("attachment", exported.headers["content-disposition"])
        self.assertEqual(exported.json(), self.packet())

    def test_sahiplik_ve_ayri_moderator_zorunlu(self):
        self.login()
        card = self.submit()
        other = self.login("other", self.client_for())
        self.assertEqual(self.post(f'/api/community/submissions/{card["id"]}',
            {"packet": self.packet(), "expected_version": 1, "idempotency_key": "other-request-0001"}, other).status_code, 403)
        self.assertEqual(self.post(f'/api/community/submissions/{card["id"]}/withdraw', {"expected_version": 1}, other).status_code, 403)
        self.assertEqual(self.client.get("/api/community/review").status_code, 403)
        self.assertEqual(self.post(f'/api/community/submissions/{card["id"]}/decision',
                                  {"decision": "publish", "expected_version": 1}).status_code, 403)
        moderator = self.login("reviewer", self.client_for())
        own = self.submit(key="moderator-own-0001", client=moderator)
        self.assertEqual(self.post(f'/api/community/submissions/{own["id"]}/decision',
                                  {"decision": "publish", "expected_version": 1}, moderator).status_code, 403)

    def test_model_bilgili_paket_moderasyon_ve_anonim_indirmede_korunur(self):
        self.login()
        provenance = {"model": "nvidia/example-recorded-model", "provider": "nvidia", "generated_at": None,
                      "question": "Which claims have independent evidence?", "topic": "ai", "source_url": "",
                      "evidence_status": "unverified", "language": "en", "modified": True}
        packet = self.packet(schema_version=2, provenance=provenance)
        card = self.submit(packet)
        anonymous = self.client_for()
        path = f'/api/community/cards/{card["id"]}/packet'
        self.assertEqual(anonymous.get(path).status_code, 404)
        self.review(card)
        result = anonymous.get(path)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), packet)

    def test_duzeltme_incelenirken_ve_reddedilince_eski_yayin_kalir(self):
        self.login()
        card = self.review(self.submit())
        original_version = card["published_version"]
        changed = self.packet(title="Yeni araştırma sürümü")
        response = self.post(f'/api/community/submissions/{card["id"]}',
                             {"packet": changed, "expected_version": card["version"], "idempotency_key": "update-request-0001"})
        self.assertEqual(response.status_code, 200, response.text)
        pending = response.json()["card"]
        public = self.client_for().get(f'/api/community/cards/{card["id"]}').json()
        self.assertEqual(public["packet"]["title"], "Kaynaklı araştırma")
        self.assertEqual(public["version"], original_version)
        rejected = self.review(pending, "reject", "Birincil kaynak açıklaması eksik.")
        self.assertEqual(self.client_for().get(f'/api/community/cards/{card["id"]}').json()["packet"]["title"], "Kaynaklı araştırma")
        response = self.post(f'/api/community/submissions/{card["id"]}',
                             {"packet": changed, "expected_version": rejected["version"], "idempotency_key": "update-request-0002"})
        published = self.review(response.json()["card"])
        self.assertEqual(self.client_for().get(f'/api/community/cards/{card["id"]}').json()["packet"]["title"], changed["title"])
        self.assertEqual(published["status"], "published")

    def test_eski_surum_yazamaz_tekrar_gonderim_tek_kayit_olur(self):
        self.login()
        payload = {"packet": self.packet(), "expected_version": 0, "idempotency_key": "idempotent-request-0001"}
        first = self.post("/api/community/submissions", payload).json()
        second = self.post("/api/community/submissions", payload).json()
        self.assertEqual(first["card"], second["card"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.client.get("/api/community/mine").json()["total"], 1)
        damaged = copy.deepcopy(payload)
        damaged["packet"]["title"] = "Farklı işlem"
        self.assertEqual(self.post("/api/community/submissions", damaged).status_code, 409)
        card = self.review(first["card"])
        self.assertEqual(self.post(f'/api/community/submissions/{card["id"]}',
            {"packet": self.packet(), "expected_version": 1, "idempotency_key": "outdated-request-0001"}).status_code, 409)

    def test_geri_cekilen_yayin_ve_paketi_anonime_kapanir(self):
        self.login()
        card = self.review(self.submit())
        response = self.post(f'/api/community/submissions/{card["id"]}/withdraw', {"expected_version": card["version"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["card"]["status"], "withdrawn")
        self.assertEqual(self.client.get("/api/community/cards").json()["total"], 0)
        self.assertEqual(self.client.get(f'/api/community/cards/{card["id"]}/packet').status_code, 404)
        self.assertEqual(self.client.get("/api/community/mine").json()["total"], 1)

    def test_csrf_host_origin_https_ve_govde_sinirlari(self):
        self.login()
        self.assertEqual(self.client.post("/api/community/logout", json={}, headers={"Origin": ORIGIN}).status_code, 403)
        self.assertEqual(self.client.post("/api/community/logout", json={}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("/api/community/cards", headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.get("http://community.example.org/api/community/cards").status_code, 400)
        response = self.client.post("/api/community/submissions", content=b"x" * 65537,
                                    headers={"Origin": ORIGIN, "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get("/api/community/cards").headers["x-content-type-options"], "nosniff")

    def test_gecersiz_paket_yerel_url_ve_fazla_alan_yazilmaz(self):
        self.login()
        for index, changes in enumerate(({"url": "http://127.0.0.1/private"}, {"owner": "reviewer"},
                                         {"license": None}, {"sources": []}, {"body": "界" * 12000})):
            response = self.post("/api/community/submissions", {"packet": self.packet(**changes), "expected_version": 0,
                                                                "idempotency_key": f"invalid-request-{index}"})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/community/mine").json()["total"], 0)

    def test_arama_ve_sayfalama_yalniz_yayindaki_surumlerde_calisir(self):
        self.login()
        moderator = self.login("reviewer", self.client_for())
        for index in range(3):
            card = self.submit(self.packet(title=f"Python araştırması {index}"), key=f"search-request-{index}")
            self.review(card, client=moderator)
        self.submit(self.packet(title="Henüz yayımlanmayan özel sözcük"), key="search-private-0001")
        result = self.client.get("/api/community/cards", params={"query": "PYTHON", "limit": 1, "offset": 1}).json()
        self.assertEqual(result["total"], 3)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(self.client.get("/api/community/cards", params={"query": "özel sözcük"}).json()["total"], 0)
        self.assertEqual(self.client.get("/api/community/cards", params={"query": "%' OR 1=1"}).json()["total"], 0)

    def test_summary_search_preserves_metadata_and_default_full_read_contract(self):
        self.login()
        packet = self.packet(body=("Published evidence. " * 400).rstrip())
        published = self.review(self.submit(packet))
        anonymous = self.client_for()
        params = {"query": "Published evidence", "kind": "research", "limit": 3}
        full = anonymous.get("/api/community/cards", params=params)
        summary = anonymous.get("/api/community/cards", params={**params, "view": "summary"})
        self.assertEqual(summary.status_code, 200, summary.text)
        expected = full.json()
        expected["items"][0]["packet"].pop("body")
        expected["view"] = "summary"
        self.assertEqual(summary.json(), expected)
        self.assertGreater(len(full.content) - len(summary.content), len(packet["body"].encode()) - 50)
        self.assertEqual(anonymous.get("/api/community/cards", params={**params, "view": "full"}).json(), full.json())
        path = f'/api/community/cards/{published["id"]}'
        self.assertEqual(anonymous.get(path).json()["packet"], packet)
        self.assertEqual(anonymous.get(path + "/packet").json(), packet)
        self.assertEqual(anonymous.get("/api/community/cards", params={"view": "unknown"}).status_code, 422)

    def test_haber_akisi_ayri_depodan_gelir(self):
        feed_depo = self.app.state.feed_depo
        source = {"id": "feed", "name": "Yayıncı", "topic": "ai", "benefit": "Gelişme", "enabled": True, "interval_hours": 24}
        from radar.depo import Depo
        Depo(feed_depo.path, [source]).kaydet("feed", [{"url": "https://example.org/news", "title": "Yeni araç",
                                                     "published_at": "2026-11-01T12:00:00+00:00"}])
        response = self.client.get("/api/community/feed").json()
        self.assertEqual(response["total"], 1)
        self.assertEqual(response["items"][0]["source_name"], "Yayıncı")
        self.assertEqual(response["coverage"]["checked_sources"], 0)
        self.assertNotEqual(feed_depo.path, self.store.path)

    def test_kisisel_ve_inceleme_listeleri_yuzden_sonra_aranir_ve_sayfalanir(self):
        self.login()
        moderator = self.login("reviewer", self.client_for())
        wanted = None
        for index in range(105):
            title = "Ölçümler sayfalama hedefi" if index == 0 else f"Araştırma {index}"
            result = self.store.gonder(self.author, {
                "packet": self.packet(title=title), "expected_version": 0,
                "idempotency_key": f"pagination-synthetic-{index:04d}"})
            if index == 0:
                wanted = result["card"]["id"]
            self.now[0] += 1
        self.store.gonder(self.other, {"packet": self.packet(title="Diğer hesabın kaydı"),
                                     "expected_version": 0, "idempotency_key": "pagination-other-0001"})
        for route, client, total, final_offset in (("mine", self.client, 105, 100),
                                                   ("review", moderator, 106, 101)):
            path = "/api/community/" + route
            page = client.get(path, params={"offset": final_offset, "limit": 24})
            self.assertEqual(page.status_code, 200, page.text)
            self.assertEqual(page.json()["total"], total)
            self.assertEqual(len(page.json()["items"]), 5)
            self.assertIn(wanted, [item["id"] for item in page.json()["items"]])
            found = client.get(path, params={"query": "HEDEFİ ÖLÇÜMLER", "limit": 24})
            self.assertEqual(found.status_code, 200, found.text)
            self.assertEqual(found.json()["total"], 1)
            self.assertEqual(found.json()["items"][0]["id"], wanted)
            self.assertEqual(client.get(path, params={"query": "missing word"}).json()["total"], 0)
            self.assertEqual(client.get(path, params={"query": "x" * 201}).status_code, 422)
            self.assertEqual(client.get(path, params={"offset": 9223372036854775808}).status_code, 422)
        self.assertEqual(self.client.get("/api/community/review", params={"query": "hedefi"}).status_code, 403)
        anonymous = self.client_for()
        self.assertEqual(anonymous.get("/api/community/mine", params={"offset": 100}).status_code, 401)
        self.assertEqual(anonymous.get("/api/community/cards", params={"query": "hedefi"}).json()["total"], 0)

    def test_arama_kelimelerin_sirasindan_bagimsiz_normalize_and_eslesmesi_kullanir(self):
        self.login()
        moderator = self.login("reviewer", self.client_for())
        wanted = self.review(self.submit(self.packet(title="Python mimarisi", body="ÖLÇÜMLER güvenilir kaynaklardan.")),
                             client=moderator)
        self.review(self.submit(self.packet(title="Yalnız Python", body="Ayrı bir konu."), key="search-one-word-0001"),
                    client=moderator)
        for query in ("ölçümler python", "PYTHON   ÖLÇÜMLER", "python ölçümler python"):
            response = self.client.get("/api/community/cards", params={"query": query, "kind": "research"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["total"], 1)
            self.assertEqual(response.json()["items"][0]["id"], wanted["id"])
        self.assertEqual(self.client.get("/api/community/cards", params={"query": "python missing"}).json()["total"], 0)

    def test_arama_and_eslesmesi_yayindaki_kartin_ozel_yeni_surumunu_sizdirmaz(self):
        self.login()
        published = self.review(self.submit(self.packet(title="Python raporu", body="Kamusal ölçümler.")))
        pending = self.post(f'/api/community/submissions/{published["id"]}', {
            "packet": self.packet(title="Python raporu", body="Gizli nebula araştırması."),
            "expected_version": published["version"], "idempotency_key": "private-query-revision-0001"})
        self.assertEqual(pending.status_code, 200, pending.text)
        self.submit(self.packet(title="Özel Python nebula raporu"), key="private-query-new-0001")
        anonymous = self.client_for()
        for query in ("nebula python", "python gizli"):
            for view in ("full", "summary"):
                result = anonymous.get("/api/community/cards", params={"query": query, "kind": "research", "view": view}).json()
                self.assertEqual(result["total"], 0)
                self.assertEqual(result["items"], [])
        public = anonymous.get("/api/community/cards", params={"query": "ölçümler python"}).json()
        self.assertEqual(public["total"], 1)
        self.assertEqual(public["items"][0]["packet"]["body"], "Kamusal ölçümler.")

    def test_oturum_suresi_ve_giris_istek_siniri(self):
        self.login()
        token = self.client.cookies.get(COOKIE)
        self.now[0] += 28801
        self.assertIsNone(self.store.oturum_oku(token))
        self.assertFalse(self.session()["authenticated"])
        for index in range(6):
            response = self.post("/api/community/login", {"username": "missing", "password": "incorrect-password"})
            self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(self.post("/api/community/login", {"username": "missing", "password": "incorrect-password"}).status_code, 429)

    def test_yedek_geri_yukleme_parola_ve_oturum_gizliligi(self):
        self.login()
        card = self.review(self.submit())
        token = self.client.cookies.get(COOKIE)
        with self.store.baglanti() as connection:
            stored_hash = connection.execute("SELECT password_hash FROM community_accounts WHERE id=?", (self.author["id"],)).fetchone()[0]
            tokens = [row[0] for row in connection.execute("SELECT token_hash FROM community_sessions")]
        self.assertTrue(parola_dogrula(PASSWORD, stored_hash))
        self.assertNotIn(PASSWORD, stored_hash)
        self.assertNotIn(token, tokens)
        backup = self.root / "backup.sqlite3"
        restored = self.root / "restored.sqlite3"
        self.store.yedekle(backup)
        with self.assertRaises(ValueError):
            self.store.yedekle(backup)
        veritabani_kopyala(backup, restored, restore=True)
        reopened = ToplulukDeposu(restored, clock=lambda: self.now[0])
        self.assertIsNone(reopened.oturum_oku(token))
        self.assertEqual(reopened.kimlik_dogrula("author", PASSWORD)["id"], self.author["id"])
        self.assertEqual(reopened.yayini_oku(card["id"])["packet"], self.packet())

    def test_http_pilot_acik_bayrak_ve_loopback_gerektirir(self):
        with self.assertRaises(ValueError):
            uygulama(self.root / "insecure.sqlite3", public_url="http://community.example.org", sources=[])
        with self.assertRaises(ValueError):
            uygulama(self.root / "external.sqlite3", public_url="http://community.example.org:8782", local_pilot=True, sources=[])
        local = uygulama(self.root / "local.sqlite3", public_url="http://127.0.0.1:8782", local_pilot=True,
                         feed_db_path=self.root / "local-feed.sqlite3", sources=[])
        with TestClient(local, base_url="http://127.0.0.1:8782") as client:
            response = client.get("/api/community/session")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("; Secure", response.headers["set-cookie"])

    def test_cli_parolayi_parametre_ve_ciktiya_koymaz(self):
        from topluluk import main
        output = io.StringIO()
        path = self.root / "cli.sqlite3"
        with patch("topluluk.getpass.getpass", side_effect=[PASSWORD, PASSWORD]), redirect_stdout(output):
            result = main(["--db", str(path), "account-add", "invited", "--display-name", "Davetli geliştirici"])
        self.assertEqual(result, 0)
        self.assertNotIn(PASSWORD, output.getvalue())
        self.assertEqual(ToplulukDeposu(path).kimlik_dogrula("invited", PASSWORD)["role"], "contributor")

    def test_parola_yenileme_ve_hesap_kapatma_oturumlari_iptal_eder(self):
        self.login()
        old_token = self.client.cookies.get(COOKIE)
        replacement = "replacement-test-passphrase"
        self.store.parola_yenile("author", replacement)
        self.assertIsNone(self.store.oturum_oku(old_token))
        self.assertEqual(self.post("/api/community/login", {"username": "author", "password": PASSWORD}).status_code, 401)
        self.assertEqual(self.post("/api/community/login", {"username": "author", "password": replacement}).status_code, 200)
        active_token = self.client.cookies.get(COOKIE)
        self.store.hesap_kapat("author")
        self.assertIsNone(self.store.oturum_oku(active_token))
        self.assertEqual(self.post("/api/community/login", {"username": "author", "password": replacement}).status_code, 401)

    def test_cli_parola_gizlenemeyen_terminalde_devam_etmez(self):
        from topluluk import gizli_parola_oku
        import getpass
        with patch("topluluk.getpass.getpass", side_effect=getpass.GetPassWarning("test")), self.assertRaises(ValueError):
            gizli_parola_oku()

    def test_saglik_urun_surumu_ve_topluluk_modunu_gosterir(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["product"], "DevTrellis")
        self.assertEqual(response.json()["mode"], "community")

    def test_moderator_yayini_gerekceyle_kaldirir_ve_sahip_yeniden_incelemeye_gonderir(self):
        self.login()
        moderator = self.login("reviewer", self.client_for())
        card = self.review(self.submit(), client=moderator)
        path = f'/api/community/submissions/{card["id"]}/hide'
        reason = "Kaynak iddiası hatalı; yeniden kontrol gerekli."
        response = self.post(path, {"expected_version": card["version"], "note": reason}, moderator)
        self.assertEqual(response.status_code, 200, response.text)
        hidden = response.json()["card"]
        self.assertEqual(hidden["status"], "hidden")
        self.assertIsNone(hidden["published_version"])
        anonymous = self.client_for()
        self.assertEqual(anonymous.get(f'/api/community/cards/{card["id"]}').status_code, 404)
        self.assertEqual(anonymous.get(f'/api/community/cards/{card["id"]}/packet').status_code, 404)
        self.assertEqual(anonymous.get("/api/community/cards").json()["total"], 0)
        with self.store.baglanti() as connection:
            audit = connection.execute("SELECT * FROM community_decisions WHERE entry_id=? AND decision='hide'",
                                       (card["id"],)).fetchone()
        self.assertEqual(audit["note"], reason)
        self.assertEqual(audit["moderator_id"], self.moderator["id"])
        self.assertEqual(audit["revision"], card["published_version"])
        resubmitted = self.post(f'/api/community/submissions/{card["id"]}',
                               {"packet": self.packet(title="Düzeltilmiş araştırma"), "expected_version": hidden["version"],
                                "idempotency_key": "hidden-resubmit-0001"})
        self.assertEqual(resubmitted.status_code, 200)
        self.assertEqual(resubmitted.json()["card"]["status"], "pending")
        self.assertIsNone(resubmitted.json()["card"]["published_version"])
        self.assertEqual(anonymous.get(f'/api/community/cards/{card["id"]}').status_code, 404)

    def test_yayindan_kaldirma_yetki_surum_ve_gerekce_gerektirir(self):
        self.login()
        moderator = self.login("reviewer", self.client_for())
        card = self.review(self.submit(), client=moderator)
        path = f'/api/community/submissions/{card["id"]}/hide'
        self.assertEqual(self.post(path, {"expected_version": card["version"], "note": "Gerekçe"}).status_code, 403)
        public = self.client_for().get(f'/api/community/cards/{card["id"]}').json()
        self.assertNotIn("moderation_version", public)
        moderator_view = moderator.get(f'/api/community/cards/{card["id"]}').json()
        self.assertEqual(moderator_view["moderation_version"], card["version"])
        listed = moderator.get("/api/community/cards").json()["items"][0]
        self.assertEqual(listed["moderation_version"], card["version"])
        self.assertEqual(self.post(path, {"expected_version": card["version"] - 1, "note": "Gerekçe"}, moderator).status_code, 409)
        self.assertEqual(self.post(path, {"expected_version": card["version"], "note": " "}, moderator).status_code, 422)
        self.assertEqual(self.client_for().get(f'/api/community/cards/{card["id"]}').status_code, 200)

    def test_moderator_kendi_yayinini_kaldirabilir(self):
        moderator = self.login("reviewer")
        own = self.submit(client=moderator)
        self.store.hesap_ekle("second-reviewer", "İkinci moderatör", "moderator", PASSWORD)
        second = self.login("second-reviewer", self.client_for())
        published = self.review(own, client=second)
        response = self.post(f'/api/community/submissions/{own["id"]}/hide',
                             {"expected_version": published["version"], "note": "Kendi kaynağımda hata buldum."}, moderator)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["card"]["status"], "hidden")

    def inbox_headers(self, token="A" * 43, **extra):
        return {"X-DevTrellis-Inbox": "1", "Authorization": f"Bearer {token}", **extra}

    def inbox_payload(self, **changes):
        value = {"packet": self.packet(author="Anonymous contributor"), "consent": True,
                 "idempotency_key": "anonymous-delivery-0001"}
        value.update(changes)
        return value

    def test_hesapsiz_giris_ozel_kopyayi_kaydetmeden_reddeder(self):
        for field, value in (("author", "Private person"), ("body", "mail person" + "@example.org")):
            payload = self.inbox_payload()
            payload["packet"][field] = value
            response = self.inbox_post(payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(value, response.text)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_entries").fetchone()[0], 0)

    def inbox_post(self, payload=None, token="A" * 43):
        return self.client_for().post("/api/community/inbox", json=payload or self.inbox_payload(),
                                      headers=self.inbox_headers(token))

    def test_hesapsiz_teslim_yalniz_moderasyonla_acilir_ve_kimlik_dogrulamasi_sayilmaz(self):
        response = self.inbox_post()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(response.headers["cache-control"], "no-store")
        submission = response.json()["submission"]
        self.assertEqual(submission["status"], "pending")
        self.assertEqual(submission["version"], 1)
        self.assertNotIn("packet", submission)
        entry_id = submission["id"]
        anonymous = self.client_for()
        self.assertEqual(anonymous.get(f"/api/community/cards/{entry_id}").status_code, 404)
        self.assertEqual(anonymous.get("/api/community/cards").json()["total"], 0)
        moderator = self.login("reviewer", self.client_for())
        review = moderator.get("/api/community/review").json()["items"][0]
        self.assertEqual(review["submission_type"], "anonymous")
        self.assertEqual(review["owner"], {"username": None, "display_name": "Hesapsız katkı", "verified": False})
        self.review(review, client=moderator)
        published = anonymous.get(f"/api/community/cards/{entry_id}").json()
        self.assertEqual(published["packet"]["author"], "Anonymous contributor")
        self.assertFalse(published["owner"]["verified"])
        self.assertNotIn("moderation_note", published)
        self.assertNotIn("capability", json.dumps(published))
        self.assertEqual(anonymous.get(f"/api/community/cards/{entry_id}/packet").json(), self.inbox_payload()["packet"])
        status = anonymous.get(f"/api/community/inbox/{entry_id}", headers=self.inbox_headers())
        self.assertEqual(status.json()["submission"]["status"], "published")

    def test_hesapsiz_teslim_tarayici_ve_mevcut_mutasyon_sinirlarini_acmaz(self):
        path = "/api/community/inbox"
        client = self.client_for()
        for forbidden in ({"Origin": ORIGIN}, {"Origin": "https://evil.example"}, {"Cookie": "anything=1"},
                          {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Mode": "cors"}):
            response = client.post(path, json=self.inbox_payload(), headers=self.inbox_headers(**forbidden))
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(client.post(path, json=self.inbox_payload()).status_code, 403)
        self.assertEqual(client.post(path, json=self.inbox_payload(), headers={"X-DevTrellis-Inbox": "1"}).status_code, 401)
        preflight = client.options(path, headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        self.assertEqual(preflight.status_code, 403)
        self.assertNotIn("access-control-allow-origin", preflight.headers)
        self.assertEqual(client.post("/api/community/submissions", json={"packet": self.packet(), "expected_version": 0,
            "idempotency_key": "no-session-0001"}, headers=self.inbox_headers()).status_code, 403)
        self.assertEqual(client.post("/api/community/logout", json={}, headers=self.inbox_headers()).status_code, 403)
        self.assertEqual(client.get("/api/community/inbox/invalid", headers=self.inbox_headers(Origin=ORIGIN)).status_code, 403)
        self.assertEqual(client.post(path, json=self.inbox_payload(), headers=self.inbox_headers(Host="evil.example")).status_code, 403)
        self.assertEqual(client.post("http://community.example.org" + path, json=self.inbox_payload(),
                                     headers=self.inbox_headers()).status_code, 400)

    def test_hesapsiz_gecersiz_riza_paket_ve_buyuk_govde_hicbir_kayit_acmaz(self):
        invalid = [self.inbox_payload(consent=False), self.inbox_payload(consent=1),
                   self.inbox_payload(packet=self.packet(url="http://127.0.0.1/private")),
                   self.inbox_payload(packet=self.packet(owner={"id": "reviewer"})),
                   self.inbox_payload(packet=self.packet(sources=[{"title": "x", "url": "https://example.org", "secret": "x"}])),
                   self.inbox_payload(packet=self.packet(body="界" * 12000)),
                   self.inbox_payload(owner="reviewer"), self.inbox_payload(idempotency_key="bad")]
        for payload in invalid:
            response = self.inbox_post(payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.now[0] += 61
        self.now[0] += 61
        response = self.client_for().post("/api/community/inbox", content=b"x" * 65537,
            headers=self.inbox_headers(**{"Content-Type": "application/json"}))
        self.assertEqual(response.status_code, 413)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_entries").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_inbox").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_accounts").fetchone()[0], 3)

    def test_hesapsiz_tekrar_dedup_surum_ve_yonetim_yetkisi(self):
        first = self.inbox_post().json()["submission"]
        duplicate = self.inbox_post(self.inbox_payload(idempotency_key="new-retry-key-0001"))
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["duplicate"])
        self.assertEqual(duplicate.json()["submission"], first)
        changed = self.inbox_post(self.inbox_payload(packet=self.packet(title="Başka araştırma", author="Anonymous contributor")))
        self.assertEqual(changed.status_code, 409)
        client = self.client_for()
        path = f'/api/community/inbox/{first["id"]}'
        self.assertEqual(client.get(path, headers=self.inbox_headers("B" * 43)).status_code, 404)
        self.assertEqual(client.get(path).status_code, 403)
        self.assertEqual(client.post(path + "/withdraw", json={"expected_version": 1},
                                     headers=self.inbox_headers("B" * 43)).status_code, 404)
        self.assertEqual(client.post(path + "/withdraw", json={"expected_version": 0},
                                     headers=self.inbox_headers()).status_code, 409)
        self.assertEqual(client.post(path + "/withdraw", json={"expected_version": 1},
                                     headers=self.inbox_headers()).json()["submission"]["status"], "withdrawn")
        duplicate = self.inbox_post().json()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["submission"]["status"], "withdrawn")
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_entries").fetchone()[0], 1)

    def test_hesapsiz_yayin_geri_cekilir_moderasyon_notu_yalniz_yonetim_anahtarina_acilir(self):
        submission = self.inbox_post().json()["submission"]
        moderator = self.login("reviewer", self.client_for())
        reviewed = self.review(submission, note="Özel inceleme notu", client=moderator)
        path = f'/api/community/inbox/{submission["id"]}'
        anonymous = self.client_for()
        public = anonymous.get(f'/api/community/cards/{submission["id"]}').json()
        self.assertNotIn("Özel inceleme notu", json.dumps(public, ensure_ascii=False))
        status = anonymous.get(path, headers=self.inbox_headers()).json()["submission"]
        self.assertEqual(status["moderation_note"], "Özel inceleme notu")
        result = anonymous.post(path + "/withdraw", json={"expected_version": reviewed["version"]},
                                headers=self.inbox_headers())
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(result.json()["submission"]["published_version"])
        self.assertEqual(anonymous.get(f'/api/community/cards/{submission["id"]}/packet').status_code, 404)
        self.assertEqual(anonymous.get("/api/community/cards").json()["total"], 0)

    def test_hesapsiz_kuyruk_siniri_atomik_ve_tekrar_teslim_dolu_kuyrukta_guvenli(self):
        from concurrent.futures import ThreadPoolExecutor
        with patch("radar.topluluk_depo.INBOX_PENDING_LIMIT", 1):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: self.store.inbox_gonder("A" * 43, self.inbox_payload()), range(2)))
            self.assertEqual(sorted(result["duplicate"] for result in results), [False, True])
            full = self.inbox_post(token="B" * 43)
            self.assertEqual(full.status_code, 503, full.text)
            first = results[0]["submission"]
            self.store.inbox_geri_cek("A" * 43, first["id"], {"expected_version": 1})
            with patch("radar.topluluk_depo.INBOX_TOTAL_LIMIT", 1):
                self.assertEqual(self.inbox_post(token="B" * 43).status_code, 503)
            accepted = self.inbox_post(token="B" * 43)
            self.assertEqual(accepted.status_code, 201, accepted.text)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_inbox").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_entries").fetchone()[0], 2)

    def test_hesapsiz_ip_dakika_gun_siniri_ve_proxy_basligini_kendisi_guvenilir_saymaz(self):
        client = self.client_for()
        for index in range(4):
            self.assertIn(self.inbox_post().status_code, (200, 201))
        blocked = client.post("/api/community/inbox", json=self.inbox_payload(),
                              headers=self.inbox_headers(**{"X-Forwarded-For": "203.0.113.99"}))
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.headers["retry-after"], "60")
        for batch in range(4):
            self.now[0] += 61
            for index in range(4):
                self.assertEqual(self.inbox_post().status_code, 200)
        self.now[0] += 61
        daily = self.inbox_post()
        self.assertEqual(daily.status_code, 429)
        self.assertEqual(daily.headers["retry-after"], "86400")
        self.assertEqual(client.get("/api/community/cards").status_code, 200)
        self.now[0] += 86400
        self.assertEqual(self.inbox_post().status_code, 200)

    def test_hesapsiz_yonetim_anahtari_gizli_kalir_ve_yedekten_durum_korunur(self):
        token = "S" * 43
        packet = self.packet(schema_version=2, author="Anonymous contributor", provenance={
            "model": "example-recorded-model", "provider": "nvidia", "generated_at": None,
            "question": "Kaynaklı araştırma", "topic": "ai", "source_url": "",
            "evidence_status": "unverified", "language": "en", "modified": True})
        response = self.inbox_post(self.inbox_payload(packet=packet), token)
        self.assertEqual(response.status_code, 201, response.text)
        submission = response.json()["submission"]
        self.assertNotIn(token, response.text)
        with self.store.baglanti() as connection:
            saved = connection.execute("SELECT * FROM community_inbox").fetchone()
            dump = "\n".join(connection.iterdump())
            account = connection.execute("SELECT * FROM community_accounts WHERE id='!devtrellis-inbox'").fetchone()
            self.assertEqual(account["active"], 0)
            self.assertNotIn(token, dump)
            self.assertNotIn("anonymous-delivery-0001", dump)
            self.assertEqual(len(saved["capability_hash"]), 64)
        backup, restored = self.root / "inbox-backup.sqlite3", self.root / "inbox-restored.sqlite3"
        self.store.yedekle(backup)
        veritabani_kopyala(backup, restored, restore=True)
        reopened = ToplulukDeposu(restored, clock=lambda: self.now[0])
        self.assertEqual(reopened.inbox_oku(token, submission["id"])["submission"], submission)
        with reopened.baglanti() as connection:
            self.assertEqual(reopened.kart(connection, submission["id"])["packet"], packet)

    def test_eski_topluluk_verisi_hesapsiz_tablo_eklenirken_korunur(self):
        self.login()
        original = self.review(self.submit())
        with self.store.baglanti(write=True) as connection:
            connection.execute("DROP TABLE community_inbox")
        reopened = ToplulukDeposu(self.store.path, clock=lambda: self.now[0])
        self.assertEqual(reopened.yayini_oku(original["id"])["packet"], self.packet())
        self.assertEqual(reopened.kimlik_dogrula("author", PASSWORD)["id"], self.author["id"])
        self.assertEqual(reopened.inbox_gonder("A" * 43, self.inbox_payload())["submission"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()

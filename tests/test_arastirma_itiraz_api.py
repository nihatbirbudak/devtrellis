"""Özel itiraz HTTP akışını gerçek oturumlar, SQLite ve kapalı varsayılanla sınar."""

import io
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import topluluk
from radar.arastirma_itiraz_api import PREFIX, RECEIPT_HEADER, itiraz_depo_yolunu_dogrula
from radar.arastirma_itirazlari import ItirazHatasi
from radar.kaynak_hafizasi import KaynakHafizasi, KaynakTanimi
from radar.topluluk_api import uygulama


ORIGIN = "https://community.example.org"
PASSWORD = "example-test-passphrase"


class ArastirmaItirazAPITestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = [1793779200]
        self.path = self.root / "private-objections" / "objections.sqlite3"
        self.app = uygulama(self.root / "community.sqlite3", public_url=ORIGIN, sources=[],
                            objections_db_path=self.path, clock=lambda: self.now[0])
        self.store = self.app.state.community
        self.author = self.store.hesap_ekle("author", "Katkıcı", "contributor", PASSWORD)
        self.reviewer = self.store.hesap_ekle("reviewer", "Moderatör", "moderator", PASSWORD)
        self.original = self.publish()
        self.receipt = secrets.token_urlsafe(32)
        self.client = self.client_for()

    def client_for(self):
        client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(client.close)
        return client

    def packet(self, **changes):
        return {"schema": "ufuk-share", "schema_version": 1, "kind": "research", "title": "Sınırları açıklanan araştırma",
                "summary": "Kamu kaynaklarının incelenmesi.", "body": "Kaynağın ölçümünde sürüm sınırı vardır.",
                "url": "https://example.org/project", "tags": ["Python"],
                "sources": [{"title": "Birincil kaynak", "url": "https://example.org/docs"}],
                "author": "Anonymous contributor", "license": "CC-BY-4.0",
                "created_at": "2026-11-01T12:00:00+00:00", "updated_at": "2026-11-01T12:00:00+00:00", **changes}

    def publish(self, previous=None):
        card = self.store.gonder(self.author,
            {"packet": self.packet(body="Düzeltme yayımlandı." if previous else "Kaynak sürümü sınırlıdır."),
             "expected_version": previous["version"] if previous else 0,
             "idempotency_key": secrets.token_hex(12)}, previous["id"] if previous else None)["card"]
        return self.store.karar(self.reviewer, card["id"],
            {"expected_version": card["version"], "decision": "publish", "note": "Kaynak kontrol edildi."})["card"]

    def headers(self, client=None, receipt=None):
        client = client or self.client
        csrf = client.get("/api/community/session").json()["csrf_token"]
        return {"Origin": ORIGIN, "X-Ufuk-CSRF": csrf,
                RECEIPT_HEADER: receipt if receipt is not None else self.receipt}

    def post(self, suffix="", payload=None, *, client=None, headers=None, receipt=None):
        client = client or self.client
        return client.post(PREFIX + suffix, json={} if payload is None else payload,
                           headers=self.headers(client, receipt) if headers is None else headers)

    def payload(self, **changes):
        return {"research_id": self.original["id"], "version": self.original["published_version"],
                "claim": "Ölçüm her sürüme uygulanabilir.", "reason": "Kaynakta bu sonuç yalnız belirtilen sürüm için açıklanmıştır.",
                "sources": [{"title": "Ölçüm kapsamı", "url": "https://example.org/measurement"}],
                "consent": True, **changes}

    def submit(self):
        response = self.post(payload=self.payload())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["objection"]

    def login(self, name="reviewer"):
        client = self.client_for()
        response = client.post("/api/community/login", json={"username": name, "password": PASSWORD}, headers=self.headers(client))
        self.assertEqual(response.status_code, 200, response.text)
        return client

    def decision(self, objection, moderator, **changes):
        payload = {"expected_revision": objection["revision"], "research_id": self.original["id"],
                   "version": self.original["published_version"], "status": "in_review", **changes}
        return self.post(f'/review/{objection["id"]}/decision', payload, client=moderator)

    def public(self, **params):
        return self.client_for().get(f'{PREFIX}/corrections/{self.original["id"]}/{self.original["published_version"]}', params=params)

    def test_lazy_anonymous_pending_has_no_public_private_text_or_receipt(self):
        self.assertFalse(self.path.exists())
        objection = self.submit()
        self.assertEqual(objection["status"], "pending")
        self.assertNotIn("user", objection)
        public = self.public()
        self.assertEqual(public.status_code, 200, public.text)
        self.assertEqual(public.json()["items"], [])
        self.assertNotIn("set-cookie", public.headers)
        self.assertEqual(self.post("/read").json()["reason"], self.payload()["reason"])
        self.assertEqual(self.post("/read").headers["cache-control"], "no-store")
        self.assertEqual(self.post("/read").headers["referrer-policy"], "no-referrer")
        self.assertNotIn(self.receipt.encode(), self.path.read_bytes())
        for result in (objection, public.json(), self.post("/read").json()):
            self.assertNotIn(self.receipt, json.dumps(result))

    def test_same_origin_session_csrf_and_no_machine_exemption(self):
        self.assertEqual(self.post(payload=self.payload(), headers={"Origin": ORIGIN, RECEIPT_HEADER: self.receipt}).status_code, 401)
        headers = self.headers()
        for changed in ({"Origin": ORIGIN, RECEIPT_HEADER: self.receipt},
                        {key: value for key, value in headers.items() if key != "Origin"},
                        {**headers, "Origin": "https://foreign.example.org"},
                        {**headers, "Sec-Fetch-Site": "cross-site"},
                        {"X-DevTrellis-Client": "1", RECEIPT_HEADER: self.receipt},
                        {"X-DevTrellis-Inbox": "1", RECEIPT_HEADER: self.receipt},
                        {**headers, "Host": "foreign.example.org"}):
            with self.subTest(header_names=list(changed)):
                self.assertEqual(self.post(payload=self.payload(), headers=changed).status_code, 403)
        self.assertFalse(self.path.exists())

    def test_private_reads_and_withdrawal_also_require_csrf_origin_and_receipt(self):
        objection = self.submit()
        headers = self.headers()
        for suffix, payload in (("/read", {}), ("/withdraw", {"expected_revision": objection["revision"]})):
            for drop in ("Origin", "X-Ufuk-CSRF", RECEIPT_HEADER):
                changed = {key: value for key, value in headers.items() if key != drop}
                result = self.post(suffix, payload, headers=changed)
                self.assertEqual(result.status_code, 401 if drop == RECEIPT_HEADER else 403, result.text)
            self.assertEqual(self.client.get(PREFIX + suffix).status_code, 405)
        self.assertEqual(self.post("/read", receipt=secrets.token_urlsafe(32)).status_code, 404)
        self.assertEqual(self.post("/read", receipt="invalid").status_code, 401)
        self.assertEqual(self.post("/read").json()["status"], "pending")

    def test_repeat_same_receipt_is_one_record_changed_body_or_version_is_conflict(self):
        first = self.submit()
        repeated = self.post(payload=self.payload())
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json()["duplicate"])
        self.assertEqual(repeated.json()["objection"]["id"], first["id"])
        for payload in (self.payload(reason="Başka bir gerekçe."), self.payload(version=999)):
            self.assertEqual(self.post(payload=payload).status_code, 409)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM research_objections").fetchone()[0], 1)

    def test_receipt_and_private_fields_are_rejected_in_body_query_and_errors(self):
        private = "private.person@example.com"
        payloads = [self.payload(user={"role": "moderator"}), self.payload(receipt=self.receipt),
                    self.payload(context=private), self.payload(reason=private), self.payload(consent=False),
                    self.payload(version=True), [private]]
        for payload in payloads:
            # Rate periods are advanced; this test checks validation rather than the separate limit.
            self.now[0] += 61
            response = self.post(payload=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn(private, response.text)
            self.assertNotIn(self.receipt, response.text)
        response = self.client.post(PREFIX, content='{"private":"' + private,
                                    headers={**self.headers(), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(private, response.text)
        for suffix in ("", "/read", "/withdraw"):
            response = self.post(suffix + "?receipt=" + self.receipt, self.payload())
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.receipt, response.text)
        bad_version = self.client.get(PREFIX + "/corrections/" + self.original["id"] + "/" + private)
        self.assertEqual(bad_version.status_code, 422)
        self.assertNotIn(private, bad_version.text)
        self.assertFalse(self.path.exists())

    def test_only_real_active_moderator_can_list_read_or_decide(self):
        objection = self.submit()
        detail = PREFIX + "/review/" + objection["id"]
        for client, expected in ((self.client, 401), (self.login("author"), 403)):
            self.assertEqual(client.get(PREFIX + "/review", headers={"X-Role": "moderator"}).status_code, expected)
            self.assertEqual(client.get(detail).status_code, expected)
            self.assertEqual(self.decision(objection, client).status_code, expected)
        moderator = self.login()
        listed = moderator.get(PREFIX + "/review").json()
        self.assertEqual(listed["total"], 1)
        self.assertNotIn("reason", listed["items"][0])
        self.assertEqual(moderator.get(detail).json()["reason"], self.payload()["reason"])
        self.store.hesap_kapat("reviewer")
        self.assertEqual(moderator.get(detail).status_code, 401)
        self.assertEqual(self.decision(objection, moderator).status_code, 401)

    def test_acceptance_is_exact_version_and_public_only_after_explicit_moderator_summary(self):
        objection = self.submit()
        moderator = self.login()
        first = self.decision(objection, moderator)
        self.assertEqual(first.status_code, 200, first.text)
        reviewing = first.json()
        corrected = self.publish(self.original)
        summary = "Kaynak ölçümünün sürüm sınırı düzeltildi; yeni sürüme bakın."
        kwargs = {"status": "accepted", "public_summary": summary,
                  "correction": {"research_id": corrected["id"], "version": corrected["published_version"]}}
        self.assertEqual(self.decision(reviewing, moderator, version=999, **kwargs).status_code, 409)
        headers = self.headers(moderator)
        headers.pop("X-Ufuk-CSRF")
        denied = self.post(f'/review/{objection["id"]}/decision',
                           {"expected_revision": reviewing["revision"], "research_id": self.original["id"],
                            "version": self.original["published_version"], **kwargs}, client=moderator, headers=headers)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.public().json()["items"], [])
        accepted = self.decision(reviewing, moderator, **kwargs)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        result = self.public().json()
        old_card_path = f'/api/community/cards/{self.original["id"]}?version={self.original["published_version"]}'
        old_card = self.client_for().get(old_card_path).json()
        self.assertEqual(old_card["public_corrections"]["items"], result["items"])
        self.assertEqual(self.client_for().get(f'/api/community/cards/{self.original["id"]}').json()["public_corrections"]["items"], [])
        self.assertEqual(result["items"][0]["summary"], summary)
        self.assertEqual(set(result["items"][0]), {"id", "summary", "withdrawn", "correction", "reviewed_at"})
        for private in (self.payload()["claim"], self.payload()["reason"], self.receipt, self.reviewer["id"]):
            self.assertNotIn(private, json.dumps(result, ensure_ascii=False))
        withdrawn = self.post("/withdraw", {"expected_revision": accepted.json()["revision"]})
        self.assertEqual(withdrawn.status_code, 200, withdrawn.text)
        self.assertTrue(self.public().json()["items"][0]["withdrawn"])
        self.assertTrue(self.client_for().get(old_card_path).json()["public_corrections"]["items"][0]["withdrawn"])
        self.assertEqual(self.post("/read").json()["reason"], "")
        self.assertEqual(self.public().json()["items"][0]["summary"], summary)

    def test_research_lists_full_and_summary_carry_at_most_three_public_corrections(self):
        packet = self.packet(schema_version=3, title="Geliştirilmiş ölçüm incelemesi", lineage={
            "parents": [{"research_id": self.original["id"], "version": self.original["published_version"]}],
            "change_summary": "Ölçümün sürüm kapsamı açıklandı."})
        child = self.store.gonder(self.author, {"packet": packet, "expected_version": 0,
                                               "idempotency_key": secrets.token_hex(12)})["card"]
        child = self.store.karar(self.reviewer, child["id"], {"expected_version": child["version"], "decision": "publish"})["card"]
        objections = self.app.state.objections
        for i in range(4):
            record = objections.gonder(secrets.token_urlsafe(32), self.payload(reason=f"Kapsamın incelenmesi için gerekçe {i}."))["objection"]
            reviewing = objections.karar(self.reviewer, record["id"], {
                "expected_revision": 1, "research_id": self.original["id"], "version": self.original["published_version"], "status": "in_review"})
            objections.karar(self.reviewer, record["id"], {"expected_revision": reviewing["revision"],
                "research_id": self.original["id"], "version": self.original["published_version"], "status": "accepted",
                "public_summary": f"Sürüm kapsamını açıklayan yayımlı düzeltme {i}.",
                "correction": {"research_id": child["id"], "version": child["published_version"]}})
        for view in ("full", "summary"):
            response = self.client_for().get("/api/community/cards", params={"kind": "research", "view": view,
                                             "query": "araştırma", "search_mode": "ranked"})
            self.assertEqual(response.status_code, 200, response.text)
            card = next(item for item in response.json()["items"] if item["id"] == self.original["id"])
            corrections = card["public_corrections"]
            self.assertEqual(set(corrections), {"total", "offset", "limit", "items"})
            self.assertEqual((corrections["total"], corrections["offset"], corrections["limit"], len(corrections["items"])), (4, 0, 3, 3))
            for item in corrections["items"]:
                self.assertEqual(set(item), {"id", "summary", "withdrawn", "correction", "reviewed_at"})
                self.assertNotIn("gerekçe", json.dumps(item, ensure_ascii=False))
            self.assertEqual("body" in card["packet"], view == "full")
        second_page = self.public(offset=3, limit=3).json()
        self.assertEqual(len(second_page["items"]), 1)

    def test_disabled_feature_preserves_public_card_and_list_shapes(self):
        disabled = uygulama(self.root / "community.sqlite3", public_url=ORIGIN, sources=[], clock=lambda: self.now[0])
        with TestClient(disabled, base_url=ORIGIN) as client:
            card = client.get(f'/api/community/cards/{self.original["id"]}').json()
            self.assertNotIn("public_corrections", card)
            for view in ("full", "summary"):
                listing = client.get("/api/community/cards", params={"kind": "research", "view": view}).json()
                self.assertNotIn("public_corrections", listing["items"][0])
            self.assertEqual(client.get(f'/api/community/cards/{self.original["id"]}/packet').json(), card["packet"])
        self.assertFalse(self.path.exists())

    def test_pending_withdrawal_is_private_and_repeat_cannot_recreate_it(self):
        objection = self.submit()
        result = self.post("/withdraw", {"expected_revision": objection["revision"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.post("/read").json()["claim"], "")
        self.assertEqual(self.public().json()["total"], 0)
        repeated = self.post(payload=self.payload())
        self.assertTrue(repeated.json()["duplicate"])
        self.assertEqual(repeated.json()["objection"]["status"], "withdrawn")
        self.assertEqual(self.post("/withdraw", {"expected_revision": 1}).status_code, 409)

    def test_hidden_or_failed_public_reader_fails_closed_without_private_exception(self):
        self.store.geri_cek(self.author, self.original["id"], {"expected_version": self.original["version"]})
        self.assertEqual(self.post(payload=self.payload()).status_code, 409)
        self.assertEqual(self.public().status_code, 409)
        with patch.object(self.app.state.objections, "published_report", side_effect=RuntimeError("private server path")):
            response = self.post(payload=self.payload())
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("private server path", response.text)

    def test_read_pagination_validation_cannot_echo_query_or_private_payload(self):
        self.submit()
        moderator = self.login()
        for params in ({"limit": 101}, {"offset": -1}, {"status": "private.person@example.com"},
                       {"receipt": self.receipt}, [("limit", 1), ("limit", 2)]):
            result = moderator.get(PREFIX + "/review", params=params)
            self.assertEqual(result.status_code, 422, result.text)
            self.assertNotIn(self.receipt, result.text)
            self.assertNotIn("private.person", result.text)
        result = self.public(receipt=self.receipt)
        self.assertEqual(result.status_code, 422)
        self.assertNotIn(self.receipt, result.text)
        self.assertEqual(self.public(limit=101).status_code, 422)

    def test_body_and_submit_rate_limits_are_bounded(self):
        response = self.client.post(PREFIX, content=json.dumps({"padding": "x" * 17000}),
                                    headers={**self.headers(), "Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.path.exists())
        self.submit()
        for _ in range(3):
            self.assertEqual(self.post(payload=self.payload()).status_code, 200)
        limited = self.post(payload=self.payload())
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["retry-after"], "60")
        # Reading/withdrawing an existing receipt does not consume the submit quota.
        self.assertEqual(self.post("/read").status_code, 200)
        self.assertEqual(self.post("/withdraw", {"expected_revision": 1}).status_code, 200)

    def test_foreign_database_is_rejected_without_touching_its_content(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE foreign_data(value TEXT)")
            db.execute("INSERT INTO foreign_data VALUES('preserve me')")
            db.commit()
        original = self.path.read_bytes()
        result = self.post(payload=self.payload())
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertNotIn(str(self.path), result.text)

    def test_safe_store_errors_preserve_http_status_without_echoing_unexpected_details(self):
        with patch.object(self.app.state.objections, "gonder", side_effect=ItirazHatasi("Geçici depolama sorunu.", 503)):
            self.assertEqual(self.post(payload=self.payload()).status_code, 503)
        with patch.object(self.app.state.objections, "gonder", side_effect=ValueError("private server details")):
            response = self.post(payload=self.payload())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private server details", response.text)


class ArastirmaItirazYapilandirmaTestleri(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.community = self.root / "community.sqlite3"
        self.feed = self.root / "feed.sqlite3"
        self.path = self.root / "private" / "objections.sqlite3"

    def cli(self, *extra):
        return topluluk.main(["--db", str(self.community), "serve", "--public-url", ORIGIN,
                              "--feed-db", str(self.feed), *map(str, extra)])

    def test_default_disabled_has_no_objection_route_state_or_database(self):
        with patch("radar.arastirma_itiraz_api.ItirazDeposu") as constructor, patch("uvicorn.run") as server, redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli(), 0)
        constructor.assert_not_called()
        app = server.call_args.args[0]
        self.assertFalse(hasattr(app.state, "objections"))
        self.assertFalse(self.path.exists())
        with TestClient(app, base_url=ORIGIN) as client:
            self.assertEqual(client.get(PREFIX + "/review").status_code, 404)
            csrf = client.get("/api/community/session").json()["csrf_token"]
            self.assertEqual(client.post(PREFIX, json={}, headers={"Origin": ORIGIN, "X-Ufuk-CSRF": csrf}).status_code, 404)
        self.assertFalse(server.call_args.kwargs["access_log"])

    def test_opt_in_registers_lazy_separate_store_and_has_no_network_or_model_work(self):
        with patch("uvicorn.run") as server, patch("radar.kaynak_hafizasi.kosullu_oku") as network, redirect_stdout(io.StringIO()):
            self.assertEqual(self.cli("--objections-db", self.path), 0)
        network.assert_not_called()
        self.assertEqual(server.call_args.args[0].state.objections.path, self.path)
        self.assertFalse(self.path.exists())
        self.assertTrue(self.community.exists())
        self.assertTrue(self.feed.exists())

    def test_same_file_and_shared_directory_rejected_before_core_files_or_memory_creation(self):
        for conflict in (self.community, self.feed, self.root / "same-directory.sqlite3"):
            with self.subTest(conflict=conflict), redirect_stderr(io.StringIO()), patch("uvicorn.run") as server, self.assertRaises(SystemExit):
                self.cli("--objections-db", conflict)
            server.assert_not_called()
            self.assertFalse(self.community.exists())
            self.assertFalse(self.feed.exists())
        with redirect_stderr(io.StringIO()), patch("radar.kaynak_hafizasi_api.kaynak_hafizasini_hazirla") as memory, self.assertRaises(SystemExit):
            self.cli("--objections-db", self.path, "--source-memory-db", self.path, "--source-memory-config", self.root / "config.json")
        memory.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_programmatic_source_memory_collision_rejected_before_community_schema(self):
        memory = KaynakHafizasi(self.path, [KaynakTanimi("pilot", "https://blog.youtube/news-and-events/source-memory-pilot/")], fetcher=Mock())
        with self.assertRaises(ItirazHatasi):
            uygulama(self.community, public_url=ORIGIN, feed_db_path=self.feed, sources=[], source_memory=memory, objections_db_path=self.path)
        self.assertFalse(self.community.exists())
        self.assertFalse(self.feed.exists())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='research_objections'").fetchone()[0], 0)

    def test_hardlink_samefile_is_rejected_without_modifying_existing_file(self):
        self.community.write_bytes(b"existing unrelated database")
        self.path.parent.mkdir()
        os.link(self.community, self.path)
        with self.assertRaises(ItirazHatasi):
            itiraz_depo_yolunu_dogrula(self.path, self.community, self.feed)
        self.assertEqual(self.community.read_bytes(), b"existing unrelated database")
        self.assertFalse(self.feed.exists())


if __name__ == "__main__":
    unittest.main()

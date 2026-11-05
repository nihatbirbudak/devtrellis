"""Hesapsız araştırma paylaşımında açık onayı, gizliliği, tekrar kullanımını ve hata sonrası kurtarmayı doğrular."""

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar.depo import Depo
from radar.platform_api import router_olustur
from radar.topluluk_api import uygulama
from radar.topluluk_baglantisi import COMMUNITY_URL, ToplulukBaglantisi, ToplulukBaglantiHatasi
from tests.test_platform_api import paket


class Kasa:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


class ToplulukBaglantisiTestleri(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.depo = Depo(self.root / "personal.sqlite3", [])
        self.remote = uygulama(self.root / "community.sqlite3", public_url=COMMUNITY_URL, sources=[])
        self.remote_client = TestClient(self.remote, base_url=COMMUNITY_URL)
        self.addCleanup(self.remote_client.close)
        self.calls = []
        self.vault = Kasa()
        self.approvals = {}
        self.time = 1000

        def exchange(request):
            self.calls.append(request)
            response = self.remote_client.request(request.method, str(request.url), content=request.read(), headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content, headers=dict(response.headers))

        self.transport = httpx.MockTransport(exchange)
        self.bridge = ToplulukBaglantisi(self.depo, transport=self.transport, vault=self.vault, clock=lambda: self.time)
        self.packet = paket()

    def prepare(self, **changes):
        return self.bridge.onizle({"packet": self.packet, "author": "Anonymous contributor", **changes})

    def send(self, preview):
        preview_id = preview["preview_id"]
        if preview_id not in self.approvals:
            self.approvals[preview_id] = self.bridge.inceleme_ac(preview_id)["approval_token"]
        return self.bridge.gonder({"preview_id": preview_id, "consent": True, "reviewed_public": True,
                                  "approval_token": self.approvals[preview_id]})

    def test_preview_is_local_anonymous_and_decline_or_expiry_never_sends(self):
        before = copy.deepcopy(self.packet)
        preview = self.prepare()
        self.assertEqual(self.calls, [])
        self.assertEqual(preview["packet"]["author"], "Anonymous contributor")
        self.assertEqual(self.packet, before)
        with self.assertRaises(ValueError):
            self.bridge.gonder({"preview_id": preview["preview_id"], "consent": False})
        self.time += 1801
        with self.assertRaises(ValueError):
            self.send(preview)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.values, {})

    def test_send_exact_preview_is_idempotent_and_no_secret_enters_local_db(self):
        preview = self.prepare()
        first = self.send(preview)
        repeated = self.send(preview)
        self.assertEqual(first["submission"]["status"], "pending")
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(first["submission"]["id"], repeated["submission"]["id"])
        sent = json.loads(self.calls[0].read())
        self.assertEqual(sent["packet"], preview["packet"])
        self.assertNotIn("cookie", self.calls[0].headers)
        self.assertNotIn("origin", self.calls[0].headers)
        self.assertEqual(self.remote.state.community.listele()["total"], 0)
        with self.depo.baglanti() as connection:
            persisted = "\n".join(connection.iterdump())
        for token in self.vault.values.values():
            self.assertNotIn(token, persisted)
            self.assertNotIn(token, json.dumps(first))
        restarted = ToplulukBaglantisi(self.depo, transport=self.transport, vault=self.vault)
        self.assertEqual(restarted.yonet(first["local_id"])["submission"]["status"], "pending")
        withdrawn = restarted.yonet(first["local_id"], {"expected_version": first["submission"]["version"]})
        self.assertEqual(withdrawn["submission"]["status"], "withdrawn")
        before = len(self.calls)
        self.assertEqual(self.send(preview)["submission"], first["submission"])
        self.assertEqual(len(self.calls), before)
        self.assertEqual(restarted.yonet(first["local_id"])["submission"]["status"], "withdrawn")

    def test_only_approved_text_sent_even_if_returned_preview_or_source_changes(self):
        preview = self.prepare()
        approved = copy.deepcopy(preview["packet"])
        preview["packet"]["body"] = "NOT_THE_APPROVED_SNAPSHOT"
        self.packet["body"] = "LATER_LOCAL_CHANGE"
        self.send(preview)
        self.assertEqual(json.loads(self.calls[0].read())["packet"], approved)

    def test_simultaneous_instances_share_one_capability_and_preserve_withdrawal(self):
        class RacingVault(Kasa):
            def __init__(self):
                super().__init__()
                self.lock = threading.Lock()
                self.first_reader = None
                self.second_reader = threading.Event()
                self.writes = 0

            def get(self, name):
                with self.lock:
                    value = self.values.get(name)
                    reader = threading.get_ident()
                    if self.first_reader is None:
                        self.first_reader = reader
                    elif reader != self.first_reader:
                        self.second_reader.set()
                # Kilit yoksa iki oturumun da aynı eski boş kasa değerini okumasını görünür kılar.
                if value is None and reader == self.first_reader:
                    self.second_reader.wait(0.25)
                return value

            def set(self, name, value):
                with self.lock:
                    self.writes += 1
                    self.values[name] = value

        vault = RacingVault()
        peers = [ToplulukBaglantisi(Depo(self.depo.path, []), vault=vault, transport=self.transport)
                 for _ in range(2)]
        payloads = []
        for peer in peers:
            preview = peer.onizle({"packet": self.packet})
            review = peer.inceleme_ac(preview["preview_id"])
            payloads.append({"preview_id": preview["preview_id"], "approval_token": review["approval_token"],
                             "consent": True, "reviewed_public": True})
        self.assertNotEqual(payloads[0]["approval_token"], payloads[1]["approval_token"])
        exchange = self.transport.handler
        network = threading.Barrier(2)

        def simultaneous_delivery(request):
            # İki ağ isteğinin aynı anda ilerlemesi DB kilidinin HTTP sırasında tutulmadığını da doğrular.
            network.wait(timeout=3)
            return exchange(request)

        for peer in peers:
            peer.transport = httpx.MockTransport(simultaneous_delivery)
        results, errors = {}, []

        def send(index):
            try:
                results[index] = peers[index].gonder(payloads[index])
            except Exception as error:
                errors.append(type(error).__name__)

        workers = [threading.Thread(target=send, args=(index,)) for index in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(6)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(vault.writes, 1)
        self.assertEqual(len(vault.values), 1)
        self.assertEqual(self.calls[0].headers["authorization"], self.calls[1].headers["authorization"])
        self.assertEqual(results[0]["submission"]["id"], results[1]["submission"]["id"])
        self.assertEqual(sorted(item["duplicate"] for item in results.values()), [False, True])
        self.assertEqual(len(peers[0].listele()["items"]), 1)
        with self.remote.state.community.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM community_inbox").fetchone()[0], 1)
        for peer in peers:
            peer.transport = self.transport
        withdrawn = peers[0].yonet(results[0]["local_id"], {"expected_version": 1})
        self.assertEqual(withdrawn["submission"]["status"], "withdrawn")
        self.assertEqual(peers[1].yonet(results[1]["local_id"])["submission"]["status"], "withdrawn")

    def test_common_secrets_are_removed_before_preview_and_sending(self):
        secret = "sk-" + "EXAMPLE" * 5
        self.packet["body"] = "Contact private@example.org and " + secret + " at C:\\Users\\Example\\secret.txt and https://example.org/doc?api_key=hidden-key and https://example.org/#access_token=hidden-fragment"
        self.packet["url"] = "https://example.org/report?utm_source=tracking"
        preview = self.prepare()
        text = json.dumps(preview)
        for private in (secret, "private@example.org", "secret.txt", "token=private", "hidden-key", "hidden-fragment"):
            self.assertNotIn(private, text)
        self.assertGreaterEqual(preview["redactions"], 4)
        self.assertEqual(preview["packet"]["url"], "https://example.org/report")
        self.send(preview)
        self.assertEqual(json.loads(self.calls[0].read())["packet"], preview["packet"])

    def test_edit_question_and_clear_claim_keep_private_context_and_original_research_local(self):
        from radar.sunucu import uygulama as personal_app
        from tests.test_tam_uygulama import kaynak, haber

        provider = Mock(side_effect=AssertionError("A privacy preview must not call a model"))
        fetcher = Mock(side_effect=AssertionError("A privacy preview must not fetch sources"))
        app = personal_app(self.depo.path, sources=[kaynak("privacy-fixture", "ai")], seed=False,
                           ai_provider=provider, article_fetcher=fetcher, credential_provider=lambda: None)
        with TestClient(app):
            app.state.depo.kaydet("privacy-fixture", [haber(1)])
            item_id = app.state.depo.gorunum()["items"][0]["id"]
            private_name = "PRIVATE_PERSON_NAME_712"
            private_claim = "PRIVATE_PROJECT_CLAIM_385"
            private_context = "PRIVATE_TRANSCRIPT_429 stays in the research request."
            request = app.state.research_requests.olustur(
                question=f"Can {private_name} use this navigation method?", topic="ai",
                source_url="https://example.org/public-post", claim_text=private_claim,
                context_text=private_context, need="PRIVATE_RESEARCH_NEED_462",
                scope=["PRIVATE_PROJECT_SCOPE_297"], idempotency_key="privacy-question-claim-0001")
            task = app.state.workflow.kuyruga_al(item_id, request_id=request["id"])
            report = {"title_tr": f"Navigation research for {private_name}", "summary_tr": "A public summary.",
                      "evidence_quality": {"status": "verified", "coverage_complete": True}}
            with self.depo.baglanti(write=True) as connection:
                source = connection.execute("SELECT id,title,url,revision FROM items WHERE id=?", (item_id,)).fetchone()
                references = [{"item_id": source["id"], "source_revision": source["revision"],
                               "title": source["title"], "url": source["url"], "body_hash": None}]
                connection.execute("UPDATE workflow_tasks SET status='ready',result_json=?,report_text=?,report_model=?,"
                                   "report_generated_at=?,sources_json=? WHERE id=?",
                    (json.dumps(report), "Only the selected public report.", "recorded-model-privacy-test",
                     "2026-11-04T10:00:00+00:00", json.dumps(references), task["id"]))
            self.bridge.workflow = app.state.workflow
            original_task = copy.deepcopy(app.state.workflow.oku(task["id"]))
            original_request = copy.deepcopy(app.state.research_requests.oku(request["id"]))
            initial = self.bridge.onizle({"task_id": task["id"]})
            self.assertEqual(initial["packet"]["provenance"]["question"], initial["packet"]["title"])
            self.assertNotIn("claim_text", initial["packet"]["provenance"])
            # Anlamsal ad tanıma iddiası yoktur; insan kamu başlığındaki bu adı kaldırabilir.
            self.assertIn(private_name, initial["packet"]["title"])
            self.assertNotIn(original_request["question"], json.dumps(initial))
            self.assertNotIn(private_claim, json.dumps(initial))
            self.assertNotIn(private_context, json.dumps(initial))

            cleaned_question = "What independent evidence supports autonomous navigation?"
            edited = self.bridge.onizle({"task_id": task["id"], "title": "Public navigation research",
                                        "question": cleaned_question, "claim_text": ""})
            self.assertNotEqual(initial["preview_id"], edited["preview_id"])
            self.assertEqual(edited["packet"]["title"], "Public navigation research")
            provenance = edited["packet"]["provenance"]
            self.assertEqual(provenance["question"], edited["packet"]["title"])
            self.assertNotIn("claim_text", provenance)
            self.assertTrue(provenance["modified"])
            self.assertEqual(provenance["evidence_status"], "unverified")
            self.assertEqual(provenance["model"], initial["packet"]["provenance"]["model"])
            self.assertEqual(provenance["generated_at"], "2026-11-04T10:00:00+00:00")
            self.assertEqual(edited["packet"]["body"], "Only the selected public report.")
            self.assertEqual(self.calls, [])
            self.assertEqual(self.vault.values, {})
            sent = self.send(edited)
            outgoing = json.loads(self.calls[0].read())
            self.assertEqual(outgoing["packet"], edited["packet"])
            self.assertEqual(sent["submission"]["status"], "pending")
            with self.remote.state.community.baglanti() as connection:
                remote_dump = "\n".join(connection.iterdump())
            shared_text = json.dumps(outgoing) + remote_dump
            for private in (private_name, private_claim, private_context, "PRIVATE_RESEARCH_NEED_462", "PRIVATE_PROJECT_SCOPE_297"):
                self.assertNotIn(private, shared_text)
            for private_field in ("context_text", "link_access", "profile_snapshot", "request_id", "chat_history"):
                self.assertNotIn(private_field, outgoing["packet"])
                self.assertNotIn(private_field, provenance)
            self.assertEqual(app.state.workflow.oku(task["id"]), original_task)
            self.assertEqual(app.state.research_requests.oku(request["id"]), original_request)
            draft = self.bridge.area.arastirmadan_taslak(original_task)["entry"]
            self.assertEqual(draft["provenance"]["question"], original_request["question"])
            self.assertEqual(draft["provenance"]["claim_text"], private_claim)
            provider.assert_not_called()
            fetcher.assert_not_called()

    def test_question_claim_edits_validate_bounds_and_reject_private_context_fields_over_http(self):
        provenance = {"model": "recorded-model", "provider": "unknown", "generated_at": None,
                      "question": "A question with a private name", "claim_text": "A private claim",
                      "topic": "ai", "source_url": "https://example.org/public-post", "language": "en",
                      "evidence_status": "verified", "modified": False}
        packet = paket(schema_version=2, provenance=provenance, author="Chosen public alias", license="CC0-1.0")
        before = copy.deepcopy(packet)
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "csrf-test", community=self.bridge))
        with TestClient(app) as client:
            path, headers = "/api/platform/community/preview", {"X-Radar-CSRF": "csrf-test"}
            invalid = [dict(title=""), dict(title="x" * 161), dict(title="Invalid\ntitle"),
                       dict(question=""), dict(question="x" * 501), dict(claim_text="x" * 1001),
                       dict(claim_text=None), dict(context_text="PRIVATE_CONTEXT_FIELD_341")]
            for change in invalid:
                response = client.post(path, json={"packet": packet, **change}, headers=headers)
                self.assertEqual(response.status_code, 422, response.text)
            nested = copy.deepcopy(packet)
            nested["provenance"]["context_text"] = "PRIVATE_NESTED_CONTEXT_675"
            self.assertEqual(client.post(path, json={"packet": nested}, headers=headers).status_code, 422)
            self.assertEqual(client.post(path, json={"packet": self.packet, "question": "No provenance to edit"},
                                         headers=headers).status_code, 422)
            accepted = client.post(path, json={"packet": packet, "title": "Public topic contact private@example.org",
                                               "question": "Public research question", "claim_text": ""},
                                   headers=headers)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            cleaned = accepted.json()["packet"]
            self.assertEqual(cleaned["title"], "Public topic contact [redacted]")
            self.assertEqual(cleaned["provenance"]["question"], cleaned["title"])
            self.assertNotIn("claim_text", cleaned["provenance"])
            self.assertEqual(cleaned["provenance"]["evidence_status"], "unverified")
            self.assertTrue(cleaned["provenance"]["modified"])
            self.assertEqual(cleaned["license"], "CC0-1.0")
            self.assertEqual(cleaned["author"], "Anonymous contributor")
        self.assertEqual(packet, before)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.values, {})

    def test_local_lineage_entry_preview_preserves_schema_three_over_http(self):
        from radar.gelistirici_alani import KAYIT_ALANLARI

        lineage = {"parents": [{"research_id": "a" * 32, "version": 2}],
                   "change_summary": "New primary-source evidence added."}
        provenance = {"question": "Private original question", "model": "unknown", "provider": "unknown",
                      "generated_at": None, "topic": "software", "language": "en", "source_url": "",
                      "evidence_status": "unverified", "modified": False}
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "csrf-test", community=self.bridge))
        with TestClient(app) as client:
            for metadata in (None, provenance):
                with self.subTest(has_provenance=metadata is not None):
                    packet = {**self.packet, "schema_version": 3, "lineage": lineage}
                    if metadata is not None:
                        packet["provenance"] = metadata
                    entry = self.bridge.area.olustur({key: value for key, value in packet.items()
                                                     if key in KAYIT_ALANLARI})
                    response = client.post("/api/platform/community/preview", json={"entry_id": entry["id"]},
                                           headers={"X-Radar-CSRF": "csrf-test"})
                    self.assertEqual(response.status_code, 200, response.text)
                    public = response.json()["packet"]
                    self.assertEqual(public["schema_version"], 3)
                    self.assertEqual(public["lineage"], lineage)
                    self.assertEqual(public["author"], "Anonymous contributor")
                    self.assertNotIn("Private original question", json.dumps(public))
                    self.assertEqual(self.bridge.area.oku(entry["id"]), entry)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.values, {})

    def test_vault_failure_does_not_send_or_fall_back_to_plaintext(self):
        class Broken(Kasa):
            def set(self, name, value):
                raise ValueError("No safe vault")
        self.bridge.vault = Broken()
        with self.assertRaises(ValueError):
            self.send(self.prepare())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.bridge.listele()["items"], [])

    def test_lost_response_retries_same_capability_without_duplicate(self):
        exchange = self.transport.handler
        def dropped(request):
            response = exchange(request)
            raise httpx.ReadError("private response error", request=request)
        self.bridge.transport = httpx.MockTransport(dropped)
        preview = self.prepare()
        with self.assertRaises(ToplulukBaglantiHatasi) as error:
            self.send(preview)
        self.assertNotIn("private response error", str(error.exception))
        self.bridge.transport = self.transport
        recovered = self.send(preview)
        self.assertTrue(recovered["duplicate"])
        self.assertEqual(len(self.vault.values), 1)

    def test_lost_response_recovers_after_restart_without_sending_the_report_again(self):
        exchange = self.transport.handler

        def dropped(request):
            exchange(request)
            raise httpx.ReadError("untrusted delivery detail", request=request)

        self.bridge.transport = httpx.MockTransport(dropped)
        preview = self.prepare()
        with self.assertRaises(ToplulukBaglantiHatasi):
            self.send(preview)
        local = self.bridge.listele()["items"][0]
        self.assertIsNone(local["submission"])
        restarted = ToplulukBaglantisi(self.depo, transport=self.transport, vault=self.vault)
        self.assertEqual(restarted.previews, {})
        before = len(self.calls)
        recovered = restarted.yonet(local["local_id"])
        self.assertEqual(recovered["submission"]["status"], "pending")
        self.assertEqual(len(self.calls), before + 1)
        self.assertEqual(self.calls[-1].method, "GET")
        self.assertEqual(self.calls[-1].url.path, "/api/community/inbox/status")
        self.assertEqual(self.calls[-1].content, b"")
        self.assertEqual(self.calls[-1].url.query, b"")
        self.assertEqual(self.calls[-1].headers["authorization"], self.calls[0].headers["authorization"])
        with self.remote.state.community.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM community_inbox").fetchone()[0], 1)
        self.assertEqual(restarted.yonet(local["local_id"], {"expected_version": 1})["submission"]["status"], "withdrawn")

    def test_unreceived_or_unsupported_recovery_keeps_unknown_delivery_without_resubmitting(self):
        self.bridge.transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"detail": "test unavailable"}))
        with self.assertRaises(ToplulukBaglantiHatasi):
            self.send(self.prepare())
        local = self.bridge.listele()["items"][0]
        reads = []

        def legacy_server(request):
            reads.append(request)
            return httpx.Response(404, json={"detail": "private remote response"})

        restarted = ToplulukBaglantisi(self.depo, transport=httpx.MockTransport(legacy_server), vault=self.vault)
        with self.assertRaises(ToplulukBaglantiHatasi) as caught:
            restarted.yonet(local["local_id"])
        self.assertNotIn("private remote response", str(caught.exception))
        self.assertEqual([(request.method, request.content) for request in reads], [("GET", b"")])
        self.assertIsNone(restarted.listele()["items"][0]["submission"])
        with self.remote.state.community.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM community_inbox").fetchone()[0], 0)

    def test_recovery_lookup_requires_own_capability_and_rejects_browser_context(self):
        sent = self.send(self.prepare())
        path = "/api/community/inbox/status"
        token = next(iter(self.vault.values.values()))
        headers = {"X-DevTrellis-Inbox": "1", "Authorization": "Bearer " + token}
        response = self.remote_client.get(path, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["submission"], sent["submission"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(token, response.text)
        self.assertNotIn("packet", response.json())
        self.assertEqual(self.remote_client.get(path).status_code, 403)
        self.assertEqual(self.remote_client.get(path, headers={"X-DevTrellis-Inbox": "1"}).status_code, 401)
        self.assertEqual(self.remote_client.get(path, headers={**headers, "Authorization": "Bearer " + "x" * 43}).status_code, 404)
        for browser in ({"Origin": COMMUNITY_URL}, {"Cookie": "example=synthetic"}, {"Sec-Fetch-Site": "same-origin"}):
            with self.subTest(browser=browser):
                self.assertEqual(self.remote_client.get(path, headers={**headers, **browser}).status_code, 403)

    def test_ranked_match_metadata_and_legacy_server_fallback(self):
        sent = self.send(self.prepare())
        store = self.remote.state.community
        reviewer = store.hesap_ekle("reviewer", "Reviewer", "moderator", "a-long-test-password")
        store.karar(reviewer, sent["submission"]["id"], {"expected_version": 1, "decision": "publish", "note": "Reviewed"})
        result = self.bridge.ara({"query": "Ortak", "limit": 3, "view": "summary", "search_mode": "ranked"})
        self.assertEqual(result["search"], {"mode": "ranked", "fallback": False})
        self.assertEqual(result["items"][0]["search_match"], {"matched_terms": ["ortak"], "missing_terms": [],
                         "all_terms_matched": True, "answer_sufficiency": "not_assessed"})
        self.assertEqual(self.calls[-1].url.params["search_mode"], "ranked")
        full = self.bridge.ara({"query": "Ortak", "limit": 3, "view": "full", "search_mode": "ranked"})
        expected = copy.deepcopy(full)
        expected["items"][0]["packet"].pop("body")
        expected["view"] = "summary"
        self.assertEqual(result, expected)
        self.assertEqual(self.remote_client.get("/api/community/cards", params={"search_mode": "invalid"}).status_code, 422)
        legacy = self.remote_client.get("/api/community/cards", params={"query": "Ortak"}).json()
        with patch.object(self.bridge, "request", return_value=legacy):
            fallback = self.bridge.ara({"query": "Ortak", "search_mode": "ranked"})
        self.assertEqual(fallback["search"], {"mode": "all", "fallback": True})
        for change in ({"answer_sufficiency": "verified"}, {"all_terms_matched": False},
                       {"missing_terms": ["ortak"]}, {"matched_terms": ["a"] * 25}):
            broken = copy.deepcopy(result)
            broken["items"][0]["search_match"].update(change)
            with self.subTest(change=change), patch.object(self.bridge, "request", return_value=broken):
                with self.assertRaises(ToplulukBaglantiHatasi):
                    self.bridge.ara({"query": "Ortak", "view": "summary", "search_mode": "ranked"})

    def test_search_read_import_reuses_published_research_without_model(self):
        sent = self.send(self.prepare())
        store = self.remote.state.community
        store.hesap_ekle("reviewer", "Reviewer", "moderator", "a-long-test-password")
        moderator = store.kimlik_dogrula("reviewer", "a-long-test-password")
        store.karar(moderator, sent["submission"]["id"], {"expected_version": 1, "decision": "publish", "note": "Reviewed"})
        result = self.bridge.ara({"query": "Ortak", "limit": 5})
        self.assertEqual(result["total"], 1)
        card = self.bridge.oku(sent["submission"]["id"])
        self.assertEqual(card["packet"]["body"], self.packet["body"])
        self.assertEqual(result["items"][0]["report_url"], card["report_url"])
        self.assertEqual(card["report_url"], COMMUNITY_URL + "/?card=" + card["id"] + "&version=" + str(card["version"]))
        imported = self.bridge.ice_aktar(sent["submission"]["id"])
        self.assertEqual(imported["entry"]["origin"], "imported")
        with self.assertRaises(ValueError):
            self.bridge.onizle({"entry_id": imported["entry"]["id"]})
        self.assertTrue(self.bridge.ice_aktar(sent["submission"]["id"])["duplicate"])

    def test_summary_search_reaches_remote_and_preserves_metadata_with_old_server_fallback(self):
        sent = self.send(self.prepare())
        store = self.remote.state.community
        reviewer = store.hesap_ekle("reviewer", "Reviewer", "moderator", "a-long-test-password")
        store.karar(reviewer, sent["submission"]["id"], {"expected_version": 1, "decision": "publish", "note": "Reviewed"})
        full = self.bridge.ara({"query": "Ortak", "limit": 3})
        summary = self.bridge.ara({"query": "Ortak", "limit": 3, "view": "summary"})
        self.assertEqual(self.calls[-1].url.params["view"], "summary")
        expected = copy.deepcopy(full)
        expected["items"][0]["packet"].pop("body")
        expected["view"] = "summary"
        self.assertEqual(summary, expected)
        raw_full = self.remote_client.get("/api/community/cards", params={"query": "Ortak", "limit": 3}).json()
        with patch.object(self.bridge, "request", return_value=raw_full) as request:
            self.assertEqual(self.bridge.ara({"query": "Ortak", "limit": 3, "view": "summary"}), expected)
            self.assertEqual(request.call_args.kwargs["params"]["view"], "summary")
        broken = copy.deepcopy(raw_full)
        broken["items"][0]["packet"]["body"] = ["invalid full body"]
        with patch.object(self.bridge, "request", return_value=broken), self.assertRaises(ToplulukBaglantiHatasi):
            self.bridge.ara({"query": "Ortak", "view": "summary"})
        # Özet istemi normal okuma, içeri alma veya paylaşım doğrulamasını gevşetmez.
        with patch.object(self.bridge, "request", return_value=summary["items"][0]), self.assertRaises(ToplulukBaglantiHatasi):
            self.bridge.oku(sent["submission"]["id"])
        for field in ("license", "sources", "created_at", "summary"):
            broken = copy.deepcopy(summary)
            broken["items"][0]["packet"].pop(field)
            with self.subTest(field=field), patch.object(self.bridge, "request", return_value=broken):
                with self.assertRaises(ToplulukBaglantiHatasi):
                    self.bridge.ara({"query": "Ortak", "view": "summary"})
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "csrf-test", community=self.bridge))
        with TestClient(app) as client:
            body = {"query": "Ortak", "limit": 3, "view": "summary"}
            path = "/api/platform/community/search"
            self.assertEqual(client.post(path, json=body).status_code, 403)
            response = client.post(path, json=body, headers={"X-Radar-CSRF": "csrf-test"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            for view in ("unknown", True, None, []):
                self.assertEqual(client.post(path, json={**body, "view": view},
                                            headers={"X-Radar-CSRF": "csrf-test"}).status_code, 422)

    def test_report_links_use_validated_exact_version_and_ignore_remote_url(self):
        identifier = "a" * 32
        remote = {"id": identifier, "status": "published", "version": 2, "published_version": 7,
                  "created_at": self.packet["created_at"], "updated_at": self.packet["updated_at"],
                  "packet": self.packet, "report_url": "https://attacker.example/steal?token=untrusted"}
        expected = COMMUNITY_URL + "/?card=" + identifier + "&version=2"
        for requested_view in ("full", "summary"):
            result = {"items": [copy.deepcopy(remote)], "total": 1}
            if requested_view == "summary":
                result["view"] = "summary"
                result["items"][0]["packet"].pop("body")
            with self.subTest(view=requested_view), patch.object(self.bridge, "request", return_value=result):
                card = self.bridge.ara({"query": "Public", "view": requested_view})["items"][0]
                self.assertEqual(card["report_url"], expected)
                self.assertEqual(card["published_version"], 7)
                self.assertNotIn("attacker.example", json.dumps(card))
        with patch.object(self.bridge, "request", return_value=remote) as request:
            card = self.bridge.oku(identifier, version=2)
            self.assertEqual(card["report_url"], expected)
            self.assertEqual(request.call_args.kwargs["params"], {"version": 2})
        self.assertEqual(remote["report_url"], "https://attacker.example/steal?token=untrusted")

    def test_moderator_hiding_is_visible_to_sender_and_invalid_response_not_saved(self):
        sent = self.send(self.prepare())
        submission = sent["submission"]
        hidden = {**submission, "status": "hidden"}
        self.assertEqual(self.bridge.kaydet(sent["local_id"], {"submission": hidden})["submission"]["status"], "hidden")
        for broken in ([], {**submission, "version": {}}, {**submission, "moderation_note": "x" * 2001}, {**submission, "created_at": "tomorrow"}):
            with self.assertRaises(ToplulukBaglantiHatasi):
                self.bridge.kaydet(sent["local_id"], {"submission": broken})
        self.assertEqual(self.bridge.listele()["items"][0]["submission"]["status"], "hidden")

    def test_untrusted_remote_size_redirect_and_arbitrary_target_rejected(self):
        for response in (httpx.Response(302, headers={"Location": "http://127.0.0.1/private"}),
                         httpx.Response(200, content=b"x" * (512 * 1024 + 1))):
            self.bridge.transport = httpx.MockTransport(lambda request: response)
            with self.assertRaises(ToplulukBaglantiHatasi):
                self.bridge.ara({"query": "test"})
        with self.assertRaises(ValueError):
            self.bridge.oku("https://127.0.0.1/private")

    def test_withdraw_invalid_version_never_leaves_local_machine(self):
        sent = self.send(self.prepare())
        before = len(self.calls)
        for invalid in (True, 0, -1, 1.0, "private-version-value", {"private": "context"},
                        [], None, 9223372036854775808):
            with self.subTest(value=invalid), self.assertRaises(ValueError) as caught:
                self.bridge.yonet(sent["local_id"], {"expected_version": invalid})
            self.assertNotIn("private-version-value", str(caught.exception))
            self.assertEqual(len(self.calls), before)

    def test_delayed_status_cannot_replace_newer_withdrawal_or_remote_identity(self):
        sent = self.send(self.prepare())
        pending = copy.deepcopy(sent["submission"])
        withdrawn = self.bridge.yonet(sent["local_id"], {"expected_version": pending["version"]})
        delayed = ToplulukBaglantisi(Depo(self.depo.path, []), transport=self.transport, vault=self.vault)
        result = delayed.kaydet(sent["local_id"], {"submission": pending})
        self.assertEqual(result["submission"], withdrawn["submission"])
        self.assertEqual(self.bridge.listele()["items"][0]["submission"], withdrawn["submission"])
        with self.assertRaises(ToplulukBaglantiHatasi):
            delayed.kaydet(sent["local_id"], {"submission": {**withdrawn["submission"], "id": "f" * 32}})
        self.assertEqual(self.bridge.yonet(sent["local_id"])["submission"], withdrawn["submission"])

    def test_withdrawal_conflict_preserves_snapshot_then_refresh_recovers(self):
        preview = self.prepare()
        self.assertEqual(self.calls, [])
        sent = self.send(preview)
        store = self.remote.state.community
        store.hesap_ekle("reviewer", "Reviewer", "moderator", "a-long-test-password")
        moderator = store.kimlik_dogrula("reviewer", "a-long-test-password")
        store.karar(moderator, sent["submission"]["id"], {"expected_version": 1, "decision": "publish", "note": "Reviewed"})
        found = self.bridge.ara({"query": "Ortak"})
        self.assertEqual(found["total"], 1)
        self.assertEqual(found["items"][0]["id"], sent["submission"]["id"])
        published = self.bridge.oku(sent["submission"]["id"])
        self.assertEqual(published["packet"], preview["packet"])
        with self.assertRaisesRegex(ToplulukBaglantiHatasi, "durumu değişti"):
            self.bridge.yonet(sent["local_id"], {"expected_version": 1})
        self.assertEqual(self.bridge.listele()["items"][0]["submission"], sent["submission"])
        current = self.bridge.yonet(sent["local_id"])["submission"]
        self.assertEqual(current["status"], "published")
        withdrawn = self.bridge.yonet(sent["local_id"], {"expected_version": current["version"]})
        self.assertEqual(withdrawn["submission"]["status"], "withdrawn")
        self.assertEqual(store.listele()["total"], 0)
        self.assertEqual(self.bridge.ara({"query": "Ortak"})["total"], 0)
        with self.assertRaises(ToplulukBaglantiHatasi):
            self.bridge.oku(sent["submission"]["id"])

    def test_local_api_csrf_guards_preview_send_and_search(self):
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "csrf-test", community=self.bridge))
        with TestClient(app) as client:
            for path, body in (("preview", {"packet": self.packet}), ("send", {"consent": True}), ("search", {"query": "hello"})):
                self.assertEqual(client.post("/api/platform/community/" + path, json=body).status_code, 403)
            preview = client.post("/api/platform/community/preview", json={"packet": self.packet}, headers={"X-Radar-CSRF": "csrf-test"})
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(self.calls, [])
            rejected = client.post("/api/platform/community/send", json={"preview_id": preview.json()["preview_id"], "consent": True, "packet": self.packet}, headers={"X-Radar-CSRF": "csrf-test"})
            self.assertEqual(rejected.status_code, 422)
            self.assertEqual(self.calls, [])

    def test_review_token_is_required_bound_to_snapshot_and_rotated_on_reopen(self):
        preview = self.prepare()
        self.assertNotIn("approval_token", preview)
        base = {"preview_id": preview["preview_id"], "consent": True, "reviewed_public": True}
        for additions in ({}, {"approval_token": "X" * 43}):
            with self.assertRaises(ValueError):
                self.bridge.gonder({**base, **additions})
        first = self.bridge.inceleme_ac(preview["preview_id"])
        second = self.bridge.inceleme_ac(preview["preview_id"])
        self.assertNotEqual(first["approval_token"], second["approval_token"])
        with self.assertRaises(ValueError):
            self.bridge.gonder({**base, "approval_token": first["approval_token"]})
        other = self.prepare(body="A distinct public copy.")
        with self.assertRaises(ValueError):
            self.bridge.gonder({**base, "preview_id": other["preview_id"], "approval_token": second["approval_token"]})
        for change in ({"reviewed_public": False}, {"reviewed_public": 1}, {"consent": False}):
            with self.assertRaises(ValueError):
                self.bridge.gonder({**base, "approval_token": second["approval_token"], **change})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.values, {})
        # İnceleme cevabını değiştirmek, saklanan ve onaylanan kamu kopyasını değiştiremez.
        second["packet"]["body"] = "NOT_THE_REVIEWED_PUBLIC_COPY"
        accepted = self.bridge.gonder({**base, "approval_token": second["approval_token"]})
        self.assertEqual(json.loads(self.calls[0].read())["packet"], preview["packet"])
        repeated = self.bridge.gonder({**base, "approval_token": second["approval_token"]})
        self.assertEqual(repeated["submission"], accepted["submission"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(len(self.calls), 1)
        with self.depo.baglanti() as connection:
            saved = "\n".join(connection.iterdump())
        self.assertNotIn(second["approval_token"], saved + json.dumps(accepted))

    def test_review_expiry_and_discard_revoke_approval_without_egress(self):
        preview = self.prepare()
        review = self.bridge.inceleme_ac(preview["preview_id"])
        control = {"preview_id": preview["preview_id"], "approval_token": review["approval_token"]}
        with self.assertRaises(ValueError):
            self.bridge.vazgec({**control, "approval_token": "X" * 43})
        self.assertEqual(self.bridge.vazgec(control), {"status": "kept_local"})
        with self.assertRaises(ValueError):
            self.bridge.inceleme_ac(preview["preview_id"])
        with self.assertRaises(ValueError):
            self.bridge.gonder({**control, "consent": True, "reviewed_public": True})
        next_preview = self.prepare()
        next_review = self.bridge.inceleme_ac(next_preview["preview_id"])
        self.time += 1801
        with self.assertRaises(ValueError):
            self.bridge.gonder({"preview_id": next_preview["preview_id"], "approval_token": next_review["approval_token"],
                               "consent": True, "reviewed_public": True})
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.values, {})
        self.assertEqual(self.bridge.listele()["items"], [])

    def test_unknown_delivery_cannot_be_misrepresented_as_kept_local(self):
        exchange = self.transport.handler
        def dropped(request):
            exchange(request)
            raise httpx.ReadError("untrusted remote details", request=request)
        self.bridge.transport = httpx.MockTransport(dropped)
        preview = self.prepare()
        with self.assertRaises(ToplulukBaglantiHatasi):
            self.send(preview)
        with self.assertRaises(ValueError):
            self.bridge.vazgec({"preview_id": preview["preview_id"], "approval_token": self.approvals[preview["preview_id"]]})
        self.bridge.transport = self.transport
        self.assertTrue(self.send(preview)["duplicate"])
        with self.assertRaises(ValueError):
            self.bridge.vazgec({"preview_id": preview["preview_id"], "approval_token": self.approvals[preview["preview_id"]]})

    def test_sensitive_queries_and_unsafe_source_urls_are_rejected_before_network(self):
        private_values = ("private@example.org", "+90 (555) 123 45 67", "192.168.1.20", "2001:db8::1",
                          "C:\\Users\\Person\\project", "/home/person/project", "project.internal", "token=private-value",
                          "https://name:password@example.org/path", "private%40example.org")
        for value in private_values:
            with self.subTest(value=value), self.assertRaises(ValueError) as error:
                self.bridge.ara({"query": "Research " + value})
            self.assertNotIn(value, str(error.exception))
        for address in ("http://example.org/source", "https://8.8.8.8/source", "https://example.org/?q=private",
                        "https://example.org/?token=private-value", "https://example.org/private%40example.org"):
            with self.subTest(address=address), self.assertRaises(ValueError) as error:
                self.bridge.onizle({"packet": paket(sources=[{"title": "Source", "url": address}])})
            self.assertNotIn(address, str(error.exception))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.vault.values, {})
        self.assertEqual(self.bridge.previews, {})

    def test_review_and_discard_http_require_csrf_and_boolean_alone_cannot_send(self):
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "csrf-test", community=self.bridge))
        with TestClient(app) as client:
            headers = {"X-Radar-CSRF": "csrf-test"}
            preview = client.post("/api/platform/community/preview", json={"packet": self.packet}, headers=headers).json()
            self.assertNotIn("approval_token", preview)
            path = "/api/platform/community/review/" + preview["preview_id"]
            self.assertEqual(client.get(path).status_code, 403)
            review = client.get(path, headers=headers)
            self.assertEqual(review.status_code, 200, review.text)
            self.assertEqual(review.headers["cache-control"], "no-store")
            base = {"preview_id": preview["preview_id"], "consent": True, "reviewed_public": True}
            self.assertEqual(client.post("/api/platform/community/send", json=base, headers=headers).status_code, 422)
            control = {"preview_id": preview["preview_id"], "approval_token": review.json()["approval_token"]}
            self.assertEqual(client.post("/api/platform/community/discard", json=control).status_code, 403)
            discarded = client.post("/api/platform/community/discard", json=control, headers=headers)
            self.assertEqual(discarded.json(), {"status": "kept_local"})
            self.assertEqual(client.post("/api/platform/community/send", json={**base, **control}, headers=headers).status_code, 422)
            self.assertEqual(client.get(path, headers=headers).status_code, 422)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()

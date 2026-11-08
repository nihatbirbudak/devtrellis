"""Araştırma atfının sürümünü, görünürlük sınırını ve kimliksiz sayaçların yarış koşullarını sınar."""

import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from radar.depo import Depo
from radar.gelistirici_alani import GelistiriciAlani, KAYIT_ALANLARI, paket_dogrula
from radar.paylasim_gizliligi import kamu_kopyasi
from radar.topluluk_api import uygulama
from radar.topluluk_depo import EVENT_COUNTERS, EVENT_RECEIPT_SECONDS, ToplulukDeposu, ToplulukHatasi
from tests.test_platform_api import paket


ORIGIN = "https://community.example.org"


class ArastirmaGelisimiTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.now = [1793779200]
        self.app = uygulama(self.root / "community.sqlite3", public_url=ORIGIN, sources=[], clock=lambda: self.now[0])
        self.store = self.app.state.community
        self.author = {"id": "a" * 32, "username": "author", "display_name": "Contributor", "role": "contributor"}
        self.other = {"id": "b" * 32, "username": "other", "display_name": "Other", "role": "contributor"}
        self.moderator = {"id": "c" * 32, "username": "moderator", "display_name": "Moderator", "role": "moderator"}
        with self.store.baglanti(write=True) as connection:
            for user in (self.author, self.other, self.moderator):
                connection.execute("INSERT INTO community_accounts VALUES(?,?,?,?,?,1,?)",
                    (user["id"], user["username"], user["display_name"], user["role"], self.store.dummy_hash, self.now[0]))
        self.client = self.client_for()

    def client_for(self):
        client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(client.close)
        return client

    def submit(self, packet=None, *, user=None, current=None):
        return self.store.gonder(user or self.author, {"packet": packet or paket(), "expected_version": current["version"] if current else 0,
            "idempotency_key": uuid.uuid4().hex}, current["id"] if current else None)["card"]

    def publish(self, card):
        return self.store.karar(self.moderator, card["id"], {"decision": "publish", "expected_version": card["version"]})["card"]

    def parent(self, **changes):
        return self.publish(self.submit(paket(**changes)))

    def derived(self, parent, **changes):
        return paket(schema_version=3, lineage={"parents": [{"research_id": parent["id"], "version": parent["published_version"]}],
                     "change_summary": "Independent measurements and new primary sources added."}, **changes)

    def event(self, card, kind="helpful", event_id=None, version=None):
        return {"kind": kind, "version": card["published_version"] if version is None else version,
                "event_id": event_id or uuid.uuid4().hex}

    def test_schema_three_requires_lineage_and_preserves_old_packets(self):
        source = self.parent()
        original = self.derived(source)
        self.assertEqual(paket_dogrula(original), original)
        self.assertEqual(paket_dogrula(paket())["schema_version"], 1)
        cases = [paket(schema_version=3), self.derived(source, kind="project"),
                 {**original, "schema_version": 1}, {**original, "lineage": None}]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                paket_dogrula(value)

    def test_lineage_rejects_private_extra_fields_invalid_versions_and_duplicates(self):
        original = self.derived(self.parent())
        parent = original["lineage"]["parents"][0]
        cases = [{"parents": [], "change_summary": "Added"}, {"parents": [parent] * 6, "change_summary": "Added"},
                 {"parents": [parent, parent], "change_summary": "Added"},
                 {"parents": [parent], "change_summary": " "}, {"parents": [parent], "change_summary": "x" * 601},
                 {"parents": [parent], "change_summary": "Added", "conversation": "private"}]
        cases.extend({"parents": [{**parent, "version": value}], "change_summary": "Added"}
                     for value in (True, 0, -1, 1.0, "1", 2**63))
        cases.extend({"parents": [{**parent, "research_id": value}], "change_summary": "Added"}
                     for value in (None, "abc", "F" * 32, "https://example.org/private"))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                paket_dogrula({**original, "lineage": value})

    def test_lineage_summary_is_sanitized_without_changing_source_identifiers(self):
        original = self.derived(self.parent())
        original["lineage"]["change_summary"] = "Added by person@example.org in C:\\Users\\Private\\notes"
        clean, count = kamu_kopyasi(original)
        self.assertGreater(count, 0)
        self.assertNotIn("person@example.org", clean["lineage"]["change_summary"])
        self.assertNotIn("Private", clean["lineage"]["change_summary"])
        self.assertEqual(original["lineage"]["parents"], clean["lineage"]["parents"])
        self.assertEqual(kamu_kopyasi(clean), (clean, 0))

    def test_local_draft_preview_import_and_edit_preserve_lineage(self):
        area = GelistiriciAlani(Depo(self.root / "local.sqlite3", []))
        source = self.derived(self.parent())
        draft = area.olustur({key: source[key] for key in KAYIT_ALANLARI if key in source})
        content = {key: draft[key] for key in KAYIT_ALANLARI if key in draft and key != "lineage"}
        edited = area.guncelle(draft["id"], {**content, "expected_version": draft["version"], "title": "Further study"})
        preview = area.onizle(edited["id"], {"expected_version": edited["version"]})["packet"]
        self.assertEqual(preview["schema_version"], 3)
        self.assertEqual(preview["lineage"], source["lineage"])
        imported = area.ice_aktar({"packet": preview})["entry"]
        self.assertEqual(imported["lineage"], preview["lineage"])

    def test_published_child_has_original_attribution_and_parent_has_child(self):
        parent = self.parent(title="Original source", author="Public attribution", license="CC-BY-4.0")
        child = self.publish(self.submit(self.derived(parent, title="Expanded study"), user=self.other))
        public = self.store.yayini_oku(child["id"])
        self.assertEqual(public["relationships"]["parents"], [{"id": parent["id"], "version": 1,
            "title": "Original source", "author": "Public attribution", "license": "CC-BY-4.0", "available": True}])
        self.assertEqual(self.store.yayini_oku(parent["id"])["relationships"]["children"],
                         [{"id": child["id"], "version": 1, "title": "Expanded study"}])
        self.assertEqual(public["packet"]["sources"], paket()["sources"])
        self.assertEqual(self.store.yayini_oku(parent["id"])["packet"]["title"], "Original source")

    def test_unpublished_future_nonresearch_or_unknown_parent_is_rejected_atomically(self):
        parent = self.parent()
        pending = self.submit(paket(title="Private draft"))
        project = self.parent(kind="project")
        for identifier, version in ((pending["id"], 1), (parent["id"], 2), (project["id"], 1), ("d" * 32, 1)):
            packet = self.derived(parent)
            packet["lineage"]["parents"] = [{"research_id": identifier, "version": version}]
            with self.subTest(identifier=identifier), self.assertRaises(ToplulukHatasi) as caught:
                self.submit(packet)
            self.assertEqual(caught.exception.status, 409)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_entries").fetchone()[0], 3)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_lineage").fetchone()[0], 0)

    def test_parent_withdrawn_between_submission_and_review_blocks_publication(self):
        parent = self.parent()
        child = self.submit(self.derived(parent))
        self.store.geri_cek(self.author, parent["id"], {"expected_version": parent["version"]})
        with self.assertRaises(ToplulukHatasi) as caught:
            self.publish(child)
        self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(ToplulukHatasi):
            self.store.yayini_oku(child["id"])

    def test_self_and_cross_report_cycles_cannot_replace_existing_publication(self):
        parent = self.parent()
        with self.assertRaises(ToplulukHatasi):
            self.submit(self.derived(parent), current=parent)
        child = self.publish(self.submit(self.derived(parent)))
        with self.assertRaises(ToplulukHatasi):
            self.submit(self.derived(child), current=parent)
        self.assertEqual(self.store.yayini_oku(parent["id"])["packet"], paket())
        self.assertEqual(self.store.yayini_oku(parent["id"])["version"], 1)

    def test_old_published_source_remains_valid_after_parent_update(self):
        parent = self.parent(title="Historical attribution", author="Original attribution", license="CC-BY-4.0")
        self.publish(self.submit(paket(title="Current revised source", author="Changed attribution", license="CC0-1.0"), current=parent))
        child = self.publish(self.submit(self.derived(parent)))
        relation = self.store.yayini_oku(child["id"])["relationships"]["parents"][0]
        self.assertEqual((relation["title"], relation["author"], relation["license"]),
                         ("Historical attribution", "Original attribution", "CC-BY-4.0"))

    def test_exact_public_version_and_packet_are_readable_until_entry_is_hidden(self):
        parent = self.parent(title="Original source")
        self.store.etkilesim_ekle(parent["id"], self.event(parent))
        updated = self.publish(self.submit(paket(title="Latest source"), current=parent))
        path = f'/api/community/cards/{parent["id"]}'
        old = self.client.get(path, params={"version": 1})
        self.assertEqual(old.status_code, 200, old.text)
        self.assertEqual(old.json()["version"], 1)
        self.assertEqual(old.json()["published_version"], updated["published_version"])
        self.assertEqual(old.json()["packet"]["title"], "Original source")
        self.assertEqual(old.json()["engagement"]["helpful"], 1)
        self.assertEqual(self.client.get(path).json()["packet"]["title"], "Latest source")
        self.assertEqual(self.client.get(path + "/packet", params={"version": 1}).json()["title"], "Original source")
        pending = self.submit(paket(title="Private future revision"), current=updated)
        for version in (pending["version"], pending["version"] + 1, 2):
            self.assertEqual(self.client.get(path, params={"version": version}).status_code, 404)
            self.assertEqual(self.client.get(path + "/packet", params={"version": version}).status_code, 404)
        for version in (0, -1, 2**63, "invalid"):
            self.assertEqual(self.client.get(path, params={"version": version}).status_code, 422)
        self.store.yayindan_kaldir(self.moderator, parent["id"], {"expected_version": pending["version"], "note": "Review"})
        self.assertEqual(self.client.get(path, params={"version": 1}).status_code, 404)
        self.assertEqual(self.client.get(path + "/packet", params={"version": 1}).status_code, 404)

    def test_withdrawal_hides_reference_metadata_and_child_without_rewriting_packets(self):
        parent = self.parent(title="Source hidden title", author="Hidden attribution")
        child = self.publish(self.submit(self.derived(parent, title="Visible child")))
        child_packet = child["packet"]
        self.store.yayindan_kaldir(self.moderator, parent["id"], {"expected_version": parent["version"], "note": "Review"})
        public = self.store.yayini_oku(child["id"])
        self.assertEqual(public["packet"], child_packet)
        self.assertEqual(public["relationships"]["parents"], [{"id": parent["id"], "version": 1,
                         "title": None, "author": None, "license": None, "available": False}])
        second_parent = self.parent()
        second_child = self.publish(self.submit(self.derived(second_parent)))
        self.store.geri_cek(self.author, second_child["id"], {"expected_version": second_child["version"]})
        self.assertEqual(self.store.yayini_oku(second_parent["id"])["relationships"]["children_total"], 0)

    def test_republication_never_reopens_hidden_or_withdrawn_historical_content(self):
        for operation in ("hide", "withdraw"):
            with self.subTest(operation=operation):
                parent = self.parent(title="Sensitive former publication")
                child = self.publish(self.submit(self.derived(parent)))
                if operation == "hide":
                    closed = self.store.yayindan_kaldir(self.moderator, parent["id"],
                        {"expected_version": parent["version"], "note": "Review"})["card"]
                else:
                    closed = self.store.geri_cek(self.author, parent["id"], {"expected_version": parent["version"]})["card"]
                updated = self.publish(self.submit(paket(title="Sanitized replacement"), current=closed))
                reopened = ToplulukDeposu(self.store.path, clock=lambda: self.now[0])
                self.assertEqual(reopened.yayini_oku(parent["id"])["packet"]["title"], "Sanitized replacement")
                with self.assertRaises(ToplulukHatasi) as caught:
                    reopened.yayini_oku(parent["id"], version=parent["published_version"])
                self.assertEqual(caught.exception.status, 404)
                with self.assertRaises(ToplulukHatasi) as caught:
                    reopened.etkilesim_ekle(parent["id"], self.event(parent))
                self.assertEqual(caught.exception.status, 409)
                self.assertFalse(reopened.yayini_oku(child["id"])["relationships"]["parents"][0]["available"])
                with self.assertRaises(ToplulukHatasi):
                    self.submit(self.derived(parent))
                self.assertEqual(reopened.yayini_oku(parent["id"], version=updated["published_version"])["packet"]["title"],
                                 "Sanitized replacement")

    def test_duplicate_submission_does_not_replay_hidden_parent_metadata(self):
        parent = self.parent(title="Former public title", author="Former attribution")
        payload = {"packet": self.derived(parent), "expected_version": 0, "idempotency_key": uuid.uuid4().hex}
        first = self.store.gonder(self.author, payload)
        self.assertTrue(first["card"]["relationships"]["parents"][0]["available"])
        self.store.geri_cek(self.author, parent["id"], {"expected_version": parent["version"]})
        repeat = self.store.gonder(self.author, payload)
        self.assertTrue(repeat["duplicate"])
        self.assertFalse(repeat["card"]["relationships"]["parents"][0]["available"])
        self.assertNotIn("Former public title", json.dumps(repeat))
        self.assertNotIn("Former attribution", json.dumps(repeat))

    def test_anonymous_inbox_uses_the_same_parent_validation(self):
        import secrets
        parent = self.parent()
        packet = kamu_kopyasi(self.derived(parent))[0]
        result = self.store.inbox_gonder(secrets.token_urlsafe(32), {"packet": packet, "consent": True,
                                                                  "idempotency_key": uuid.uuid4().hex})
        entry_id = result["submission"]["id"]
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT parent_id FROM community_lineage WHERE entry_id=?", (entry_id,)).fetchone()[0], parent["id"])
        packet["lineage"]["parents"][0]["version"] = 999
        with self.assertRaises(ToplulukHatasi):
            self.store.inbox_gonder(secrets.token_urlsafe(32), {"packet": packet, "consent": True, "idempotency_key": uuid.uuid4().hex})

    def test_events_are_revision_scoped_and_reads_do_not_increment(self):
        parent = self.parent()
        self.assertEqual(set(self.store.yayini_oku(parent["id"])["engagement"].values()), {0})
        for kind, counter in EVENT_COUNTERS.items():
            result = self.store.etkilesim_ekle(parent["id"], self.event(parent, kind))
            self.assertEqual(result["engagement"][counter], 1)
        changed = self.publish(self.submit(paket(title="New version"), current=parent))
        self.assertEqual(set(self.store.yayini_oku(parent["id"])["engagement"].values()), {0})
        historical = self.store.etkilesim_ekle(parent["id"], self.event(parent))
        self.assertEqual(historical["engagement"]["helpful"], 2)
        self.assertEqual(self.store.yayini_oku(parent["id"], version=parent["published_version"])["engagement"]["helpful"], 2)
        self.assertEqual(self.store.yayini_oku(parent["id"])["engagement"]["helpful"], 0)
        with self.assertRaises(ToplulukHatasi) as caught:
            self.store.etkilesim_ekle(parent["id"], self.event(parent, version=999))
        self.assertEqual(caught.exception.status, 409)
        self.store.geri_cek(self.author, changed["id"], {"expected_version": changed["version"]})
        with self.assertRaises(ToplulukHatasi) as caught:
            self.store.etkilesim_ekle(changed["id"], self.event(changed))
        self.assertEqual(caught.exception.status, 404)

    def test_concurrent_identical_events_increment_once_without_raw_identifier(self):
        card = self.parent()
        payload = self.event(card)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.store.etkilesim_ekle(card["id"], payload), range(16)))
        self.assertEqual(sum(not result["duplicate"] for result in results), 1)
        self.assertEqual(self.store.yayini_oku(card["id"])["engagement"]["helpful"], 1)
        with self.store.baglanti() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(community_event_receipts)")}
            self.assertEqual(columns, {"receipt_hash", "entry_id", "version", "expires_at"})
            stored = dict(connection.execute("SELECT * FROM community_event_receipts").fetchone())
        self.assertNotIn(payload["event_id"], json.dumps(stored))

    def test_receipts_expire_and_capacity_does_not_evict_a_valid_receipt(self):
        card = self.parent()
        payload = self.event(card)
        self.store.etkilesim_ekle(card["id"], payload)
        with patch("radar.topluluk_depo.EVENT_RECEIPT_LIMIT", 1):
            self.assertTrue(self.store.etkilesim_ekle(card["id"], payload)["duplicate"])
            with self.assertRaises(ToplulukHatasi) as caught:
                self.store.etkilesim_ekle(card["id"], self.event(card))
            self.assertEqual(caught.exception.status, 503)
        self.now[0] += EVENT_RECEIPT_SECONDS
        self.assertFalse(self.store.etkilesim_ekle(card["id"], payload)["duplicate"])
        self.assertEqual(self.store.yayini_oku(card["id"])["engagement"]["helpful"], 2)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_event_receipts").fetchone()[0], 1)

    def test_invalid_event_fields_cannot_store_context(self):
        card = self.parent()
        valid = self.event(card)
        cases = [{**valid, "kind": "rating"}, {**valid, "kind": []}, {**valid, "version": True},
                 {**valid, "version": 0}, {**valid, "event_id": "person@example.org"},
                 {**valid, "conversation": "private"}, {**valid, "ip": "127.0.0.1"}]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.store.etkilesim_ekle(card["id"], payload)
        self.assertEqual(set(self.store.yayini_oku(card["id"])["engagement"].values()), {0})

    def test_migration_reopen_preserves_existing_data_and_cascades_revision_metrics(self):
        card = self.parent()
        with self.store.baglanti(write=True) as connection:
            connection.execute("DROP TABLE community_event_receipts")
            connection.execute("DROP TABLE community_engagement")
            connection.execute("DROP TABLE community_lineage")
            connection.execute("DROP TABLE community_public_revisions")
        migrated = ToplulukDeposu(self.store.path, clock=lambda: self.now[0])
        self.assertEqual(migrated.yayini_oku(card["id"])["packet"], card["packet"])
        migrated.etkilesim_ekle(card["id"], self.event(card))
        reopened = ToplulukDeposu(self.store.path, clock=lambda: self.now[0])
        self.assertEqual(reopened.yayini_oku(card["id"])["engagement"]["helpful"], 1)
        with reopened.baglanti(write=True) as connection:
            connection.execute("DELETE FROM community_revisions WHERE entry_id=?", (card["id"],))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_engagement").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_event_receipts").fetchone()[0], 0)

    def test_browser_events_require_anonymous_session_csrf(self):
        card = self.parent()
        path = f'/api/community/cards/{card["id"]}/events'
        payload = self.event(card, "web_view")
        session = self.client.post("/api/community/session", json={}, headers={"Origin": ORIGIN}).json()
        self.assertFalse(session["authenticated"])
        self.assertEqual(self.client.post(path, json=payload, headers={"Origin": ORIGIN}).status_code, 403)
        response = self.client.post(path, json=payload, headers={"Origin": ORIGIN, "X-Ufuk-CSRF": session["csrf_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["engagement"]["web_views"], 1)

    def test_cookieless_browser_view_only_accepts_same_origin_view_without_account(self):
        card = self.parent()
        path = f'/api/community/cards/{card["id"]}/events'
        payload = self.event(card, "web_view")
        headers = {"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin", "X-DevTrellis-View": "1"}
        with patch.object(self.store, "oturum_ac", side_effect=AssertionError("Views must not create sessions")):
            response = self.client.post(path, json=payload, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(response.json()["engagement"]["web_views"], 1)
            self.assertTrue(self.client.post(path, json=payload, headers=headers).json()["duplicate"])
        for extra in ({"Origin": "https://foreign.example.org"}, {"Sec-Fetch-Site": "cross-site"},
                      {"Cookie": "other=private"}, {"Authorization": "Bearer private"},
                      {"X-Ufuk-CSRF": "private"}, {"X-DevTrellis-Client": "1"}, {"X-DevTrellis-View": "0"}):
            self.assertEqual(self.client.post(path, json=payload, headers={**headers, **extra}).status_code, 403)
        self.assertEqual(self.client.post(path, json=payload, headers={"X-DevTrellis-View": "1"}).status_code, 403)
        for kind in ("mcp_read", "helpful", "used_in_research", "needs_update", "disputed"):
            self.assertEqual(self.client.post(path, json=self.event(card, kind), headers=headers).status_code, 403)
        with self.store.baglanti() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 0)
        self.assertEqual(self.store.yayini_oku(card["id"])["engagement"]["web_views"], 1)

    def test_explicit_client_events_reject_browser_context_and_do_not_need_account(self):
        card = self.parent()
        path = f'/api/community/cards/{card["id"]}/events'
        payload = self.event(card, "mcp_read")
        self.assertEqual(self.client.post(path, json=payload).status_code, 403)
        for extra in ({"Origin": ORIGIN}, {"Cookie": "arbitrary=value"}, {"Sec-Fetch-Site": "same-origin"},
                      {"Sec-Fetch-Mode": "cors"}, {"X-DevTrellis-Client": "0"}):
            response = self.client.post(path, json=payload, headers={"X-DevTrellis-Client": "1", **extra})
            self.assertEqual(response.status_code, 403, response.text)
        response = self.client.post(path, json=payload, headers={"X-DevTrellis-Client": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["engagement"]["mcp_reads"], 1)
        self.assertEqual(self.client.post("/api/community/logout", json={}, headers={"X-DevTrellis-Client": "1"}).status_code, 403)

    def test_events_have_bounded_rate_and_hidden_cards_cannot_receive_events(self):
        card = self.parent()
        path = f'/api/community/cards/{card["id"]}/events'
        headers = {"X-DevTrellis-Client": "1"}
        for _ in range(60):
            response = self.client.post(path, json=self.event(card), headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
        response = self.client.post(path, json=self.event(card), headers=headers)
        self.assertEqual(response.status_code, 429)
        self.now[0] += 60
        self.store.yayindan_kaldir(self.moderator, card["id"], {"expected_version": card["version"], "note": "Review"})
        self.assertEqual(self.client.post(path, json=self.event(card), headers=headers).status_code, 404)

    def test_public_library_count_distinguishes_empty_library_from_no_search_match(self):
        path = "/api/community/cards"
        self.assertEqual(self.client.get(path).json()["library"], {"published_research_total": 0})
        self.parent(kind="project", title="Public project")
        self.submit(paket(title="Private pending research"))
        self.assertEqual(self.client.get(path).json()["library"], {"published_research_total": 0})
        research = self.parent(title="Public research")
        result = self.client.get(path, params={"query": "no-matching-result", "kind": "research"}).json()
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["library"], {"published_research_total": 1})
        self.assertNotIn("library", self.store.listele(user=self.author))
        self.assertNotIn("library", self.store.listele(user=self.moderator, review=True))
        self.store.yayindan_kaldir(self.moderator, research["id"], {"expected_version": research["version"], "note": "Review"})
        self.assertEqual(self.client.get(path).json()["library"], {"published_research_total": 0})


if __name__ == "__main__":
    unittest.main()

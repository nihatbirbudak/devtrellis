"""Araştırma taslaklarının model bilgisini, eski paylaşım uyumluluğunu ve özgün raporu koruduğunu doğrular."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from radar.depo import Depo
from radar.gelistirici_alani import GelistiriciAlani, KAYIT_ALANLARI, paket_dogrula
from radar.platform_api import router_olustur


class ArastirmaPaylasimiTestleri(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.depo = Depo(Path(temporary.name) / "test.sqlite3", [])
        self.area = GelistiriciAlani(self.depo)
        self.task = {"id": "saved-research", "kind": "deep", "status": "ready", "profile_topic": "ai", "language": "en",
            "updated_at": "2026-11-04T12:00:00+00:00", "report_generated_at": "2026-11-04T10:00:00+00:00",
            "model": "nvidia/example-recorded-model", "engine": "nvidia",
            "source_url": "https://example.org/article", "report_text": "Source-backed report with limitations.",
            "sources": [{"title": "Original source", "url": "https://example.org/article"}],
            "result": {"title_tr": "Recorded research", "summary_tr": "A summary.",
                       "evidence_quality": {"status": "verified", "coverage_complete": True}}, "coverage": {"limited": False}}

    def publishable(self, entry):
        payload = {key: entry[key] for key in KAYIT_ALANLARI if key in entry}
        payload.update(author="Example author", license="CC-BY-4.0", expected_version=entry["version"])
        return self.area.guncelle(entry["id"], payload)

    def test_snapshot_stays_separate_and_duplicate_keeps_user_edits(self):
        original = copy.deepcopy(self.task)
        created = self.area.arastirmadan_taslak(self.task)
        entry = created["entry"]
        self.assertFalse(created["duplicate"])
        self.assertEqual(entry["provenance"]["model"], original["model"])
        self.assertEqual(entry["provenance"]["generated_at"], original["report_generated_at"])
        self.assertEqual(entry["provenance"]["evidence_status"], "verified")
        with self.assertRaises(ValueError):
            self.area.onizle(entry["id"], {"expected_version": 1})
        edited = self.publishable(entry)
        repeated = self.area.arastirmadan_taslak(self.task)
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(repeated["entry"], edited)
        self.assertEqual(self.task, original)

    def test_version_two_round_trip_and_old_version_one_unchanged(self):
        entry = self.publishable(self.area.arastirmadan_taslak(self.task)["entry"])
        packet = self.area.onizle(entry["id"], {"expected_version": entry["version"]})["packet"]
        self.assertEqual(packet["schema_version"], 2)
        self.assertEqual(self.area.ice_aktar({"packet": packet})["entry"]["provenance"], packet["provenance"])
        old = {key: value for key, value in packet.items() if key != "provenance"}
        old["schema_version"] = 1
        self.assertEqual(paket_dogrula(old), old)
        first = self.area.ice_aktar({"packet": old})
        self.assertFalse(first["duplicate"])
        self.assertTrue(GelistiriciAlani(self.depo).ice_aktar({"packet": old})["duplicate"])
        for invalid in ({**packet, "schema_version": 1}, {**old, "schema_version": 2}):
            with self.assertRaises(ValueError):
                paket_dogrula(invalid)

    def test_edited_text_invalidates_original_check_and_old_records_have_no_model(self):
        entry = self.publishable(self.area.arastirmadan_taslak(self.task)["entry"])
        payload = {key: entry[key] for key in KAYIT_ALANLARI if key in entry and key != "provenance"}
        payload.update(body="Author's later correction.", expected_version=entry["version"])
        edited = self.area.guncelle(entry["id"], payload)
        self.assertTrue(edited["provenance"]["modified"])
        self.assertEqual(edited["provenance"]["evidence_status"], "unverified")
        self.assertEqual(edited["provenance"]["model"], self.task["model"])
        old = {**self.task, "id": "legacy", "model": "", "engine": "", "report_generated_at": None}
        legacy = self.area.arastirmadan_taslak(old)["entry"]
        self.assertEqual(legacy["provenance"]["model"], "")
        self.assertEqual(legacy["provenance"]["provider"], "unknown")
        self.assertIsNone(legacy["provenance"]["generated_at"])

    def test_stale_and_incomplete_evidence_is_not_exported_as_verified(self):
        for changes in ({"stale": True}, {"coverage": {"limited": True}}):
            task = {**self.task, **changes, "id": str(changes)}
            self.assertEqual(self.area.arastirmadan_taslak(task)["entry"]["provenance"]["evidence_status"], "unverified")
        with self.assertRaises(ValueError):
            self.area.arastirmadan_taslak({**self.task, "status": "running"})
        with self.assertRaises(ValueError):
            self.area.arastirmadan_taslak({**self.task, "id": "too-long", "report_text": "a" * 12001})

    def test_bad_metadata_and_private_link_rejected_before_import(self):
        entry = self.publishable(self.area.arastirmadan_taslak(self.task)["entry"])
        packet = self.area.onizle(entry["id"], {"expected_version": entry["version"]})["packet"]
        for key, bad in (("model", "m" * 161), ("provider", []), ("source_url", "https://127.0.0.1/"),
                         ("generated_at", "yesterday"), ("topic", "missing"), ("evidence_status", "certain"),
                         ("modified", "false")):
            damaged = copy.deepcopy(packet)
            damaged["provenance"][key] = bad
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.area.ice_aktar({"packet": damaged})

    def test_http_draft_requires_csrf_and_reuses_saved_report(self):
        app = FastAPI()
        app.include_router(router_olustur(self.depo, "test-csrf", SimpleNamespace(oku=lambda task_id: self.task)))
        with TestClient(app) as client:
            path = "/api/platform/research/saved-research/draft"
            self.assertEqual(client.post(path, json={}).status_code, 403)
            response = client.post(path, json={}, headers={"X-Radar-CSRF": "test-csrf"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["entry"]["provenance"]["model"], self.task["model"])
            self.assertEqual(client.post(path, json={"model": "forged"}, headers={"X-Radar-CSRF": "test-csrf"}).status_code, 422)

    def test_real_workflow_view_creates_draft_over_application_http(self):
        from radar.sunucu import uygulama
        from tests.test_tam_uygulama import kaynak, haber
        app = uygulama(self.depo.path, sources=[kaynak("fixture", "ai")], seed=False, credential_provider=lambda: None)
        app.state.depo.kaydet("fixture", [haber(1)])
        item_id = app.state.depo.gorunum()["items"][0]["id"]
        request = app.state.research_requests.olustur(question="Is this claim supported?", topic="ai",
            source_url="https://example.org/post", claim_text="The robot navigates autonomously.",
            context_text="PRIVATE_TRANSCRIPT_STAYS_LOCAL_482", idempotency_key="shared-research-claim")
        task = app.state.workflow.kuyruga_al(item_id, request_id=request["id"])
        with app.state.depo.baglanti(write=True) as connection:
            connection.execute("UPDATE workflow_tasks SET status='ready',result_json=?,report_text=?,report_model=?,report_generated_at=? WHERE id=?",
                (json.dumps(self.task["result"]), self.task["report_text"], self.task["model"], self.task["report_generated_at"], task["id"]))
        with TestClient(app, base_url="http://127.0.0.1:8781") as client:
            token = client.get("/api/state").json()["csrf_token"]
            response = client.post(f"/api/platform/research/{task['id']}/draft", json={},
                                   headers={"X-Radar-CSRF": token, "Origin": "http://127.0.0.1:8781"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["entry"]["provenance"]["topic"], "ai")
            self.assertEqual(response.json()["entry"]["provenance"]["generated_at"], self.task["report_generated_at"])
            self.assertEqual(response.json()["entry"]["provenance"]["claim_text"], request["claim_text"])
            self.assertNotIn("PRIVATE_TRANSCRIPT_STAYS_LOCAL_482", response.text)


if __name__ == "__main__":
    unittest.main()

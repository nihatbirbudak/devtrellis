"""MCP okuma ve kullanım sayımlarında sürüm, gizlilik, hata dayanımı ve yerel geçmişi sınar."""

import json
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mcp_topluluk import contribution_packet
from radar.platform_api import router_olustur
from radar.topluluk_baglantisi import ToplulukBaglantiHatasi
from tests.test_community_mcp import report
from tests import test_topluluk_baglantisi as fixtures


class ArastirmaKopruTestleri(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ToplulukBaglantisiTestleri()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.bridge = self.fixture.bridge
        preview = self.fixture.prepare()
        sent = self.fixture.send(preview)
        store = self.fixture.remote.state.community
        moderator = store.hesap_ekle("reviewer", "Reviewer", "moderator", "a-long-test-password")
        store.karar(moderator, sent["submission"]["id"],
                    {"expected_version": 1, "decision": "publish", "note": "Sources checked"})
        self.entry_id = sent["submission"]["id"]
        self.fixture.calls.clear()

    def test_mcp_access_and_declared_reuse_are_distinct_and_do_not_send_context(self):
        card = self.bridge.mcp_oku(self.entry_id, 1)
        self.assertTrue(card["access_counted"])
        event = self.fixture.calls[-1]
        self.assertEqual(event.headers["X-DevTrellis-Client"], "1")
        self.assertNotIn("cookie", event.headers)
        self.assertNotIn("origin", event.headers)
        payload = json.loads(event.read())
        self.assertEqual(set(payload), {"kind", "version", "event_id"})
        self.assertEqual(payload["kind"], "mcp_read")
        first = self.bridge.kullanimi_bildir(self.entry_id, {"version": 1})
        second = self.bridge.kullanimi_bildir(self.entry_id, {"version": 1})
        self.assertEqual(first["engagement"]["mcp_reads"], 1)
        self.assertEqual(first["engagement"]["used_in_research"], 1)
        self.assertEqual(second["engagement"]["used_in_research"], 1)
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["engagement"]["helpful"], 0)

    def test_count_failure_does_not_break_read_and_bad_version_never_reaches_network(self):
        with patch.object(self.bridge, "olay_bildir", side_effect=ToplulukBaglantiHatasi("Unavailable")):
            card = self.bridge.mcp_oku(self.entry_id)
        self.assertFalse(card["access_counted"])
        self.assertTrue(card["packet"]["body"])
        self.fixture.calls.clear()
        for version in (True, 0, -1, "1", 2**63, {}):
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.bridge.kullanimi_bildir(self.entry_id, {"version": version})
        with self.assertRaises(ValueError):
            self.bridge.kullanimi_bildir(self.entry_id, {"version": 1, "private_question": "not sent"})
        self.assertEqual(self.fixture.calls, [])

    def test_local_read_and_usage_routes_require_csrf_and_lineage_round_trips(self):
        app = FastAPI()
        app.include_router(router_olustur(self.fixture.depo, "csrf-test", community=self.bridge))
        with TestClient(app) as client:
            base = "/api/platform/community/cards/" + self.entry_id
            self.assertEqual(client.post(base + "/read", json={}).status_code, 403)
            self.assertEqual(client.post(base + "/use", json={"version": 1}).status_code, 403)
            response = client.post(base + "/read", json={"version": 1}, headers={"X-Radar-CSRF": "csrf-test"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["version"], 1)
            self.assertEqual(client.post(base + "/read", json={"private": "context"},
                                         headers={"X-Radar-CSRF": "csrf-test"}).status_code, 422)
        lineage = {"parents": [{"research_id": self.entry_id, "version": 1}],
                   "change_summary": "Adds original source evidence and a reproducible example."}
        packet = contribution_packet(**report(lineage=lineage))
        self.assertEqual(packet["schema_version"], 3)
        preview = self.bridge.onizle({"packet": packet})
        self.assertEqual(preview["packet"]["lineage"], lineage)
        self.assertEqual(preview["packet"]["author"], "Anonymous contributor")

    def test_local_history_pagination_reaches_older_than_one_hundred_without_network(self):
        with self.fixture.depo.baglanti(write=True) as connection:
            for index in range(105):
                identity = f"{index:032x}"
                connection.execute("INSERT INTO community_deliveries VALUES(?,?,?,NULL,NULL,?)",
                                   (identity, "fixture-" + identity, "Public report " + str(index), "2026-11-01T00:00:00Z"))
        self.fixture.calls.clear()
        first = self.bridge.listele(limit=25)
        last = self.bridge.listele(offset=100, limit=25)
        self.assertEqual(first["total"], 106)
        self.assertEqual(len(first["items"]), 25)
        self.assertEqual(len(last["items"]), 6)
        self.assertFalse({r["local_id"] for r in first["items"]} & {r["local_id"] for r in last["items"]})
        self.assertEqual(self.fixture.calls, [])


if __name__ == "__main__":
    unittest.main()

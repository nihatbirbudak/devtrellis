"""Özel taleplerin hesapsız teslimini ve kamu kütüphanesinden ayrılığını sınar."""
import json
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from radar.topluluk_api import uygulama

ORIGIN = "https://community.example.org"
PRIVATE = "Private test request only, never a public research card"


class RequestAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.now = [1793880000]
        self.app = uygulama(Path(temporary.name) / "community.sqlite3", public_url=ORIGIN,
                            sources=[], clock=lambda: self.now[0])
        self.client = self.client_for()

    def client_for(self):
        client = TestClient(self.app, base_url=ORIGIN)
        self.addCleanup(client.close)
        return client

    def headers(self, client=None):
        session = (client or self.client).post("/api/community/session", json={}, headers={"Origin": ORIGIN}).json()
        return {"Origin": ORIGIN, "X-Ufuk-CSRF": session["csrf_token"]}

    def payload(self):
        return dict(kind="support", category="setup", subject="Test setup request",
                    message=PRIVATE, name="Optional test contact", email="test@example.org",
                    language="en", consent=True, idempotency_key=str(uuid.uuid4()), website="")

    def submit(self, payload=None):
        return self.client.post("/api/community/requests", json=payload or self.payload(), headers=self.headers())

    def test_anonymous_delivery_is_receipt_only_and_retry_deduplicates(self):
        payload = self.payload()
        first = self.submit(payload)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(set(first.json()), {"id", "status", "created_at", "duplicate"})
        self.assertFalse(first.json()["duplicate"])
        second = self.submit(payload)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertTrue(second.json()["duplicate"])
        for value in (PRIVATE, payload["email"], payload["name"], payload["idempotency_key"]):
            self.assertNotIn(value, first.text + second.text)
        self.assertEqual(first.headers["cache-control"], "no-store")
        self.assertEqual(first.headers["referrer-policy"], "no-referrer")

    def test_create_requires_same_origin_session_and_csrf(self):
        payload = self.payload()
        self.assertEqual(self.client.post("/api/community/requests", json=payload,
                                         headers={"Origin": ORIGIN}).status_code, 401)
        valid = self.headers()
        for headers in ({"Origin": ORIGIN}, {"X-Ufuk-CSRF": valid["X-Ufuk-CSRF"]},
                        {**valid, "Origin": "https://elsewhere.example.org"}):
            self.assertEqual(self.client.post("/api/community/requests", json=payload, headers=headers).status_code, 403)
        self.assertEqual(self.app.state.requests.listele()["total"], 0)

    def test_only_moderator_can_list_read_and_change_status(self):
        receipt = self.submit().json()
        path = "/api/community/requests/" + receipt["id"]
        for role, expected in ((None, 401), ("contributor", 403), ("moderator", 200)):
            client = self.client_for()
            if role:
                account = self.app.state.community.hesap_ekle(role, "Test actor", role, "A-test-password-2947!")
                token = self.app.state.community.oturum_ac(account["id"])
                client.cookies.set("devtrellis_session", token)
            headers = self.headers(client)
            listing = client.get("/api/community/requests")
            detail = client.get(path)
            changed = client.post(path + "/status", json={"status": "in_review"}, headers=headers)
            self.assertEqual([listing.status_code, detail.status_code, changed.status_code], [expected] * 3)
            if role == "moderator":
                self.assertEqual(detail.json()["message"], PRIVATE)
                self.assertNotIn(PRIVATE, listing.text)
                self.assertNotIn("test@example.org", listing.text)
                self.assertEqual(changed.json()["status"], "in_review")
                self.assertEqual(client.post(path + "/status", json={"status": "closed"},
                                             headers={"Origin": ORIGIN}).status_code, 403)

    def test_private_request_never_enters_public_card_routes(self):
        receipt = self.submit().json()
        self.assertEqual(self.client.get("/api/community/cards", params={"query": PRIVATE}).json()["total"], 0)
        for suffix in ("", "/packet"):
            result = self.client.get("/api/community/cards/" + receipt["id"] + suffix)
            self.assertEqual(result.status_code, 404)
            self.assertNotIn(PRIVATE, result.text)

    def test_intake_body_limit_and_four_per_minute(self):
        oversized = {**self.payload(), "message": "x" * 17000}
        self.assertEqual(self.submit(oversized).status_code, 413)
        for _ in range(3):
            self.assertEqual(self.submit().status_code, 201)
        limited = self.submit()
        self.assertEqual(limited.status_code, 429)
        self.assertIn("retry-after", limited.headers)
        self.assertEqual(self.app.state.requests.listele()["total"], 3)

    def test_daily_limit_survives_minute_boundary(self):
        for index in range(20):
            if index and index % 4 == 0:
                self.now[0] += 60
            self.assertEqual(self.submit().status_code, 201)
        self.now[0] += 60
        self.assertEqual(self.submit().status_code, 429)
        self.assertEqual(self.app.state.requests.listele()["total"], 20)

    def test_framework_validation_does_not_echo_private_body(self):
        response = self.client.post("/api/community/requests", json=[PRIVATE], headers=self.headers())
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(PRIVATE, response.text)

    def test_public_pages_and_assets_are_available_without_account(self):
        paths = ["/about", "/guidelines", "/contribute", "/support", "/requests"]
        paths += ["/static/community-" + name + extension for name in ("pages", "requests") for extension in (".js", ".css")]
        for path in paths:
            with self.subTest(path=path):
                result = self.client.get(path)
                self.assertEqual(result.status_code, 200)
                self.assertNotIn(PRIVATE, result.text)
                self.assertEqual(result.headers["cache-control"], "no-store")

    def test_one_hundred_anonymous_reads_do_not_create_sessions(self):
        for _ in range(100):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("set-cookie", response.headers)
        self.assertIsNone(self.client.cookies.get("devtrellis_session"))
        with closing(sqlite3.connect(self.app.state.community.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM community_sessions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

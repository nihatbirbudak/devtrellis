"""MCP köprüsünün yerel ağ sınırını, izinli gönderimini ve gerçek SDK araç kaydını doğrular."""

import asyncio
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from mcp_topluluk import CommunityClient, contribution_packet, create_server, local_address


def report(**changes):
    return {"title": "Source comparison", "summary": "A comparison with limitations.",
            "body": "The selected report text, with an explicit unresolved question.",
            "sources": [{"title": "Original source", "url": "https://example.org/research"}],
            "model": "unknown", "provider": "claude",
            "license": "CC-BY-4.0", **changes}


class LocalApiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond(None)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.respond(json.loads(body))

    def respond(self, payload):
        self.server.calls.append((self.path, dict(self.headers), payload))
        status, content_type = 200, "application/json"
        if self.server.failure:
            status, result, content_type = self.server.failure
        elif self.path == "/api/platform/community/session":
            result = {"csrf_token": "a" * 64, "destination": "https://devtrellis.org"}
        elif self.path.endswith("/search"):
            result = {"items": [{"id": "research-1", "packet": {"title": "Existing report"}}],
                      "total": 1, "destination": "https://devtrellis.org"}
        elif self.path.endswith("/cards/research-1"):
            result = {"packet": contribution_packet(**report()), "id": "research-1"}
        elif self.path.endswith("/preview"):
            result = {"preview_id": "preview-1", "packet": payload["packet"],
                      "destination": "https://devtrellis.org", "notice": "Only this packet will be sent."}
        elif "/review/" in self.path:
            result = {"approval_token": "c" * 43}
        elif self.path.endswith("/send"):
            if payload.get("approval_token") != "c" * 43 or payload.get("reviewed_public") is not True:
                status, result = 422, {"detail": "Review required"}
            else:
                result = {"status": "pending", "id": "contribution-1"}
        elif self.path.endswith("/discard"):
            result = {"status": "kept_local"}
        else:
            status, result = 404, {"detail": "Unknown endpoint"}
        raw = result if isinstance(result, bytes) else json.dumps(result).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        if status == 302:
            self.send_header("Location", "https://example.org/should-not-be-requested")
        self.end_headers()
        self.wfile.write(raw)


class CommunityMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LocalApiHandler)
        cls.server.calls = []
        cls.server.failure = None
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def setUp(self):
        self.server.calls.clear()
        self.server.failure = None
        self.client = CommunityClient(self.origin)

    def test_only_explicit_loopback_urls(self):
        self.assertEqual(local_address("http://localhost:8781/"), "http://localhost:8781")
        for value in ["https://localhost:8781", "http://127.0.0.1", "http://127.1:8781",
                      "http://example.org:8781", "http://192.168.1.1:8781", "http://localhost:0",
                      "http://user:password@localhost:8781", "http://localhost:8781/api",
                      "http://localhost:8781?destination=evil", "http://localhost:8781#evil",
                      "http://localhost:8781\n", "http://localhost:8781\\evil"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                CommunityClient(value)

    def test_search_reads_small_session_without_loading_private_workspace(self):
        result = self.client.search("evidence", 3)
        self.assertEqual(result["total"], 1)
        self.assertNotIn("csrf_token", result)
        self.assertEqual([row[0] for row in self.server.calls],
                         ["/api/platform/community/session", "/api/platform/community/search"])
        _, headers, payload = self.server.calls[-1]
        self.assertEqual(headers["X-Radar-CSRF"], "a" * 64)
        self.assertEqual(headers["Origin"], self.origin)
        self.assertEqual(payload, {"query": "evidence", "limit": 3})
        with self.assertRaises(ValueError):
            self.client.request("/api/platform")

    def test_read_accepts_ids_and_rejects_paths_or_urls(self):
        self.assertEqual(self.client.read("research-1")["packet"]["provenance"]["model"], "unknown")
        for value in ["../secret", "https://example.org", "a?x=1", "a%2fb", ""]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.client.read(value)

    def test_preview_preserves_report_and_submission_requires_explicit_consent(self):
        packet = contribution_packet(**report())
        preview = self.client.preview(packet)
        self.assertEqual(preview["packet"]["body"], report()["body"])
        self.assertEqual(preview["packet"]["author"], "Anonymous contributor")
        self.assertEqual(preview["packet"]["provenance"]["evidence_status"], "unverified")
        self.assertEqual(preview["packet"]["provenance"]["generated_at"], None)
        calls = len(self.server.calls)
        for confirmed in [False, "true", 1, None, True]:
            with self.subTest(confirmed=confirmed), self.assertRaises(ValueError):
                self.client.submit(preview["preview_id"], confirmed)
        with self.assertRaises(ValueError):
            self.client.submit("another-preview", True)
        self.assertEqual(len(self.server.calls), calls)
        self.assertNotIn("approval_token", preview)
        self.assertIn("/static/contribution-review.html?preview=preview-1", preview["review_url"])
        token = self.client.review(preview["preview_id"])
        for value in [False, "true", 1, None]:
            with self.assertRaises(ValueError):
                self.client.decide(preview["preview_id"], token, "share", value, True)
        self.assertEqual(self.client.decide(preview["preview_id"], token, "share", True, True)["status"], "pending")
        self.assertEqual(self.server.calls[-1][2], {"preview_id": "preview-1", "consent": True,
                                                  "reviewed_public": True, "approval_token": token})

    def test_private_question_and_author_are_never_added_to_packet(self):
        packet = contribution_packet(**report(question="PRIVATE_QUESTION", author="PRIVATE_NAME"))
        self.assertEqual(packet["author"], "Anonymous contributor")
        self.assertEqual(packet["provenance"]["question"], packet["title"])
        self.assertNotIn("PRIVATE_", json.dumps(packet))

    def test_keep_local_invalidates_the_mcp_session_capability(self):
        preview = self.client.preview(contribution_packet(**report()))
        token = self.client.review(preview["preview_id"])
        result = self.client.decide(preview["preview_id"], token, "keep")
        self.assertEqual(result["status"], "kept_local")
        count = len(self.server.calls)
        with self.assertRaises(ValueError):
            self.client.decide(preview["preview_id"], token, "share", True, True)
        self.assertEqual(len(self.server.calls), count)

    def test_redirect_error_and_malformed_responses_do_not_leak_content(self):
        for status, body, content_type in [(302, {}, "application/json"),
                                          (403, {"detail": "private-value"}, "application/json"),
                                          (200, b"invalid", "application/json"),
                                          (200, b"<html>not API</html>", "text/html")]:
            self.server.failure = (status, body, content_type)
            before = len(self.server.calls)
            with self.subTest(status=status, content_type=content_type), self.assertRaises(ValueError) as failure:
                self.client.read("research-1")
            self.assertNotIn("private-value", str(failure.exception))
            self.assertEqual(len(self.server.calls), before + 1)

    def test_response_and_request_limits(self):
        self.server.failure = (200, b"x" * 101, "application/json")
        with patch("mcp_topluluk.MAX_RESPONSE_BYTES", 100), self.assertRaisesRegex(ValueError, "limit"):
            self.client.read("research-1")
        self.server.calls.clear()
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            self.client.preview({"body": "x" * 33000})
        self.assertEqual(self.server.calls, [])

    def test_report_limits_and_conscious_license_selection(self):
        for changes in [{"license": "MIT"}, {"body": "x" * 12001}, {"model": ""},
                        {"sources": []}, {"sources": [{"title": "x", "url": "https://example.org", "key": "x"}]},
                        {"generated_at": "2026-11-04"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                contribution_packet(**report(**changes))

    def test_proxy_environment_cannot_redirect_local_requests(self):
        with patch.dict("os.environ", {"HTTP_PROXY": "http://not-a-proxy.invalid:99", "ALL_PROXY": "http://not-a-proxy.invalid:99"}):
            self.assertEqual(self.client.read("research-1")["id"], "research-1")

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional requirements-mcp.txt is not installed.")
    def test_sdk_protocol_lists_tools_and_enforces_confirmation(self):
        from mcp import Client

        async def scenario():
            async with Client(create_server(client=self.client), raise_exceptions=True) as session:
                listing = await session.list_tools()
                tools = {tool.name: tool for tool in listing.tools}
                self.assertEqual(set(tools), {"search_research", "read_research", "preview_contribution", "contribution_decision"})
                self.assertEqual(tools["contribution_decision"].meta["ui"]["visibility"], ["app"])
                self.assertNotIn("author", tools["preview_contribution"].input_schema["properties"])
                self.assertNotIn("question", tools["preview_contribution"].input_schema["properties"])
                self.assertTrue(tools["search_research"].annotations.read_only_hint)
                search = await session.call_tool("search_research", {"query": "evidence"})
                self.assertFalse(search.is_error)
                self.assertEqual(search.structured_content["total"], 1)
                preview = await session.call_tool("preview_contribution", report())
                self.assertFalse(preview.is_error)
                self.assertEqual(preview.structured_content["packet"]["body"], report()["body"])
                self.assertNotIn("approval_token", preview.meta or {})
                calls = len(self.server.calls)
                denied = await session.call_tool("contribution_decision", {"preview_id": "preview-1",
                    "approval_token": "c" * 43, "decision": "share", "reviewed_public": True, "consent": True})
                self.assertTrue(denied.is_error)
                self.assertEqual(len(self.server.calls), calls)

        asyncio.run(scenario())

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional requirements-mcp.txt is not installed.")
    def test_mcp_apps_hides_capability_from_model_content_and_serves_review_resource(self):
        from mcp import Client
        from mcp.client.extension import advertise
        from mcp_topluluk import APP_URI

        async def scenario():
            extension = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})
            async with Client(create_server(client=self.client), extensions=[extension]) as session:
                preview = await session.call_tool("preview_contribution", report())
                self.assertFalse(preview.is_error)
                token = preview.meta["approval_token"]
                self.assertNotIn(token, json.dumps(preview.structured_content))
                self.assertNotIn(token, str(preview.content))
                page = await session.read_resource(APP_URI)
                self.assertEqual(page.contents[0].mime_type, "text/html;profile=mcp-app")
                self.assertIn("contribution_decision", page.contents[0].text)
                rejected = await session.call_tool("contribution_decision", {"preview_id": "preview-1",
                    "approval_token": token, "decision": "share", "consent": True})
                self.assertTrue(rejected.is_error)
                accepted = await session.call_tool("contribution_decision", {"preview_id": "preview-1",
                    "approval_token": token, "decision": "share", "consent": True, "reviewed_public": True})
                self.assertFalse(accepted.is_error)
                self.assertEqual(accepted.structured_content["status"], "pending")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()

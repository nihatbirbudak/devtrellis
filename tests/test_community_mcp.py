"""MCP köprüsünün yerel ağ sınırını, izinli gönderimini ve gerçek SDK araç kaydını doğrular."""

import asyncio
import importlib.util
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

from mcp_topluluk import CommunityClient, contribution_packet, create_server, local_address
from radar.gelistirici_alani import paket_dogrula
from radar.konular import KONULAR


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
            if self.server.summary_support:
                result["search_views"] = ["full", "summary"]
            if self.server.ranked_support:
                result["search_modes"] = ["all", "ranked"]
        elif self.path.endswith("/search"):
            result = {"items": [{"id": "research-1", "version": 3, "status": "published",
                      "packet": contribution_packet(**report()), "updated_at": "2026-11-05T09:00:00+00:00",
                      "engagement": {"needs_update": 2, "disputed": 1},
                      "relationships": {"parents": [], "children": [], "children_total": 0}}],
                      "total": 1, "limit": payload["limit"], "offset": 0,
                      "library": {"published_research_total": 11}, "destination": "https://devtrellis.org"}
        elif urlsplit(self.path).path.endswith("/cards/research-1") or self.path.endswith("/cards/research-1/read"):
            result = {"packet": contribution_packet(**report()), "id": "research-1", "version": 3}
        elif self.path.endswith("/cards/research-1/use"):
            result = {"duplicate": False, "engagement": {"used_in_research": 1}}
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
        self.server.summary_support = True
        self.server.ranked_support = True
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
        self.assertEqual(payload, {"query": "evidence", "limit": 3, "view": "summary", "search_mode": "ranked"})
        with self.assertRaises(ValueError):
            self.client.request("/api/platform")

    def test_search_uses_old_local_request_when_summary_is_not_advertised(self):
        self.server.summary_support = False
        self.server.ranked_support = False
        result = self.client.search("evidence", 3)
        self.assertEqual(self.server.calls[-1][2], {"query": "evidence", "limit": 3})
        self.assertEqual(len(self.server.calls), 2)
        self.assertEqual(result["view"], "summary")
        self.assertNotIn("body", result["items"][0]["packet"])

    def test_summary_only_local_version_does_not_receive_ranked_option(self):
        self.server.ranked_support = False
        result = self.client.search("evidence")
        self.assertEqual(self.server.calls[-1][2], {"query": "evidence", "limit": 3, "view": "summary"})
        self.assertEqual(result["search"], {"mode": "all", "fallback": True})

    def test_search_omits_body_but_keeps_evidence_and_full_version_read(self):
        result = self.client.search("evidence", 3)
        card = result["items"][0]
        self.assertEqual(result["view"], "summary")
        self.assertNotIn("body", card["packet"])
        self.assertNotIn(report()["body"], json.dumps(result))
        self.assertEqual((card["id"], card["version"], card["status"]), ("research-1", 3, "published"))
        for field in ("title", "summary", "sources", "license"):
            self.assertEqual(card["packet"][field], report()[field])
        self.assertEqual(card["packet"]["provenance"]["evidence_status"], "unverified")
        self.assertIsNone(card["packet"]["provenance"]["generated_at"])
        self.assertEqual(card["packet"]["provenance"]["model"], "unknown")
        self.assertIn("created_at", card["packet"])
        self.assertIn("updated_at", card["packet"])
        self.assertEqual(card["updated_at"], "2026-11-05T09:00:00+00:00")
        self.assertEqual(card["engagement"], {"needs_update": 2, "disputed": 1})
        self.assertEqual(card["relationships"]["children_total"], 0)
        self.assertEqual(result["library"], {"published_research_total": 11})
        self.assertEqual(result["destination"], "https://devtrellis.org")
        full = self.client.read(card["id"], card["version"])
        self.assertEqual(full["packet"]["body"], report()["body"])
        self.assertEqual(full["version"], card["version"])
        self.assertEqual(self.server.calls[-1][0], "/api/platform/community/cards/research-1?version=3")
        self.assertIsNone(self.server.calls[-1][2])

    def test_brief_preserves_limits_without_export_or_popularity(self):
        result = self.client.search("evidence", view="brief")
        self.assertEqual(result["view"], "brief")
        self.assertFalse(result["exportable"])
        card = result["items"][0]
        self.assertNotIn("body", card["packet"])
        self.assertNotIn("schema", card["packet"])
        self.assertEqual(card["packet"]["sources"], report()["sources"])
        self.assertEqual(card["engagement"], {"needs_update": 2, "disputed": 1})
        self.assertEqual(len(self.server.calls), 2)

    def test_partial_read_is_exact_and_does_not_generate_or_send_events(self):
        identifier = "a" * 32
        body = "# Result\nFirst finding.\n\n## Limits\nNot measured.\n"
        card = {"id": identifier, "version": 3, "packet": contribution_packet(**report(body=body))}
        with patch.object(self.client, "request", return_value=card) as request:
            outline = self.client.read(identifier, 3, view="outline")
            request.assert_called_once_with("/api/platform/community/cards/" + identifier, version=3)
        self.assertNotIn("body", outline["packet"])
        self.assertFalse(outline["complete_report"])
        with patch.object(self.client, "request", return_value=card) as request:
            part = self.client.read(identifier, 3, view="section", section_id=outline["sections"][1]["section_id"])
            self.assertEqual(request.call_count, 1)
        self.assertEqual(part["excerpt"]["text"], "## Limits\nNot measured.")
        self.assertEqual(part["packet"]["sources"], card["packet"]["sources"])
        self.assertEqual(card["packet"]["body"], body.strip())
        with patch.object(self.client, "request", return_value={**card, "version": 4}):
            with self.assertRaisesRegex(ValueError, "snapshot"):
                self.client.read(identifier, 3, view="outline")

    def test_partial_invalid_requests_fail_before_network(self):
        for version, view, section in [(None, "outline", None), (3, "wrong", None),
                                       (3, "full", "section-" + "a" * 64), (3, "section", "../private"),
                                       (None, "section", "section-" + "a" * 64)]:
            with self.subTest(view=view), self.assertRaises(ValueError):
                self.client.read("a" * 32, version, view=view, section_id=section)
        self.assertEqual(self.server.calls, [])

    def test_read_is_one_get_without_session_or_counter_write(self):
        for version, suffix in [(None, ""), (3, "?version=3"), (9223372036854775807, "?version=9223372036854775807")]:
            with self.subTest(version=version):
                self.server.calls.clear()
                self.client.read("research-1", version)
                self.assertEqual(len(self.server.calls), 1)
                path, headers, payload = self.server.calls[0]
                self.assertEqual(path, "/api/platform/community/cards/research-1" + suffix)
                self.assertIsNone(payload)
                self.assertNotIn("X-Radar-CSRF", headers)

    def test_version_query_cannot_expand_the_local_request_boundary(self):
        for version in [True, False, 0, -1, 9223372036854775808, 1.5, "3", "3&private=true"]:
            with self.subTest(version=version), self.assertRaises(ValueError):
                self.client.read("research-1", version)
        for path, payload in [("/api/platform/community/search", None),
                              ("/api/platform/community/cards/research-1/read", None),
                              ("/api/platform/community/cards/research-1?extra=1", None),
                              ("/api/platform/community/cards/research-1", {})]:
            with self.subTest(path=path, payload=payload), self.assertRaises(ValueError):
                self.client.request(path, payload, version=3)
        self.assertEqual(self.server.calls, [])

    def test_search_does_not_modify_source_cards_and_handles_empty_results(self):
        original = {"items": [{"id": "research-1", "packet": contribution_packet(**report())}], "total": 1}
        before = json.dumps(original, sort_keys=True)
        with patch.object(self.client, "request", return_value=original):
            result = self.client.search("evidence")
        self.assertEqual(json.dumps(original, sort_keys=True), before)
        self.assertIsNot(result["items"][0]["packet"], original["items"][0]["packet"])
        with patch.object(self.client, "request", return_value={"items": [], "total": 0}):
            self.assertEqual(self.client.search("missing"), {"items": [], "total": 0, "view": "summary",
                                                             "search": {"mode": "all", "fallback": True}})
        for invalid in ({}, {"items": [None]}, {"items": [{"packet": None}]}):
            with self.subTest(invalid=invalid), patch.object(self.client, "request", return_value=invalid):
                with self.assertRaisesRegex(ValueError, "geçersiz"):
                    self.client.search("evidence")

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

    def test_report_categories_match_local_packet_contract(self):
        for topic in KONULAR:
            with self.subTest(topic=topic):
                packet = contribution_packet(**report(topic=topic))
                self.assertEqual(paket_dogrula(packet)["provenance"]["topic"], topic)
        with self.assertRaisesRegex(ValueError, "Topic must be a category code"):
            contribution_packet(**report(topic="NVIDIA hosted NIM API access"))
        with self.assertRaisesRegex(ValueError, "Provider must be"):
            contribution_packet(**report(provider="OpenAI"))
        with self.assertRaises(ValueError):
            contribution_packet(**report(sources=[{"title": "Invalid source", "url": "not-a-url"}]))

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
                self.assertEqual(set(tools), {"lookup_source", "search_research", "read_research", "record_research_use", "open_submission_history", "preview_contribution", "contribution_decision"})
                self.assertEqual(tools["contribution_decision"].meta["ui"]["visibility"], ["app"])
                self.assertNotIn("author", tools["preview_contribution"].input_schema["properties"])
                self.assertNotIn("question", tools["preview_contribution"].input_schema["properties"])
                properties = tools["preview_contribution"].input_schema["properties"]
                self.assertEqual(set(properties["topic"]["enum"]), set(KONULAR))
                self.assertEqual(set(properties["provider"]["enum"]), {"codex", "claude", "nvidia", "unknown"})
                self.assertEqual(set(properties["license"]["enum"]), {"CC-BY-4.0", "CC0-1.0"})
                self.assertEqual(set(properties["language"]["enum"]), {"en", "tr"})
                before = len(self.server.calls)
                invalid = await session.call_tool("preview_contribution", report(topic="NVIDIA hosted NIM API access"))
                self.assertTrue(invalid.is_error)
                self.assertEqual(len(self.server.calls), before)
                self.assertTrue(tools["search_research"].annotations.read_only_hint)
                self.assertTrue(tools["read_research"].annotations.read_only_hint)
                self.assertFalse(tools["record_research_use"].annotations.read_only_hint)
                self.assertIn("report_url", tools["search_research"].description)
                self.assertIn("report_url", tools["read_research"].description)
                self.assertEqual(tools["search_research"].input_schema["properties"]["limit"]["default"], 3)
                search = await session.call_tool("search_research", {"query": "evidence", "view": "brief"})
                self.assertFalse(search.is_error)
                self.assertEqual(search.structured_content["total"], 1)
                self.assertEqual(search.structured_content["view"], "brief")
                self.assertNotIn("body", search.structured_content["items"][0]["packet"])
                self.assertNotIn(report()["body"], str(search.content))
                full = await session.call_tool("read_research", {"research_id": "research-1", "version": 3})
                self.assertFalse(full.is_error)
                self.assertEqual(full.structured_content["packet"]["body"], report()["body"])
                self.assertEqual(full.structured_content["version"], 3)
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

"""Yerel/MCP kaynak sınırlarını gerçek loopback ve sahte kamu hizmetiyle sınar."""
import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from mcp_topluluk import CommunityClient, contribution_packet, create_server
from radar.mcp_yerel import yerel_kopru
from tests.test_kaynak_hafizasi_istemci import record, source_record


class KaynakKopruTestleri(unittest.TestCase):
    def test_standalone_catalog_read_check_preserve_narrow_contract(self):
        remote = Mock()
        remote.katalog.return_value = {"sources": [source_record()], "model_calls": 0}
        remote.oku.return_value = record()
        remote.kontrol_et.return_value = {**record(), "check_started": False}
        with tempfile.TemporaryDirectory() as temp, patch("radar.platform_api.KaynakHafizasiIstemcisi", return_value=remote):
            with yerel_kopru(Path(temp)) as origin:
                client = CommunityClient(origin)
                self.assertEqual(client.source_memory()["sources"][0]["id"], "pilot")
                self.assertFalse(client.source_memory("pilot")["summary_reusable"])
                self.assertFalse(client.source_check("pilot", 600)["check_started"])
                remote.kontrol_et.assert_called_once_with("pilot", max_age_seconds=600, freshness_required=True)
                calls = len(remote.mock_calls)
                with self.assertRaisesRegex(ValueError, "HTTP 422"):
                    client.request("/api/platform/community/source-memory/pilot/check", {"private_query": "not sent"})
                self.assertEqual(len(remote.mock_calls), calls)
                self.assertEqual(remote.katalog.call_count, 1)
                self.assertEqual(remote.oku.call_count, 1)

    def test_source_identifiers_and_age_rejected_before_transport(self):
        client = CommunityClient()
        with patch.object(client, "request", side_effect=AssertionError("No request")):
            for identifier in ("https://example.org", "../private", "x?question=secret", "", "X", "a"):
                with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                    client.source_memory(identifier)
            for age in (True, 0, 59, 86401, "3600"):
                with self.subTest(age=age), self.assertRaises(ValueError):
                    client.source_check("pilot", age)
            for args in ((None, "a" * 32, None), (None, None, 1), ("pilot", "a" * 32, 1)):
                with self.assertRaises(ValueError):
                    client.source_memory(*args)

    def test_mapping_is_exact_public_source_language_not_hash_attestation(self):
        source = source_record()
        card = {"id": "a" * 32, "version": 2, "packet": contribution_packet(
            title="Public source", summary="Scope remains limited.", body="A public finding.",
            sources=[{"title": "Source", "url": source["url"] + "#heading"}],
            model="unknown", provider="unknown", license="CC-BY-4.0", language="en")}
        catalog = {"sources": [source, {**source, "id": "different-query", "url": source["url"] + "?version=2"},
                               {**source, "id": "different-language", "language": "tr"}], "model_calls": 0}
        client = CommunityClient()
        with patch.object(client, "read", return_value=card) as read, patch.object(client, "request", return_value=catalog) as request:
            result = client.source_memory(research_id=card["id"], version=2)
            read.assert_called_once_with(card["id"], 2)
            request.assert_called_once_with("/api/platform/community/source-memory")
        self.assertEqual([s["id"] for s in result["sources"]], [source["id"]])
        self.assertFalse(result["report_content_verified"])
        self.assertEqual(result["binding"], "url_and_language_only")
        self.assertEqual(result["sources"][0]["report_source_indexes"], [0])

    @unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK is not installed.")
    def test_real_sdk_read_only_and_explicit_check_are_separate(self):
        from mcp import Client
        client = CommunityClient()
        catalog = {"sources": [source_record()], "model_calls": 0}
        async def run():
            async with Client(create_server(client=client, source_memory=True)) as session:
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                self.assertTrue(tools["read_source_memory"].annotations.read_only_hint)
                self.assertFalse(tools["check_source_freshness"].annotations.read_only_hint)
                with patch.object(client, "request", return_value=catalog) as request:
                    result = await session.call_tool("read_source_memory", {})
                    self.assertEqual(result.structured_content, catalog)
                    request.assert_called_once_with("/api/platform/community/source-memory")
                with patch.object(client, "request", return_value={**record(), "check_started": False}) as request:
                    result = await session.call_tool("check_source_freshness", {"source_id": "pilot", "max_age_seconds": 600})
                    self.assertFalse(result.structured_content["check_started"])
                    request.assert_called_once_with("/api/platform/community/source-memory/pilot/check",
                        {"max_age_seconds": 600, "freshness_required": True}, timeout=55)
        asyncio.run(run())

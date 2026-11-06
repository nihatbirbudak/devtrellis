"""Kısa MCP adaylarının kanıtı koruduğunu ve tam paket diye sunulmadığını sınar."""

import copy
import json
import unittest

from mcp_topluluk import contribution_packet
from radar.arastirma_kisa import kisa_arama_sonucu


class KisaAramaTestleri(unittest.TestCase):
    def result(self):
        packet = contribution_packet(title="Public evidence", summary="Supported claim. Missing benchmark.",
            body="Full body excluded from candidate.",
            sources=[{"title": "Primary source", "url": "https://example.org/docs"}],
            model="unknown", provider="unknown", license="CC-BY-4.0")
        return {"items": [{"id": "a" * 32, "version": 2, "status": "published", "packet": packet,
            "report_url": "https://devtrellis.org/?card=" + "a" * 32 + "&version=2",
            "search_match": {"matched_terms": ["public"], "missing_terms": ["benchmark"],
                             "all_terms_matched": False, "answer_sufficiency": "not_assessed"},
            "engagement": {"needs_update": 2, "disputed": 1, "web_views": 100000},
            "relationships": {"parents": [], "children": [], "children_total": 0}}],
            "total": 1, "search": {"mode": "ranked", "fallback": False}}

    def test_evidence_and_limits_preserved_without_body_or_popularity(self):
        source = self.result()
        before = copy.deepcopy(source)
        result = kisa_arama_sonucu(source)
        self.assertEqual(source, before)
        self.assertFalse(result["exportable"])
        self.assertEqual(result["view"], "brief")
        card = result["items"][0]
        for field in ("summary", "sources", "license", "created_at", "updated_at"):
            self.assertEqual(card["packet"][field], source["items"][0]["packet"][field])
        for field in ("model", "provider", "evidence_status", "generated_at", "modified", "language"):
            self.assertEqual(card["packet"]["provenance"][field], source["items"][0]["packet"]["provenance"][field])
        self.assertNotIn("question", card["packet"]["provenance"])
        self.assertEqual(card["search_match"], source["items"][0]["search_match"])
        self.assertEqual(card["engagement"], {"needs_update": 2, "disputed": 1})
        self.assertNotIn("body", card["packet"])
        self.assertNotIn("schema", card["packet"])
        self.assertEqual(card["report_url"], source["items"][0]["report_url"])
        self.assertLess(len(json.dumps(result)), len(json.dumps(source)) + 100)

    def test_larger_evidence_is_explicitly_over_budget_never_silently_truncated(self):
        source = self.result()
        source["items"][0]["packet"]["sources"] = [
            {"title": "x" * 160, "url": "https://example.org/" + "a" * 500 + str(i)} for i in range(10)]
        result = kisa_arama_sonucu(source)
        self.assertTrue(result["size_notice"]["exceeded"])
        self.assertFalse(result["size_notice"]["content_truncated"])
        self.assertEqual(result["items"][0]["packet"]["sources"], source["items"][0]["packet"]["sources"])

    def test_exact_parent_versions_and_change_summary_survive(self):
        source = self.result()
        source["items"][0]["packet"]["lineage"] = {"parents": [{"research_id": "b"*32, "version": 7}],
                                                   "change_summary": "Corrected public claim."}
        result = kisa_arama_sonucu(source)
        self.assertEqual(result["items"][0]["packet"]["lineage"], source["items"][0]["packet"]["lineage"])

    def test_distinct_scope_and_nonempty_source_url_are_kept(self):
        source = self.result()
        source["items"][0]["packet"]["provenance"].update(question="A different public scope", source_url="https://example.org/paper")
        result = kisa_arama_sonucu(source)
        self.assertEqual(result["items"][0]["packet"]["provenance"]["question"], "A different public scope")
        self.assertEqual(result["items"][0]["packet"]["provenance"]["source_url"], "https://example.org/paper")

    def test_empty_and_invalid_results(self):
        self.assertEqual(kisa_arama_sonucu({"items": [], "total": 0})["items"], [])
        for result in ({}, {"items": [None]}, {"items": [{"packet": None}]}):
            with self.assertRaises(ValueError):
                kisa_arama_sonucu(result)


if __name__ == "__main__":
    unittest.main()

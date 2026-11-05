"""Gövdesiz aramanın kaynak, izin, tarih ve sürüm doğrulamasını koruduğunu sınar."""

import copy
import unittest

from mcp_topluluk import contribution_packet
from radar.arastirma_ozeti import arama_ozeti, paket_ozetini_dogrula
from radar.gelistirici_alani import paket_dogrula


class ArastirmaOzetiTestleri(unittest.TestCase):
    def packet(self, *, lineage=None):
        return contribution_packet(title="Public report", summary="Public summary with limits.",
            body="Full report with detailed evidence.", sources=[{"title": "Original", "url": "https://example.org/docs"}],
            model="unknown", provider="unknown", license="CC-BY-4.0", lineage=lineage)

    def test_summary_preserves_all_nonbody_fields_without_modifying_original(self):
        packet = self.packet(lineage={"parents": [{"research_id": "a" * 32, "version": 2}], "change_summary": "New evidence."})
        full = {"items": [{"id": "b" * 32, "version": 4, "packet": packet,
            "engagement": {"needs_update": 2, "disputed": 1},
            "relationships": {"parents": [], "children": [], "children_total": 0}}],
            "total": 1, "library": {"published_research_total": 11}}
        original = copy.deepcopy(full)
        summary = arama_ozeti(full)
        self.assertEqual(full, original)
        expected = copy.deepcopy(full)
        expected["view"] = "summary"
        expected["items"][0]["packet"].pop("body")
        self.assertEqual(summary, expected)
        self.assertEqual(paket_ozetini_dogrula(summary["items"][0]["packet"]), expected["items"][0]["packet"])
        with self.assertRaises(ValueError):
            paket_dogrula(summary["items"][0]["packet"])

    def test_summary_validates_each_supported_schema_and_rejects_missing_metadata(self):
        base = self.packet()
        legacy = {key: value for key, value in base.items() if key != "provenance"}
        legacy["schema_version"] = 1
        extension = self.packet(lineage={"parents": [{"research_id": "a" * 32, "version": 2}], "change_summary": "New evidence."})
        for packet in (legacy, base, extension):
            summary = {key: value for key, value in packet.items() if key != "body"}
            with self.subTest(schema=packet["schema_version"]):
                self.assertEqual(paket_ozetini_dogrula(summary), summary)
                required = packet.keys() - {"body"}
                if packet["schema_version"] == 3:
                    required = required - {"provenance"}  # Mevcut schema3 sözleşmesinde isteğe bağlı.
                for field in required:
                    broken = {key: value for key, value in summary.items() if key != field}
                    with self.subTest(field=field), self.assertRaises(ValueError):
                        paket_ozetini_dogrula(broken)
        for changed in ({"sources": [{"title": "Private", "url": "http://127.0.0.1/private"}]},
                        {"license": "MIT"}, {"created_at": "today"}, {"body": "hidden body"},
                        {"private_context": "unexpected"}):
            summary = {key: value for key, value in base.items() if key != "body"}
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                paket_ozetini_dogrula({**summary, **changed})


if __name__ == "__main__":
    unittest.main()

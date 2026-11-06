"""Bölüm seçiminin özgün metni, kamu kökenini ve tam sürüm bağını kayıpsız koruduğunu sınar."""

import copy
import hashlib
import unittest

from radar.arastirma_bolumleri import rapor_gorunumu


class ArastirmaBolumleriTestleri(unittest.TestCase):
    def card(self, body):
        return {"id": "a" * 32, "version": 7, "status": "published", "report_url": "https://example.org/?card=" + "a" * 32 + "&version=7",
                "engagement": {"helpful": 3}, "relationships": {"parents": [], "children": []},
                "packet": {"kind": "research", "schema": "ufuk-share", "schema_version": 3,
                           "title": "Kaynaklı kamu araştırması", "summary": "Kapsam ve sınırlar.", "body": body,
                           "sources": [{"title": "Belge", "url": "https://example.org/docs"}],
                           "provenance": {"model": "unknown", "evidence_status": "unverified"},
                           "lineage": {"parents": [{"research_id": "b" * 32, "version": 2}], "change_summary": "Ek kaynak."},
                           "author": "Anonymous", "license": "CC-BY-4.0"}}

    def assert_lossless(self, card):
        original = copy.deepcopy(card)
        outline = rapor_gorunumu(card, "outline")
        self.assertNotIn("body", outline["packet"])
        self.assertFalse(outline["complete_report"])
        self.assertEqual(outline["body_sha256"], hashlib.sha256(card["packet"]["body"].encode("utf-8")).hexdigest())
        self.assertEqual(outline["offset_unit"], "unicode_codepoint")
        self.assertEqual(outline["body_char_count"], len(card["packet"]["body"]))
        texts, position = [], 0
        for section in outline["sections"]:
            self.assertEqual(section["start"], position)
            self.assertLessEqual(section["char_count"], 4000)
            selected = rapor_gorunumu(card, "section", section["section_id"])
            self.assertNotIn("body", selected["packet"])
            self.assertNotIn("sections", selected)
            self.assertEqual(selected["body_sha256"], outline["body_sha256"])
            self.assertFalse(selected["complete_report"])
            self.assertEqual({key: value for key, value in selected["excerpt"].items() if key != "text"}, section)
            self.assertEqual(selected["excerpt"]["text"], card["packet"]["body"][section["start"]:section["end"]])
            self.assertEqual(section["char_count"], len(selected["excerpt"]["text"]))
            texts.append(selected["excerpt"]["text"])
            position = section["end"]
        self.assertEqual("".join(texts), card["packet"]["body"])
        self.assertEqual(card, original)
        return outline

    def test_full_keeps_shape_and_returned_metadata_cannot_mutate_input(self):
        card = self.card("# Title\nFull body.")
        original = copy.deepcopy(card)
        for view in ("full", "outline"):
            result = rapor_gorunumu(card, view)
            if view == "full":
                self.assertEqual(result, original)
            result["packet"]["sources"][0]["title"] = "Changed"
            result["packet"]["lineage"]["parents"][0]["version"] = 99
            result["engagement"]["helpful"] = 999
            self.assertEqual(card, original)
        outline = rapor_gorunumu(card, "outline")
        selected = rapor_gorunumu(card, "section", outline["sections"][0]["section_id"])
        selected["packet"]["provenance"]["model"] = "Different"
        selected["relationships"]["parents"].append("Unexpected")
        self.assertEqual(card, original)

    def test_metadata_and_heading_context_survive_exact_public_version(self):
        card = self.card("Preamble.\n\n# First\nA.\n## Child ##\nB.\n# First\nC.")
        outline = self.assert_lossless(card)
        sections = outline["sections"]
        self.assertEqual([item["title"] for item in sections], [card["packet"]["title"], "First", "Child", "First"])
        self.assertEqual(sections[2]["heading_context"], [{"level": 1, "title": "First"}, {"level": 2, "title": "Child"}])
        self.assertEqual(sections[3]["heading_context"], [{"level": 1, "title": "First"}])
        self.assertEqual(len({item["section_id"] for item in sections}), 4)
        for key in card.keys() - {"packet"}:
            self.assertEqual(outline[key], card[key])
        self.assertEqual(outline["packet"], {key: value for key, value in card["packet"].items() if key != "body"})

    def test_empty_headingless_and_unicode_preserve_original_offsets_and_newlines(self):
        for body in ("", "Plain report without heading.", "İzmir 😀 e\u0301\r\n\r\n# Ölçüm\r\nı İ ş ğ 😀\r\n", "\n\n", "#\n## ###\n"):
            with self.subTest(body=body):
                outline = self.assert_lossless(self.card(body))
                self.assertEqual(outline["outline_fallback"], None)
                if not body:
                    self.assertEqual(outline["sections"], [])

    def test_atx_rules_and_fenced_code_do_not_invent_headings(self):
        body = ("# Real\n```python\n# Fake one\n~~~~\n## Still code\n``\n# Still code too\n````\n"
                "## Next ###\n~~~lang\n# Fake two\n```\n~~~\n"
                "    # Indented code\n####### Seven hashes\n#No space\n   ### Third\n"
                "#### Inline `code` ###\n")
        outline = self.assert_lossless(self.card(body))
        self.assertEqual([item["title"] for item in outline["sections"]], ["Real", "Next", "Third", "Inline `code`"])
        for fence in ("```", "~~~~"):
            body = "# Real\n" + fence + "\n# Unclosed code\n## Still code"
            self.assertEqual(len(self.assert_lossless(self.card(body))["sections"]), 1)

    def test_long_sections_prefer_paragraph_then_line_and_expose_continuations(self):
        body = "# Report\n" + "a" * 2500 + "\n\n" + "b" * 2500 + "\n" + "c" * 1000
        outline = self.assert_lossless(self.card(body))
        first, second = outline["sections"]
        self.assertEqual(first["split_reason"], "paragraph")
        self.assertTrue(body[:first["end"]].endswith("\n\n"))
        self.assertEqual(first["next_section_id"], second["section_id"])
        self.assertEqual(first["remaining_chars"], len(body) - first["end"])
        self.assertEqual((first["part"], second["part"], second["parts"]), (1, 2, 2))
        self.assertTrue(second["continuation"])
        self.assertEqual(second["remaining_chars"], 0)
        self.assertEqual(second["heading_context"], first["heading_context"])
        self.assertEqual(second["heading_start"], 0)
        self.assertIsNone(second["next_section_id"])
        lines = self.assert_lossless(self.card("x" * 2200 + "\n" + "y" * 2200))
        self.assertEqual(lines["sections"][0]["split_reason"], "line")

    def test_single_long_code_line_is_bounded_explicit_and_lossless(self):
        body = "# Code\n```\n" + "😀" * 10000 + "\n```\n"
        outline = self.assert_lossless(self.card(body))
        self.assertTrue(any(item["split_reason"] == "character_limit" for item in outline["sections"]))
        self.assertTrue(all(item["title"] == "Code" for item in outline["sections"]))
        self.assertTrue(all(item["parts"] == len(outline["sections"]) for item in outline["sections"]))
        crlf = self.assert_lossless(self.card("x" * 3999 + "\r\n" + "y" * 4500))
        self.assertNotEqual(crlf["sections"][0]["end"], 4000)

    def test_excess_headings_fall_back_without_omitting_original_text(self):
        body = "".join(f"# Heading {index}\nText {index}.\n" for index in range(100))
        outline = self.assert_lossless(self.card(body))
        self.assertEqual(outline["outline_fallback"], "too_many_headings")
        self.assertLessEqual(len(outline["sections"]), 32)
        self.assertTrue(all(item["heading_level"] == 0 and item["heading_context"] == [] for item in outline["sections"]))
        long_heading = self.assert_lossless(self.card("# " + "x" * 5000 + "\nBody"))
        self.assertEqual(long_heading["outline_fallback"], "heading_too_long")
        self.assertTrue(all(item["title"] == "Kaynaklı kamu araştırması" for item in long_heading["sections"]))

    def test_body_limit_includes_exact_boundary_and_rejects_nonfull_or_invalid_input(self):
        self.assert_lossless(self.card("x" * 12000))
        invalid = [None, {}, {**self.card("body"), "version": True}, {**self.card("body"), "version": 0},
                   {**self.card("body"), "id": "../private"}, self.card("x" * 12001), self.card(None), self.card("\ud800")]
        missing_body = self.card("body")
        del missing_body["packet"]["body"]
        invalid.append(missing_body)
        project = self.card("body")
        project["packet"]["kind"] = "project"
        invalid.append(project)
        for card in invalid:
            with self.subTest(card=repr(card)[:120]), self.assertRaises(ValueError):
                rapor_gorunumu(card, "outline")

    def test_invalid_views_ids_and_outdated_version_or_body_are_rejected(self):
        card = self.card("# Exact\nOriginal body")
        section_id = rapor_gorunumu(card, "outline")["sections"][0]["section_id"]
        for view in (None, False, "summary", "", [], {}):
            with self.subTest(view=view), self.assertRaises(ValueError):
                rapor_gorunumu(card, view)
        for view in ("full", "outline"):
            with self.assertRaises(ValueError):
                rapor_gorunumu(card, view, section_id)
        for invalid_id in (None, "", [], {}, 1, "../private", section_id.upper(), "section-" + "f" * 64):
            with self.subTest(invalid_id=invalid_id), self.assertRaises(ValueError):
                rapor_gorunumu(card, "section", invalid_id)
        for changed in ({**card, "version": 8}, {**card, "id": "c" * 32},
                        {**card, "packet": {**card["packet"], "body": "# Exact\nDifferent body"}}):
            with self.assertRaises(ValueError):
                rapor_gorunumu(changed, "section", section_id)
        with self.assertRaises(ValueError):
            rapor_gorunumu(self.card(""), "section", section_id)


if __name__ == "__main__":
    unittest.main()

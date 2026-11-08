"""Bölüm izinin kanıt yapısını korumasını ve belirsiz değişimleri aynı saymamasını sınar."""

import copy
import json
import unittest

from radar.kaynak_bolumleri import (MAX_BODY_BYTES, KaynakBolumHatasi,
                                   compare_documents, extract_document)


class DocumentSectionTests(unittest.TestCase):
    URL = "https://docs.example.org/en/v1/guide"

    def doc(self, content, **options):
        return extract_document(("<main>" + content + "</main>").encode("utf-8"), options.get("url", self.URL))

    def delta(self, before, after):
        return compare_documents(self.doc(before), self.doc(after))

    def assert_changed(self, before, after):
        delta = self.delta(before, after)
        self.assertEqual(delta["status"], "changed")
        self.assertTrue(delta["changed"] or delta["added"] or delta["removed"] or delta["moved"])
        return delta

    def test_no_raw_body_title_link_or_anchor_is_retained(self):
        result = self.doc('<h1 id="anchor-sentinel">Heading sentinel</h1><p>Body sentinel <a href="/target-sentinel">link</a></p>')
        encoded = json.dumps(result)
        for fragment in ("anchor-sentinel", "Heading sentinel", "Body sentinel", "target-sentinel", "<h1"):
            self.assertNotIn(fragment, encoded)
        self.assertEqual(set(result), {"policy_version", "extractor_version", "canonical_url", "body_hash", "sections", "ambiguous"})
        self.assertEqual(result["policy_version"], "public-doc-anonymous-v2")
        self.assertEqual(result["extractor_version"], "dom-sections-v2.generic")
        self.assertRegex(result["sections"][0]["section_id"], r"^[a-f0-9]{64}$")

    def test_determinism_bytes_string_and_host_fragment_normalization(self):
        body = "<article><h1>A</h1><p>Türkçe</p></article>"
        self.assertEqual(extract_document(body, self.URL), extract_document(body.encode(), self.URL + "#part"))
        doc = extract_document(body, self.URL)
        self.assertEqual(compare_documents(doc, doc)["status"], "unchanged")

    def test_safe_static_comment_and_container_whitespace_noise(self):
        before = "<h1>A</h1><p>one two</p>"
        after = "\n<!-- layout -->\n<h1>A</h1>\n<p>one<!-- ignored --> two</p>\n"
        self.assertEqual(self.delta(before, after)["status"], "unchanged")

    def test_normal_prose_whitespace_is_collapsed(self):
        self.assertEqual(self.delta("<h1>A</h1><p>one two</p>", "<h1>A</h1><p>one\n \t two</p>")["status"], "unchanged")

    def test_changed_prose_is_assigned_to_section(self):
        delta = self.assert_changed("<h1>A</h1><p>old</p><h2>B</h2><p>keep</p>", "<h1>A</h1><p>new</p><h2>B</h2><p>keep</p>")
        self.assertEqual(len(delta["changed"]), 1)
        self.assertEqual(delta["added"], [])
        self.assertEqual(delta["removed"], [])

    def test_static_acronym_preserves_tag_title_and_text(self):
        before = '<h1>A</h1><p><acronym title="Structured Query Language">SQL</acronym></p>'
        original = self.doc(before)
        self.assertFalse(original["ambiguous"])
        for after in (before.replace("Structured Query Language", "Different expansion"),
                      before.replace(">SQL<", ">Different text<"),
                      before.replace("acronym", "abbr")):
            with self.subTest(after=after):
                current = self.doc(after)
                self.assertNotEqual(original["body_hash"], current["body_hash"])
                delta = compare_documents(original, current)
                self.assertEqual(delta["status"], "changed")
                self.assertEqual(len(delta["changed"]), 1)

    def test_code_and_pre_whitespace_are_meaningful(self):
        for tag in ("code", "pre", "kbd", "samp"):
            with self.subTest(tag=tag):
                self.assert_changed(f"<h1>A</h1><{tag}>x  y\n z</{tag}>", f"<h1>A</h1><{tag}>x y\n  z</{tag}>")

    def test_link_target_and_fragment_are_meaningful(self):
        for target in ("/different", "/same#two", "https://other.example.org/same#one"):
            with self.subTest(target=target):
                self.assert_changed('<h1>A</h1><p><a href="/same#one">same label</a></p>', f'<h1>A</h1><p><a href="{target}">same label</a></p>')
        self.assertEqual(self.delta('<h1>A</h1><a href="/same">link</a>', '<h1>A</h1><a href="https://docs.example.org/same">link</a>')["status"], "unchanged")

    def test_table_header_scope_relationship_and_span_are_meaningful(self):
        prefix = '<h1>A</h1><table><caption>Rate</caption><tr><th id="h" scope="col">Header</th><td headers="h" colspan="1">Value</td></tr></table>'
        for changed in (prefix.replace('scope="col"', 'scope="row"'), prefix.replace('headers="h"', 'headers="other"'),
                        prefix.replace('colspan="1"', 'colspan="2"'), prefix.replace("<th ", "<td ").replace("</th>", "</td>")):
            self.assert_changed(prefix, changed)

    def test_definition_list_and_footnote_associations_are_meaningful(self):
        self.assert_changed("<h1>A</h1><dl><dt>term</dt><dd>meaning</dd></dl>", "<h1>A</h1><dl><dd>term</dd><dt>meaning</dt></dl>")
        before = '<h1>A</h1><p>Claim<a href="#note1" role="doc-noteref">1</a></p><ol><li id="note1" role="doc-footnote">Evidence</li></ol>'
        self.assert_changed(before, before.replace('href="#note1"', 'href="#note2"'))
        self.assert_changed(before, before.replace(">Evidence<", ">Different evidence<"))

    def test_section_add_remove_and_reorder_are_separate(self):
        a, b, c = "<h1>A</h1><p>a</p>", "<h1>B</h1><p>b</p>", "<h1>C</h1><p>c</p>"
        added = self.delta(a + b, a + c + b)
        self.assertEqual(len(added["added"]), 1)
        self.assertEqual(added["moved"], [])
        removed = self.delta(a + c + b, a + b)
        self.assertEqual(len(removed["removed"]), 1)
        self.assertEqual(removed["moved"], [])
        moved = self.delta(a + b, b + a)
        self.assertEqual(len(moved["moved"]), 2)
        self.assertEqual(moved["changed"], [])

    def test_heading_rename_with_anchor_and_reparenting_are_tracked(self):
        changed = self.delta('<h1 id="stable">A</h1><p>x</p>', '<h1 id="stable">Renamed</h1><p>x</p>')
        self.assertEqual(len(changed["changed"]), 1)
        self.assertEqual(changed["added"], [])
        moved = self.delta('<h1>A</h1><h2 id="child">Child</h2><p>x</p><h1>B</h1>', '<h1>A</h1><h1>B</h1><h2 id="child">Child</h2><p>x</p>')
        self.assertTrue(moved["moved"])

    def test_duplicate_headings_are_distinct_and_need_unique_anchors(self):
        same = self.doc("<h1>A</h1><p>first</p><h1>A</h1><p>second</p>")
        self.assertNotEqual(same["sections"][0]["section_id"], same["sections"][1]["section_id"])
        self.assertTrue(same["ambiguous"])
        self.assertEqual(compare_documents(same, same)["status"], "ambiguous")
        anchored = self.doc('<h1 id="first">A</h1><p>first</p><h1 id="second">A</h1><p>second</p>')
        self.assertFalse(anchored["ambiguous"])
        self.assertEqual(compare_documents(anchored, anchored)["status"], "unchanged")
        duplicate_anchor = self.doc('<h1 id="first">A</h1><h1 id="first">B</h1>')
        self.assertTrue(duplicate_anchor["ambiguous"])

    def test_static_hidden_text_and_hidden_heading_are_not_content(self):
        before = '<h1>A</h1><p>x</p>'
        after = '<h1>A<span hidden>invisible heading text</span></h1><h2 hidden>hidden</h2><p>x</p><div style="display:none">hidden</div>'
        self.assertEqual(self.delta(before, after)["status"], "unchanged")

    def test_aria_hidden_visual_text_is_still_content(self):
        self.assert_changed('<h1>A</h1><p aria-hidden="true">10</p>', '<h1>A</h1><p aria-hidden="true">20</p>')
        result = self.doc('<h1>A</h1><h2 aria-hidden="true">Visible heading</h2><p>x</p>')
        self.assertEqual(len(result["sections"]), 2)

    def test_inline_neighbor_space_is_meaningful(self):
        for tag in ("span", "code", 'div style="display:inline"'):
            closing = tag.split()[0]
            before = f'<h1>A</h1><{tag}>not</{closing}> <{tag}>safe</{closing}>'
            after = f'<h1>A</h1><{tag}>not</{closing}><{tag}>safe</{closing}>'
            with self.subTest(tag=tag):
                self.assert_changed(before, after)

    def test_active_content_even_hidden_is_rejected(self):
        for extra in ('<script hidden>sentinel</script>', '<iframe hidden src="/x"></iframe>',
                      '<span hidden onclick="sentinel()">x</span>', '<div contenteditable="false">x</div>',
                      '<template><p>x</p></template>', '<form><p>x</p></form>'):
            with self.subTest(extra=extra), self.assertRaises(KaynakBolumHatasi) as caught:
                self.doc("<h1>A</h1>" + extra)
            self.assertEqual(str(caught.exception), "unsupported_active_content")

    def test_unsupported_evidence_and_ambiguous_roots_are_rejected(self):
        for extra in ('<img src="/chart.png" alt="chart">', '<svg><text>chart</text></svg>',
                      '<math><mi>x</mi></math>', '<canvas>chart</canvas>', '<details><summary>x</summary>y</details>',
                      '<video></video>', '<custom-evidence>claim</custom-evidence>'):
            with self.subTest(extra=extra), self.assertRaises(KaynakBolumHatasi):
                self.doc("<h1>A</h1>" + extra)
        for body in ("<div>no root</div>", "<main>A</main><article>B</article>",
                     "<main>outer<article>inner</article></main>", "<main><div id='docContent'>inner</div></main>"):
            with self.subTest(body=body), self.assertRaisesRegex(KaynakBolumHatasi, "ambiguous_document_root"):
                extract_document(body, self.URL)
        self.assertEqual(len(extract_document('<main id="docContent">A</main>', self.URL)["sections"]), 1)

    def test_unsafe_link_and_base_are_not_silently_dropped(self):
        for target in ("javascript:alert(1)", "http://docs.example.org/x", "/x?token=secret", "https://localhost/x"):
            with self.subTest(target=target), self.assertRaisesRegex(KaynakBolumHatasi, "unsupported_link_target"):
                self.doc(f'<h1>A</h1><a href="{target}">link</a>')
        with self.assertRaisesRegex(KaynakBolumHatasi, "unsupported_document_base"):
            extract_document('<head><base href="https://other.example.org/"></head><main>x</main>', self.URL)

    def test_invalid_body_and_limits_fail_with_fixed_error(self):
        for body in (b"\xff", "", b"\0", None, b"x" * (MAX_BODY_BYTES + 1)):
            with self.subTest(body_type=type(body)), self.assertRaisesRegex(KaynakBolumHatasi, "invalid_document_body"):
                extract_document(body, self.URL)
        with self.assertRaisesRegex(KaynakBolumHatasi, "document_limit_exceeded"):
            self.doc("<div>" * 81 + "text" + "</div>" * 81)
        with self.assertRaisesRegex(KaynakBolumHatasi, "document_limit_exceeded"):
            self.doc("".join(f"<h1>Section {n}</h1>" for n in range(257)))

    def test_invalid_table_or_heading_structure_is_rejected(self):
        for fragment in ("<td>value</td>", '<table><tr><td colspan="0">x</td></tr></table>',
                         "<pre><h2>not a section</h2></pre>", "<table><tr><td><h2>not a section</h2></td></tr></table>"):
            with self.subTest(fragment=fragment), self.assertRaises(KaynakBolumHatasi):
                self.doc("<h1>A</h1>" + fragment)

    def test_manifest_extra_raw_data_bad_parent_and_bad_hash_are_rejected(self):
        base = self.doc("<h1>A</h1><p>x</p>")
        bad_cases = []
        extra = copy.deepcopy(base); extra["raw_html"] = "private"; bad_cases.append(extra)
        extra = copy.deepcopy(base); extra["sections"][0]["text"] = "private"; bad_cases.append(extra)
        extra = copy.deepcopy(base); extra["sections"][0]["parent_id"] = "a" * 64; bad_cases.append(extra)
        extra = copy.deepcopy(base); extra["sections"][0]["section_hash"] = "not a hash"; bad_cases.append(extra)
        extra = copy.deepcopy(base); extra["sections"][0]["order"] = True; bad_cases.append(extra)
        extra = copy.deepcopy(base); extra["policy_version"] = "not a version\nprivate text"; bad_cases.append(extra)
        for extra in bad_cases:
            with self.subTest(extra=extra), self.assertRaisesRegex(KaynakBolumHatasi, "invalid_document_manifest"):
                compare_documents(base, extra)

    def test_metadata_or_version_drift_never_reports_unchanged(self):
        base = self.doc("<h1>A</h1><p>x</p>")
        for field, value in (("heading_hash", "b" * 64), ("anchor_hash", "c" * 64), ("duplicate_heading", True)):
            changed = copy.deepcopy(base); changed["sections"][0][field] = value
            with self.subTest(field=field):
                self.assertEqual(compare_documents(base, changed)["status"], "ambiguous")
        for field, value in (("extractor_version", "future"), ("policy_version", "future"), ("canonical_url", self.URL + "/")):
            changed = copy.deepcopy(base); changed[field] = value
            self.assertEqual(compare_documents(base, changed)["status"], "ambiguous")
        unexplained = copy.deepcopy(base); unexplained["body_hash"] = "d" * 64
        self.assertEqual(compare_documents(base, unexplained)["status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()

"""K0 ikinci dilim: belge üreticisi sözleşmelerinin altın fixture'larla çıkarım, değişim ve ret davranışını sınar."""

import json
from pathlib import Path
import unittest

from radar.kaynak_bolumleri import KaynakBolumHatasi, compare_documents, extract_document
from radar.kaynak_kimligi import canonical_hash

FIXTURES = Path(__file__).parent / "fixtures" / "k0_aileleri"

# Her satır: fixture, adres, beklenen sözleşme, bölüm sayısı, (sözleşme arayüzü değişikliği), (içerik değişikliği)
CASES = {
    "docbook-section": {
        "url": "https://www.postgresql.org/docs/17/ddl-constraints.html", "family": "docbook-1", "sections": 5,
        "chrome": [('Prev</a></td>', 'Previous</a></td>'), ('5.6. System Columns</td>', '5.6. Hidden Columns</td>')],
        "content": ('only for the row being inserted', 'only for each row being inserted')},
    "docbook-refentry": {
        "url": "https://mosquitto.org/man/mosquitto-conf-5.html", "family": "docbook-1", "sections": 9,
        "chrome": [('Copyright notice for the website', 'Changed site notice')],
        "content": ('Send log messages', 'Write log messages')},
    "sphinx": {
        "url": "https://docs.example.org/en/2.1/configuration.html", "family": "sphinx-1", "sections": 5,
        "chrome": [('title="Link to this heading">¶</a></h1>', 'title="Permalink">#</a></h1>'),
                   ('<h3>Navigation</h3>', '<h3>Site map</h3>')],
        "content": ('Environment variables override the file.', 'Environment variables never override the file.')},
    "mkdocs-material": {
        "url": "https://docs.example.org/guide/install/", "family": "mkdocs-material-1", "sections": 5,
        "chrome": [('April 2, 2024', 'June 9, 2025'), ('title="Edit this page"', 'title="Edit on GitHub"'),
                   ('title="Permanent link">&para;</a></h1>', 'title="Link">#</a></h1>')],
        "content": ('needs a C compiler', 'needs a Rust compiler')},
    "docusaurus": {
        "url": "https://docs.example.org/docs/quick-start", "family": "docusaurus-1", "sections": 5,
        "chrome": [('aria-label="Copy code to clipboard" title="Copy"', 'aria-label="Copy" title="Copy code"'),
                   ('May 1, 2024', 'June 9, 2025'), ('codeBlockContent_biex', 'codeBlockContent_Zq9x'),
                   ('aria-label="Direct link to Install"', 'aria-label="Link to Install"')],
        "content": ('Node.js 18 or newer', 'Node.js 20 or newer')},
    "hugo-docsy": {
        "url": "https://docs.example.org/docs/concepts/", "family": "hugo-docsy-1", "sections": 5,
        "chrome": [('Last modified May 3, 2024', 'Last modified June 9, 2025'), ('0123abc', '4567def'),
                   ('Edit this page</a></div></aside>', 'Edit</a></div></aside>')],
        "content": ('up to ten projects', 'up to twenty projects')},
    "starlight": {
        "url": "https://esphome.io/components/sensor/index.html", "family": "starlight-1", "sections": 4,
        "chrome": [('Section titled “offset”', 'Link to “offset”'), ('/_astro/ec.8zarh.js', '/_astro/ec.Q1w2e.js'),
                   ('title="Copy to clipboard"', 'title="Copy"'), ('astro-b5vgtrtx', 'astro-zz11yy22'),
                   ('Edit page</a>', 'Edit this page</a>')],
        "content": ('Adds a constant value', 'Adds a fixed value')},
    "vuepress1": {
        "url": "https://www.openhab.org/docs/configuration/items.html", "family": "vuepress1-1", "sections": 5,
        "chrome": [('class="header-anchor">#</a> Types', 'class="header-anchor">¶</a> Types'),
                   ('5/2/2024, 10:00:00 AM', '6/9/2025, 09:00:00 AM')],
        "content": ('A number item can carry a unit.', 'A number item can carry a dimension.')},
    "nodered": {
        "url": "https://nodered.org/docs/user-guide/messages", "family": "nodered-docs-1", "sections": 4,
        "chrome": [('Copyright notice outside the document.', 'Another footer.'),
                   ('className = "active"', 'className = "current"')],
        "content": ('removes it from the old location', 'removes it from the previous location')},
}


def page(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


def extract(name, html=None, url=None):
    return extract_document(html if html is not None else page(name), url or CASES[name]["url"])


class FamilyFixtureTests(unittest.TestCase):
    def test_each_family_is_selected_once_and_extracts_unambiguous_sections(self):
        for name, case in CASES.items():
            with self.subTest(fixture=name):
                document = extract(name)
                self.assertEqual(document["extractor_version"], "dom-sections-v2." + case["family"])
                self.assertFalse(document["ambiguous"])
                self.assertEqual(len(document["sections"]), case["sections"])
                self.assertEqual(compare_documents(document, extract(name))["status"], "unchanged")

    def test_declared_chrome_and_outside_root_changes_keep_the_document_unchanged(self):
        for name, case in CASES.items():
            for old, new in case["chrome"]:
                with self.subTest(fixture=name, chrome=old):
                    html = page(name)
                    self.assertIn(old, html)
                    changed = extract(name, html.replace(old, new))
                    self.assertEqual(changed["body_hash"], extract(name)["body_hash"])
                    self.assertEqual(compare_documents(extract(name), changed)["status"], "unchanged")

    def test_one_content_change_is_reported_in_exactly_one_section(self):
        for name, case in CASES.items():
            old, new = case["content"]
            with self.subTest(fixture=name):
                html = page(name)
                self.assertEqual(html.count(old), 1)
                delta = compare_documents(extract(name), extract(name, html.replace(old, new)))
                self.assertEqual(delta["status"], "changed")
                self.assertEqual((len(delta["changed"]), delta["added"], delta["removed"], delta["moved"]), (1, [], [], []))

    def test_no_heading_text_link_target_or_body_text_is_retained(self):
        for name in CASES:
            with self.subTest(fixture=name):
                encoded = json.dumps(extract(name))
                for fragment in ("Example", "precedence.png", "issues?q=", "<h", "payload", "Options", "Limits", "README"):
                    self.assertNotIn(fragment, encoded)


class DuplicateHeadingTests(unittest.TestCase):
    def test_docbook_admonition_titles_are_content_not_sections(self):
        document = extract("docbook-section")
        # sect1, iki sect2 ve iki sect3: beş bölüm; iki not ve bir ipucu başlığı bölüm açmaz.
        self.assertEqual([row["level"] for row in document["sections"]], [2, 3, 4, 3, 4])
        html = page("docbook-section")
        delta = compare_documents(document, extract("docbook-section", html.replace(
            "Two null values are not considered equal", "Two null values are never considered equal")))
        self.assertEqual(len(delta["changed"]), 1)

    def test_repeated_titles_are_bound_by_source_anchors_not_order(self):
        for name, title in (("docbook-section", "Examples"), ("docbook-refentry", "Examples"), ("sphinx", "Options"),
                            ("mkdocs-material", "Example"), ("docusaurus", "Requirements"), ("hugo-docsy", "Limits"),
                            ("vuepress1", "Example")):
            with self.subTest(fixture=name):
                document = extract(name)
                repeated = [row for row in document["sections"] if row["duplicate_heading"]]
                self.assertEqual(len(repeated), 2)
                self.assertTrue(all(row["anchor_hash"] for row in repeated))
                self.assertEqual(len({row["anchor_hash"] for row in repeated}), 2)
                self.assertFalse(document["ambiguous"])

    def test_duplicate_without_anchor_stays_ambiguous(self):
        html = page("docbook-refentry").replace('<a name="idm3"></a>', "").replace('<a name="idm5"></a>', "")
        self.assertTrue(extract("docbook-refentry", html)["ambiguous"])
        html = page("sphinx").replace('<section id="file-format-options">', "<section>").replace(
            '<section id="environment-options">', "<section>")
        self.assertTrue(extract("sphinx", html)["ambiguous"])

    def test_swapping_two_same_titled_sections_is_detected_by_anchor(self):
        html = page("docbook-section")
        first = html[html.index('<div class="sect3" id="DDL-CONSTRAINTS-CHECK-EXAMPLES">'):]
        first = first[:first.index("</div>\n      </div>") + 6]
        second = html[html.index('<div class="sect3" id="DDL-CONSTRAINTS-UNIQUE-EXAMPLES">'):]
        second = second[:second.index("</div>\n      </div>") + 6]
        swapped = html.replace(first, "@@FIRST@@").replace(second, first).replace("@@FIRST@@", second)
        delta = compare_documents(extract("docbook-section"), extract("docbook-section", swapped))
        self.assertEqual(delta["status"], "changed")
        self.assertEqual(len(delta["moved"]), 2)


class EvidenceAndRejectionTests(unittest.TestCase):
    def test_media_and_embeds_are_represented_by_address_and_alt_text(self):
        for name, old, new in (("sphinx", "_images/precedence.png", "_images/precedence-v2.png"),
                               ("mkdocs-material", 'alt="Build steps"', 'alt="Build stages"'),
                               ("starlight", 'src="/_astro/offset.Dq3y2.webp"', 'src="/_astro/offset.Zz9.webp"'),
                               ("nodered", "embed/abcdefghijk", "embed/zyxwvutsrqp"),
                               ("nodered", "images/debug-message.png", "images/debug-message-2.png")):
            with self.subTest(fixture=name, media=old):
                delta = compare_documents(extract(name), extract(name, page(name).replace(old, new)))
                self.assertEqual(delta["status"], "changed")
                self.assertEqual(len(delta["changed"]), 1)
        # Duyarlı görsel varyantları (srcset) ve sunum öznitelikleri temsil dışıdır; bu sözleşmede yazılıdır.
        unchanged = page("starlight").replace("offset.Dq3y2_400.webp 400w", "offset.Dq3y2_480.webp 480w").replace('loading="lazy"', 'loading="eager"')
        self.assertEqual(compare_documents(extract("starlight"), extract("starlight", unchanged))["status"], "unchanged")

    def test_decorative_icon_geometry_is_not_evidence_but_its_presence_is(self):
        html = page("vuepress1")
        self.assertEqual(compare_documents(extract("vuepress1"), extract("vuepress1", html.replace(
            "M18.8,85.1h56l0,0", "M18.9,85.1h56l0,0")))["status"], "unchanged")
        removed = html.replace('<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true"', '<svg-removed aria-hidden="true"', 1)
        with self.assertRaises(KaynakBolumHatasi):
            extract("vuepress1", removed)
        no_icon = html[:html.index("<span><svg")] + html[html.index("(opens new window)</span></span>") + len("(opens new window)</span></span>"):]
        self.assertEqual(compare_documents(extract("vuepress1"), extract("vuepress1", no_icon))["status"], "changed")

    def test_undeclared_active_content_inside_root_is_still_rejected(self):
        injections = {
            "docbook-section": ('<p>Data types limit', '<script>track()</script><p>Data types limit'),
            "sphinx": ('<p>Each table configures', '<button type="button">Run</button><p>Each table configures'),
            "mkdocs-material": ('<p>A virtual environment', '<iframe src="https://example.org/x"></iframe><p>A virtual environment'),
            "docusaurus": ('<p>The account must be active.</p>', '<p>The account must be active.</p><form><input name="q"></form>'),
            "hugo-docsy": ('<p>A project holds', '<p onclick="go()">A project holds'),
            "starlight": ('<script type="module" src="/_astro/ec.8zarh.js"></script>', '<script type="module" src="/_astro/ec.8zarh.js">alert(1)</script>'),
            "vuepress1": ('<p>Items represent', '<p><a href="javascript:alert(1)">x</a>Items represent'),
            "nodered": ('<p>The debug sidebar', '<object data="/x.svg"></object><p>The debug sidebar'),
        }
        for name, (old, new) in injections.items():
            with self.subTest(fixture=name), self.assertRaisesRegex(KaynakBolumHatasi, "unsupported_active_content"):
                html = page(name)
                self.assertIn(old, html)
                extract(name, html.replace(old, new, 1))

    def test_undeclared_media_or_diagram_svg_is_rejected(self):
        for name, old, new in (
                ("docbook-section", "<p>Data types limit", '<canvas>chart</canvas><p>Data types limit'),
                ("sphinx", "<p>Each table configures", '<svg viewBox="0 0 10 10"><text>diagram</text></svg><p>Each table configures'),
                ("starlight", "<p>All sensors share", '<svg viewBox="0 0 10 10"><path d="M0 0"/></svg><p>All sensors share'),
                ("starlight", "<p>All sensors share", '<img src="data:image/png;base64,AAAA" alt="x"><p>All sensors share'),
                ("vuepress1", "<p>Items represent", '<video src="/x.mp4"></video><p>Items represent')):
            with self.subTest(fixture=name, injected=new[:20]), self.assertRaises(KaynakBolumHatasi):
                extract(name, page(name).replace(old, new, 1))

    def test_generator_without_its_root_is_a_contract_mismatch_not_generic_fallback(self):
        for name, old, new in (("sphinx", 'role="main"', 'role="region"'), ("mkdocs-material", "md-content__inner", "md-inner"),
                               ("docusaurus", "theme-doc-markdown", "doc-markdown"), ("hugo-docsy", 'class="td-content"', 'class="content"'),
                               ("starlight", 'class="sl-markdown-content"', 'class="markdown"'),
                               ("vuepress1", "theme-default-content content__default", "content"),
                               ("starlight", '<div class="sl-markdown-content">', '<div class="sl-markdown-content"></div><div class="sl-markdown-content">')):
            with self.subTest(fixture=name, change=new), self.assertRaisesRegex(KaynakBolumHatasi, "family_contract_mismatch"):
                extract(name, page(name).replace(old, new, 1))

    def test_two_generator_signatures_are_ambiguous(self):
        html = page("sphinx").replace('<meta name="viewport"', '<meta name="generator" content="Docusaurus v3.5.2"><meta name="viewport"')
        with self.assertRaisesRegex(KaynakBolumHatasi, "ambiguous_document_family"):
            extract("sphinx", html)
        html = page("hugo-docsy").replace('<div class="td-content">', '<div class="td-content"><div class="sect1" id="x"><div class="titlepage"><h2 class="title">X</h2></div><p>y</p></div>')
        with self.assertRaisesRegex(KaynakBolumHatasi, "ambiguous_document_family"):
            extract("hugo-docsy", html)

    def test_site_profile_applies_only_to_its_registered_site(self):
        self.assertEqual(extract("nodered")["extractor_version"], "dom-sections-v2.nodered-docs-1")
        for url in ("https://docs.example.org/docs/user-guide/messages", "https://nodered.org/blog/messages"):
            with self.subTest(url=url), self.assertRaisesRegex(KaynakBolumHatasi, "ambiguous_document_root"):
                extract("nodered", url=url)

    def test_volatile_build_classes_do_not_hide_semantic_class_changes(self):
        html = page("starlight")
        self.assertEqual(compare_documents(extract("starlight"), extract("starlight", html.replace(
            "filter-section astro-b5vgtrtx", "filter-block astro-b5vgtrtx", 1)))["status"], "changed")

    def test_generic_extractor_is_unchanged_for_pages_without_a_generator(self):
        body = '<main><h1>A</h1><p>x</p><a href="http://docs.example.org/x">y</a></main>'
        with self.assertRaisesRegex(KaynakBolumHatasi, "unsupported_link_target"):
            extract_document(body, "https://docs.example.org/a")
        document = extract_document('<main><h1>A</h1><p>x</p></main>', "https://docs.example.org/a")
        self.assertEqual(document["extractor_version"], "dom-sections-v2.generic")
        self.assertEqual(document["sections"][0]["section_id"], canonical_hash({"extractor_version": "dom-sections-v2.generic",
                                                                                 "path": [[document["sections"][0]["heading_hash"], 1]]}))

    def test_family_switch_is_ambiguous_not_unchanged(self):
        generic = extract_document('<main><h1>Install</h1><p>x</p></main>', CASES["sphinx"]["url"])
        self.assertEqual(compare_documents(generic, extract("sphinx"))["status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()

"""Kamu raporunun güvenli işaretlemeyle ve tam sürüm adresiyle okunduğunu sınar."""

import unittest
import xml.etree.ElementTree as ET

from radar.topluluk_sayfalari import kisa_ad, atom, guvenli_markdown, markdown_rapor, sayfa, sitemap, yayindaki_arastirmalar


def card(identifier="a" * 32):
    return {"id": identifier, "version": 3, "published_version": 3,
            "updated_at": "2026-11-06T09:00:00+00:00",
            "packet": {"kind": "research", "title": "A test report", "summary": "Source-backed summary",
                       "body": "## Results\n- [source](https://example.org/page)\n- <script>alert(1)</script>\n```html\n<img src=x onerror=alert(1)>\n```",
                       "author": "A contributor", "license": "CC-BY-4.0",
                       "sources": [{"title": "Primary", "url": "https://example.org/page"}],
                       "provenance": {"language": "en", "evidence_status": "unverified", "claim_text": "A limited claim"}}}


class PublicPageTest(unittest.TestCase):
    def test_markdown_escapes_html_and_keeps_safe_links(self):
        rendered = guvenli_markdown(card()["packet"]["body"] + "\n[bad](javascript:alert)")
        self.assertIn('<a href="https://example.org/page"', rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn('href="javascript:', rendered)

    def test_report_has_exact_canonical_metadata_and_no_view_write(self):
        rendered = sayfa(card(), "https://community.example.org", "tr")
        self.assertIn('<html lang="tr">', rendered)
        self.assertIn('/r/' + "a" * 32 + '/v/3', rendered)
        self.assertIn('application/ld+json', rendered)
        self.assertIn('"@type": "TechArticle"', rendered)
        self.assertIn('"citation":', rendered)
        self.assertNotIn('ClaimReview', rendered)
        self.assertIn('<dt>Kanıt durumu</dt><dd>Denetlenmemiş</dd>', rendered)
        self.assertIn('Denetlenmemiş', rendered)
        self.assertNotIn('Kanıt durumu: unverified', rendered)
        self.assertIn('A limited claim', rendered)
        self.assertNotIn('web_view', rendered)
        self.assertIn('<dt>Evidence status</dt><dd>Unverified</dd>', sayfa(card(), "https://community.example.org", "en"))
        plain = markdown_rapor(card(), "https://community.example.org")
        self.assertIn('Version: 3', plain)
        self.assertIn('License: CC-BY-4.0', plain)
        self.assertIn('https://example.org/page', plain)

    def test_feeds_only_use_public_listing(self):
        class Store:
            def listele(self, *, kind, offset, limit):
                self.assert_kind = kind
                return {"items": [card()] if offset == 0 else [], "total": 1}
        cards = list(yayindaki_arastirmalar(Store()))
        self.assertEqual(len(cards), 1)
        self.assertIn('/r/' + "a" * 32 + '/a-test-report', sitemap(cards, "https://community.example.org"))
        self.assertIn('/r/' + "a" * 32 + '/v/3', atom(cards, "https://community.example.org"))


    def test_exact_version_download_and_shared_provenance_limits(self):
        saved = card()
        saved["published_version"] = 4
        saved["packet"]["provenance"].update(model="example/model", provider="test/provider",
            generated_at="2020-01-02T03:04:05Z", modified=True, evidence_status="verified")
        page = sayfa(saved, "https://community.example.org", "en")
        plain = markdown_rapor(saved, "https://community.example.org")
        self.assertIn('/r/' + "a" * 32 + '.md?version=3', page)
        for output in (page, plain):
            self.assertIn('example/model', output)
            self.assertIn('test/provider', output)
            self.assertIn('2020-01-02T03:04:05Z', output)
            self.assertIn('Unverified', output)
            self.assertIn('modified after the original report', output)
            self.assertIn('earlier version', output)
            self.assertNotIn('Source match recorded', output)

    def test_corrections_are_visible_in_html_and_markdown_with_withdrawal_and_more(self):
        saved = card()
        saved['public_corrections'] = {'total': 4, 'items': [
            {'summary': '<script>Correction</script>', 'withdrawn': True,
             'correction': {'research_id': 'b' * 32, 'version': 9}}]}
        page = sayfa(saved, 'https://community.example.org', 'en')
        plain = markdown_rapor(saved, 'https://community.example.org')
        for output in (page, plain):
            self.assertIn('/r/' + 'b' * 32 + '/v/9', output)
            self.assertIn('Private objection withdrawn', output)
            self.assertIn('/api/community/objections/corrections/' + 'a' * 32 + '/3', output)
        self.assertNotIn('<script>Correction</script>', page)
        self.assertIn('&lt;script&gt;Correction&lt;/script&gt;', page)

    def test_missing_provenance_is_explicit_and_untrusted_details_are_escaped(self):
        saved = card()
        saved['packet'].pop('provenance')
        page = sayfa(saved, 'https://community.example.org', 'en')
        self.assertIn('Original research model</dt><dd>Not recorded', page)
        self.assertIn('Evidence status</dt><dd>Unknown', page)
        saved['packet']['provenance'] = {'model': '<img src=x onerror=alert(1)>',
                                        'provider': '<script>bad</script>', 'modified': True}
        page = sayfa(saved, 'https://community.example.org', 'tr')
        self.assertNotIn('<img src=x', page)
        self.assertNotIn('<script>bad', page)
        self.assertIn('yeniden kanıt denetimi yapılmadı', page)



    def test_language_links_work_without_scripts_and_report_body_keeps_its_language(self):
        saved = card()
        page = sayfa(saved, 'https://community.example.org', 'tr')
        self.assertIn('<html lang="tr">', page)
        self.assertIn('<section class="report-body" lang="en">', page)
        self.assertIn('/r/' + 'a' * 32 + '/v/3?lang=en', page)
        self.assertIn('/r/' + 'a' * 32 + '/v/3?lang=tr', page)
        self.assertIn('data-report-language="en"', page)
        self.assertIn('src="/static/report-language.js"', page)
        saved['packet']['provenance']['language'] = '<script>'
        self.assertIn('<section class="report-body" lang="tr">', sayfa(saved, 'https://community.example.org', 'tr'))

    def test_atom_has_author_and_self_link_and_turkish_slug_keeps_dotless_i(self):
        self.assertEqual(kisa_ad('Kabul sınaması IŞIK'), 'kabul-sinamasi-isik')
        root = ET.fromstring(atom([card()], 'https://community.example.org'))
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        self.assertEqual(root.find('a:author/a:name', ns).text, 'DevTrellis')
        self.assertEqual(root.find("a:link[@rel='self']", ns).get('href'), 'https://community.example.org/feed.atom')
        self.assertEqual(root.find('a:entry/a:author/a:name', ns).text, 'A contributor')


if __name__ == "__main__":
    unittest.main()

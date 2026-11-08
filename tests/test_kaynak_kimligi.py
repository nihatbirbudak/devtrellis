"""Kamu kaynak kimliğinin anlamı koruyan ayrımlarını ve özel adresleri kapalı tutmasını sınar."""

import hashlib
import json
import unittest

from radar.kaynak_kimligi import (EXTRACTOR_VERSION, REPRESENTATION, KaynakKimligiHatasi,
                                  address_key, canonical_source_url, source_key)


class SourceIdentityTests(unittest.TestCase):
    URL = "https://docs.example.org/en/v1/Guide/?version=1&lang=en"

    def variant(self, language="en"):
        return {"accept": "text/html, application/xhtml+xml;q=0.9", "accept-language": language,
                "accept-encoding": "identity", "user-agent": "UfukRadar/0.1"}

    def source(self, url=None, language="en", **options):
        values = {"representation": REPRESENTATION, "request_variant": self.variant(language),
                  "extractor_version": EXTRACTOR_VERSION}
        values.update(options)
        return source_key(url or self.URL, language, **values)

    def test_only_safe_host_and_default_port_normalization(self):
        value = "HTTPS://DOCS.EXAMPLE.ORG:443/en/v1/Guide/?version=1&lang=en#section"
        self.assertEqual(canonical_source_url(value), self.URL)
        self.assertEqual(address_key(value, "en"), address_key(self.URL, "en"))
        self.assertEqual(self.source(value), self.source())

    def test_path_case_slash_version_encoding_and_query_order_remain_distinct(self):
        urls = ["https://docs.example.org/en/v1/Guide", "https://docs.example.org/en/v1/Guide/",
                "https://docs.example.org/en/v1/guide", "https://docs.example.org/en/v2/Guide",
                "https://docs.example.org/en/v1/%47uide", "https://docs.example.org/en/v1/Guide?",
                "https://docs.example.org/en/v1/Guide?v=1&lang=en", "https://docs.example.org/en/v1/Guide?lang=en&v=1",
                "https://docs.example.org/en/v1/Guide?v=%31&lang=en"]
        self.assertEqual(len(urls), len({canonical_source_url(url) for url in urls}))
        self.assertEqual(len(urls), len({address_key(url, "en") for url in urls}))

    def test_json_contract_is_full_sha256(self):
        payload = {"canonical_url": self.URL, "language": "en", "representation": REPRESENTATION,
                   "request_variant": self.variant(), "extractor_version": EXTRACTOR_VERSION}
        expected = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(self.source(), expected)
        self.assertEqual(len(expected), 64)

    def test_variant_order_does_not_change_source_key(self):
        self.assertEqual(self.source(), self.source(request_variant=dict(reversed(list(self.variant().items())))))

    def test_language_request_variant_and_extractor_are_separate_identity(self):
        self.assertNotEqual(address_key(self.URL, "en"), address_key(self.URL, "tr"))
        self.assertNotEqual(self.source(), self.source(language="tr"))
        self.assertNotEqual(self.source(), self.source(extractor_version="dom-sections-v3"))
        changed = {**self.variant(), "user-agent": "UfukRadar/0.2"}
        self.assertNotEqual(self.source(), self.source(request_variant=changed))
        # address_key'nin çıkarıcı/başlık argümanı bulunmaz; URL+dil sabit kalır.
        self.assertEqual(address_key(self.URL, "en"), address_key(self.URL + "#different", "en"))

    def test_private_unsafe_or_unbounded_urls_are_rejected_without_echo(self):
        forbidden = ["http://docs.example.org/path", "https://owner:sentinel-secret@docs.example.org/x",
                     "https://127.0.0.1/x", "https://[::1]/x", "https://example.local/x",
                     "https://docs.example.org:8443/x", "https://docs.example.org/x?token=sentinel-secret",
                     "https://docs.example.org/x?version=1&version=2", "https://docs.example.org/x?lang=",
                     "https://docs.example.org/x?unknown=1", "https://docs.example.org/x?lang=en%0A",
                     "https://docs.example.org/x?v=1&version=2&tag=3&lang=en&language=en",
                     "https://docs.example.org/x?v=1&", "https://docs.example.org/x%0A",
                     "https://docs.example.org/x%250A", "https://docs.example.org/x%5Cadmin",
                     "https://docs.example.org/person%40private.example.org/report",
                     "https://docs.example.org/person%2540private.example.org/report",
                     "https://docs.example.org/%GG", "https://docs.example.org/" + "x" * 2048,
                     None, 3]
        for url in forbidden:
            with self.subTest(url=url):
                with self.assertRaises(KaynakKimligiHatasi) as caught:
                    canonical_source_url(url)
                self.assertEqual(str(caught.exception), "invalid_public_source_url")

    def test_variant_exact_fields_bounded_strings_and_matching_language(self):
        variants = [{}, {**self.variant(), "authorization": "sentinel-secret"},
                    {**self.variant(), "accept": "x\r\ny"}, {**self.variant(), "accept": "x" * 257},
                    {**self.variant(), "accept": 3}, {**self.variant(), "accept-language": "tr"},
                    {**self.variant(), "User-Agent": "a"}]
        for value in variants:
            with self.subTest(value=value):
                with self.assertRaises(KaynakKimligiHatasi):
                    self.source(request_variant=value)
        for language in ("", "EN", "en-us", "en\n", "English", None):
            with self.subTest(language=language), self.assertRaises(KaynakKimligiHatasi):
                address_key(self.URL, language)
        for representation in ("public-doc-anonymous-v1", "future", None):
            with self.subTest(representation=representation), self.assertRaises(KaynakKimligiHatasi):
                self.source(representation=representation)


if __name__ == "__main__":
    unittest.main()

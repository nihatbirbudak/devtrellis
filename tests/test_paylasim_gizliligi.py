"""Anonim kamu kopyasının hassas kalıpları temizlediğini ve URL sınırlarını ağ olmadan koruduğunu sınar."""

import copy
import json
import unittest
from unittest.mock import patch

from radar.paylasim_gizliligi import kamu_kopyasi, kamusal_url, sorguyu_dogrula
from tests.test_platform_api import paket


class PaylasimGizliligiTestleri(unittest.TestCase):
    def test_public_copy_is_anonymous_idempotent_and_preserves_model_date_license(self):
        original = paket(schema_version=2, license="CC0-1.0", author="PRIVATE_AUTHOR_734", provenance={
            "question": "PRIVATE_QUESTION_562", "claim_text": "PRIVATE_CLAIM_245", "model": "published-model-v2",
            "provider": "nvidia", "generated_at": "2026-11-04T10:00:00+00:00", "topic": "ai", "language": "en",
            "source_url": "https://example.org/public", "evidence_status": "verified", "modified": False})
        before = copy.deepcopy(original)
        first, count = kamu_kopyasi(original)
        self.assertEqual(first["author"], "Anonymous contributor")
        self.assertEqual(first["provenance"]["question"], first["title"])
        self.assertNotIn("claim_text", first["provenance"])
        self.assertEqual(first["provenance"]["model"], "published-model-v2")
        self.assertEqual(first["provenance"]["generated_at"], original["provenance"]["generated_at"])
        self.assertEqual(first["license"], "CC0-1.0")
        self.assertEqual(first["provenance"]["evidence_status"], "unverified")
        self.assertTrue(first["provenance"]["modified"])
        self.assertGreater(count, 0)
        self.assertEqual(kamu_kopyasi(first), (first, 0))
        self.assertEqual(original, before)
        for value in ("PRIVATE_AUTHOR_734", "PRIVATE_QUESTION_562", "PRIVATE_CLAIM_245"):
            self.assertNotIn(value, json.dumps(first))

    def test_common_private_patterns_are_removed_from_text_without_changing_structured_dates(self):
        values = ["user@example.org", "sk-" + "TESTKEY" * 5, "Bearer TEST_TOKEN_12345", "password=private-value",
                  "+90 (555) 123 45 67", "905551234567", "192.168.2.35", "8.8.8.8", "2001:db8::1",
                  "C:\\Users\\Person\\private.txt", "/home/person/private", "machine.internal",
                  "https://name:password@example.org/", "https://example.org/?access_token=private-value",
                  "user%2540example.org", "user\u200b@example.org", '{"password": "private multi word secret"}',
                  "--api-key command-private-key"]
        source = paket(body="\n".join(values) + "\nPublished 2026-11-04; version 3.12.16.")
        result, count = kamu_kopyasi(source)
        for value in values:
            self.assertNotIn(value, result["body"])
        for value in ("private multi word secret", "multi word secret", "command-private-key"):
            self.assertNotIn(value, result["body"])
        self.assertIn("Published 2026-11-04; version 3.12.16.", result["body"])
        self.assertEqual(result["created_at"], source["created_at"])
        self.assertGreaterEqual(count, len(values))

    def test_unsafe_sources_rejected_with_generic_errors_and_no_dns(self):
        addresses = ["http://example.org/public", "https://user:password@example.org/", "https://127.0.0.1/",
                     "https://8.8.8.8/", "https://[2001:4860:4860::8888]/", "https://project.internal/",
                     "https://project.corp/", "https://example.org/?unknown=private", "https://example.org/?token=secret",
                     "https://example.org/#auth=secret", "https://example.org/path/private%40example.org",
                     "https://example.org/home/person/private", "https://example.org/?%2574oken=secret"]
        with patch("socket.getaddrinfo", side_effect=AssertionError("No DNS is permitted")):
            for address in addresses:
                with self.subTest(address=address), self.assertRaises(ValueError) as caught:
                    kamu_kopyasi(paket(sources=[{"title": "Public source", "url": address}]))
                self.assertNotIn(address, str(caught.exception))

    def test_sensitive_patterns_are_cleaned_in_every_free_text_metadata_field(self):
        secret = "nvapi-" + "SYNTHETICEXAMPLE" * 2
        source = paket(schema_version=2, title="Research user@example.org", summary="Contact user@example.org",
                       body="Research findings only.", author="Unshared account name", tags=["user@example.org"],
                       sources=[{"title": "Contact user@example.org", "url": "https://example.org/public"}],
                       provenance={"question": "Private request context", "claim_text": "Private claim context",
                                   "model": secret, "provider": "unknown", "generated_at": "2026-11-05T10:00:00+00:00",
                                   "topic": "ai", "language": "en", "source_url": "https://example.org/public",
                                   "evidence_status": "verified", "modified": False})
        before = copy.deepcopy(source)
        cleaned, count = kamu_kopyasi(source)
        serialized = json.dumps(cleaned)
        for value in (secret, "user@example.org", "Unshared account name", "Private request context", "Private claim context"):
            self.assertNotIn(value, serialized)
        self.assertEqual(cleaned["provenance"]["question"], cleaned["title"])
        self.assertEqual(cleaned["provenance"]["model"], "[redacted]")
        self.assertEqual(cleaned["provenance"]["evidence_status"], "unverified")
        self.assertEqual(source, before)
        self.assertGreaterEqual(count, 8)

    def test_tracking_is_removed_and_youtube_exception_is_narrow(self):
        self.assertEqual(kamusal_url("https://example.org/article?utm_source=feed&gclid=tracking#section"),
                         "https://example.org/article")
        self.assertEqual(kamusal_url("https://www.youtube.com/watch?v=abcdefghijk&si=tracking"),
                         "https://www.youtube.com/watch?v=abcdefghijk")
        for address in ("https://example.org/watch?v=abcdefghijk", "https://www.youtube.com/watch?v=too-short",
                        "https://www.youtube.com/watch?v=abcdefghijk&v=lmnopqrstuv"):
            with self.assertRaises(ValueError):
                kamusal_url(address)

    def test_sensitive_query_rejected_and_public_topic_query_retained(self):
        for query in ("email user@example.org", "private.local project", "phone +90 555 123 45 67",
                      "key=secretvalue", '{"token": "private multi word secret"}', "--password secretvalue",
                      "network 10.0.0.1", "open /home/user/project", "user%40example.org"):
            with self.assertRaises(ValueError) as caught:
                sorguyu_dogrula(query)
            self.assertNotIn(query, str(caught.exception))
        self.assertEqual(sorguyu_dogrula("Python 3.12 asyncio research"), "Python 3.12 asyncio research")


if __name__ == "__main__":
    unittest.main()

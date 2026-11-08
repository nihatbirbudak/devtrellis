"""Anonim belge önbelleği, doğrulayıcı bağı ve ağ sınırlarını gerçek istek yapmadan sınar."""

import email.utils
import http.client
import io
import json
import unittest
from unittest.mock import MagicMock, patch

from radar import kaynak_http, toplayici
from radar.toplayici import FetchResult


URL = "https://docs.python.org/3/library/json.html"
NOW = 1793966400.0
BODY = b"<html><article><h1>Public documentation</h1><p>Stable public body.</p></article></html>"


def date(stamp):
    return email.utils.formatdate(stamp, usegmt=True)


def source(**changes):
    return {"url": URL, "language": "en", "allow_public_cookie": False,
            "representation": kaynak_http.REPRESENTATION, "extractor_version": "dom-sections-v1",
            "source_key": "a" * 64, "request_variant": kaynak_http.request_variant("en"), **changes}


def response(status=200, *, headers=None, body=None, url=URL):
    default = {"content-type": "text/html; charset=utf-8", "cache-control": "public, max-age=600",
               "date": date(NOW), "etag": '"one"', "last-modified": date(NOW - 1000),
               "vary": "Accept-Language, Accept-Encoding, User-Agent"}
    return FetchResult(status, (BODY if status == 200 else b"") if body is None else body,
                       default if headers is None else headers, url)


class HTTPPolicyTests(unittest.TestCase):
    def prepare(self, result=None, previous=None, definition=None, **options):
        arguments = {"now": NOW, "request_started_at": NOW - 2, **options}
        return kaynak_http.prepare_response(definition or source(), previous, result or response(), **arguments)

    def test_body_and_safe_json_metadata_are_separate(self):
        meta, body = self.prepare()
        self.assertEqual(body, BODY)
        self.assertEqual(meta["fresh_until"], NOW + 598)
        self.assertFalse(meta["revalidate"])
        self.assertTrue(meta["byte_verified"])
        self.assertNotIn("quote_verified_at", meta)
        self.assertNotIn("Stable public body", json.dumps(meta))
        self.assertEqual(meta["downloaded_bytes"], len(BODY))

    def test_v1_and_changed_request_variant_cannot_opt_in(self):
        for definition in (source(representation="html-anonymous-v1"), source(request_variant={}),
                           source(language="en\r\nCookie: secret"), source(allow_public_cookie="yes")):
            with self.subTest(definition=definition), self.assertRaises(kaynak_http.HTTPPolicyError):
                self.prepare(definition=definition)

    def test_private_no_store_and_unsupported_vary_are_always_rejected(self):
        for extra in ({"cache-control": "private, public, max-age=600"}, {"cache-control": 'private="Set-Cookie"'},
                      {"cache-control": "public, no-store"}, {"vary": "Cookie"}, {"vary": "*"},
                      {"vary": "Accept-Language, Authorization"}, {"vary": "X-Custom"}):
            for status in (200, 304):
                before, _ = self.prepare()
                raw = response(status, headers={**response().headers, **extra})
                with self.subTest(extra=extra, status=status), self.assertRaises(kaynak_http.HTTPPolicyError):
                    self.prepare(raw, before, sent_etag='"one"')

    def test_cookie_requires_both_operator_flag_and_explicit_public_directive(self):
        raw = response(headers={**response().headers, "Set-Cookie": "SYNTHETIC_COOKIE_VALUE"})
        with self.assertRaisesRegex(kaynak_http.HTTPPolicyError, "public_cookie_not_authorized"):
            self.prepare(raw)
        meta, _ = self.prepare(raw, definition=source(allow_public_cookie=True))
        self.assertTrue(meta["set_cookie_seen"])
        self.assertNotIn("SYNTHETIC_COOKIE_VALUE", json.dumps(meta))
        self.assertNotIn("set-cookie", meta["headers"])
        for cache in ("max-age=600", "private, public", "no-store, public"):
            with self.subTest(cache=cache), self.assertRaises(kaynak_http.HTTPPolicyError):
                self.prepare(response(headers={**raw.headers, "cache-control": cache}), definition=source(allow_public_cookie=True))

    def test_304_merges_missing_headers_and_preserves_private_constraints(self):
        definition = source(allow_public_cookie=True)
        first = response(headers={**response().headers, "cache-control": "public, no-cache", "set-cookie": "SECRET"})
        before, _ = self.prepare(first, definition=definition)
        after, body = self.prepare(response(304, headers={}), before, definition, sent_etag='"one"', now=NOW + 20)
        self.assertEqual(after["headers"]["vary"], before["headers"]["vary"])
        self.assertEqual(after["headers"]["cache-control"], before["headers"]["cache-control"])
        self.assertTrue(after["set_cookie_seen"])
        self.assertTrue(after["revalidate"])
        self.assertIsNone(body)
        self.assertEqual(after["content_fetched_at"], NOW)
        self.assertEqual(after["representation_checked_at"], NOW + 20)
        self.assertFalse(after["byte_verified"])
        with self.assertRaises(kaynak_http.HTTPPolicyError):
            self.prepare(response(304, headers={"cache-control": "max-age=600"}), before,
                         definition, sent_etag='"one"')

    def test_304_requires_actual_sent_validator_matching_previous(self):
        before, _ = self.prepare()
        for previous, arguments in ((None, {"sent_etag": '"one"'}), (before, {}),
                                    (before, {"sent_etag": '"other"'}),
                                    (before, {"sent_modified": date(NOW - 500)})):
            with self.subTest(arguments=arguments), self.assertRaises(kaynak_http.HTTPPolicyError):
                self.prepare(response(304, headers={}), previous, **arguments)

    def test_304_rejects_different_source_variant_or_extractor(self):
        before, _ = self.prepare()
        for change in ({"url": URL + "?version=2"}, {"representation": "html-anonymous-v1"},
                       {"request_variant": {}}, {"source_key": "b" * 64}, {"extractor_version": "other"}):
            with self.subTest(change=change), self.assertRaises(kaynak_http.HTTPPolicyError):
                self.prepare(response(304, headers={}), {**before, **change}, sent_etag='"one"')

    def test_304_response_validator_drift_or_body_is_rejected(self):
        before, _ = self.prepare()
        for raw in (response(304, headers={"etag": '"other"'}),
                    response(304, headers={"last-modified": date(NOW - 500)}),
                    response(304, headers={}, body=b"unexpected")):
            with self.subTest(raw=raw), self.assertRaises(kaynak_http.HTTPPolicyError):
                self.prepare(raw, before, sent_etag='"one"')

    def test_supported_vary_change_rechecks_whole_fixed_variant(self):
        before, _ = self.prepare()
        after, _ = self.prepare(response(304, headers={"vary": "User-Agent"}), before, sent_etag='"one"')
        self.assertEqual(after["headers"]["vary"], "user-agent")
        self.assertEqual(after["request_variant"], before["request_variant"])

    def test_content_language_matches_source_and_survives_304_merge(self):
        before, _ = self.prepare(response(headers={**response().headers, "content-language": "EN"}))
        self.assertEqual(before["headers"]["content-language"], "en")
        after, _ = self.prepare(response(304, headers={}), before, sent_etag='"one"')
        self.assertEqual(after["headers"]["content-language"], "en")
        for language in ("tr", "en, tr", "en-US", "*"):
            for status in (200, 304):
                headers = {**response().headers, "content-language": language}
                with self.subTest(language=language, status=status), self.assertRaises(kaynak_http.HTTPPolicyError):
                    self.prepare(response(status, headers=headers), before, sent_etag='"one"')

    def test_weak_etag_and_last_modified_do_not_become_byte_or_quote_verification(self):
        weak, _ = self.prepare(response(headers={**response().headers, "etag": 'W/"one"'}))
        after, _ = self.prepare(response(304, headers={}), weak, sent_etag='W/"one"')
        self.assertEqual(after["validation_kind"], "weak_etag")
        self.assertFalse(after["byte_verified"])
        headers = {key: value for key, value in response().headers.items() if key != "etag"}
        prior, _ = self.prepare(response(headers=headers))
        after, _ = self.prepare(response(304, headers={}), prior, sent_modified=headers["last-modified"])
        self.assertEqual(after["validation_kind"], "last_modified")
        self.assertFalse(after["byte_verified"])
        self.assertNotIn("quote_verified_at", after)

    def test_age_and_request_delay_reduce_freshness(self):
        for additions, expected in (({"age": "120"}, NOW + 478), ({"date": date(NOW - 300)}, NOW + 300),
                                    ({"age": "900"}, NOW)):
            meta, _ = self.prepare(response(headers={**response().headers, **additions}))
            self.assertEqual(meta["fresh_until"], expected)

    def test_missing_invalid_future_clocks_and_invalid_age_never_grant_freshness(self):
        for additions in ({"date": "invalid private text"}, {"date": date(NOW + 1)}, {"age": "-1"}, {"age": "SECRET"}):
            meta, _ = self.prepare(response(headers={**response().headers, **additions}))
            self.assertEqual(meta["fresh_until"], NOW)
            self.assertTrue(meta["revalidate"])
            self.assertNotIn("SECRET", json.dumps(meta))
        for started in (None, NOW + 1, float("nan")):
            meta, _ = self.prepare(request_started_at=started)
            self.assertEqual(meta["fresh_until"], NOW)
        meta, _ = self.prepare(response(headers={key: value for key, value in response().headers.items() if key != "date"}))
        self.assertEqual(meta["fresh_until"], NOW)

    def test_304_missing_date_cannot_reset_accumulated_age(self):
        before, _ = self.prepare(response(headers={**response().headers, "age": "300"}))
        after, _ = self.prepare(response(304, headers={"age": "0"}), before, sent_etag='"one"',
                                now=NOW + 100, request_started_at=NOW + 98)
        self.assertEqual(after["initial_age"], 402)
        self.assertEqual(after["fresh_until"], NOW + 298)

    def test_no_cache_zero_age_duplicate_lifetimes_and_expires_are_conservative(self):
        for cache in ("no-cache, max-age=600", 'no-cache="ETag", max-age=600', "max-age=0",
                      "max-age=0, s-maxage=600", "max-age=600, max-age=800", "max-age=bad"):
            meta, _ = self.prepare(response(headers={**response().headers, "cache-control": cache}))
            self.assertEqual(meta["fresh_until"], NOW)
        headers = {**response().headers, "cache-control": "public", "expires": date(NOW + 100)}
        meta, _ = self.prepare(response(headers=headers))
        self.assertEqual(meta["fresh_until"], NOW + 98)

    def test_redirect_partial_oversized_and_unsafe_content_are_rejected(self):
        for raw in (response(301), response(206), response(url="https://example.org/doc"),
                    response(headers={**response().headers, "content-range": "bytes 0-2/100"}),
                    response(headers={**response().headers, "content-type": "application/pdf"}),
                    response(headers={**response().headers, "content-encoding": "gzip"}),
                    response(body=b"x" * (toplayici.MAX_BODY_BYTES + 1)), response(body=b"")):
            with self.subTest(status=raw.status), self.assertRaises(kaynak_http.HTTPPolicyError):
                self.prepare(raw)

    def test_fetch_metadata_contains_only_actual_sent_values_and_cookie_boolean(self):
        raw = response(headers={**response().headers, "set-cookie": "NEVER_RETURN_COOKIE", "x-private": "NEVER_RETURN_HEADER"})
        with patch.object(kaynak_http, "anonim_belge_oku", return_value=raw) as network:
            fetched, request = kaynak_http.fetch_document(source(), etag='"one"')
        self.assertEqual(network.call_args.args[1], {**kaynak_http.request_variant("en"), "if-none-match": '"one"'})
        self.assertEqual(request["sent_etag"], '"one"')
        self.assertIsNone(request["sent_modified"])
        self.assertNotIn("NEVER_RETURN", repr(fetched))
        self.assertTrue(fetched.set_cookie_seen)
        with self.assertRaises(kaynak_http.HTTPPolicyError):
            self.prepare(fetched)


class NetworkBoundaryTests(unittest.TestCase):
    def test_fixed_headers_and_strict_existing_network_layer_are_used(self):
        with patch.object(toplayici, "_robots_allowed", return_value=True) as robots, \
                patch.object(toplayici, "_request_once", return_value=response()) as request:
            result = toplayici.anonim_belge_oku(URL, kaynak_http.request_variant("en"))
        self.assertEqual(result.status, 200)
        robots.assert_called_once()
        self.assertEqual(request.call_args.args[1], {"docs.python.org"})
        self.assertEqual(request.call_args.args[2], {name.title(): value for name, value in kaynak_http.request_variant("en").items()})
        self.assertTrue(request.call_args.kwargs["strict_validators"])
        self.assertTrue(request.call_args.kwargs["strict_representation"])

    def test_repeated_age_or_representation_header_cannot_override_first_value(self):
        for name, first, second in (("Age", "900", "0"), ("Date", date(NOW - 900), date(NOW)),
                                    ("Content-Type", "application/pdf", "text/html"),
                                    ("Content-Encoding", "gzip", "identity")):
            raw = ("HTTP/1.1 200 OK\r\n" + name + ": " + first + "\r\n" + name + ": " + second
                   + "\r\nContent-Length: " + str(len(BODY)) + "\r\n\r\n").encode("ascii") + BODY
            response_socket = MagicMock()
            response_socket.makefile.return_value = io.BytesIO(raw)
            parsed = http.client.HTTPResponse(response_socket)
            parsed.begin()
            connection = MagicMock()
            connection.getresponse.return_value = parsed
            with self.subTest(name=name), patch.object(toplayici, "_robots_allowed", return_value=True), \
                    patch.object(toplayici, "_resolve_public_host", return_value=(2, ("8.8.8.8", 443))), \
                    patch.object(toplayici, "_PinnedHTTPSConnection", return_value=connection), \
                    self.assertRaises(toplayici.CollectionError):
                toplayici.anonim_belge_oku(URL, kaynak_http.request_variant("en"))
            connection.close.assert_called_once()

    def test_cookie_authorization_private_query_and_robots_denial_never_reach_connection(self):
        with patch.object(toplayici, "_robots_allowed", return_value=False), patch.object(toplayici, "_request_once") as request:
            for extra in ({"cookie": "SECRET"}, {"authorization": "SECRET"}, {}):
                with self.assertRaises(toplayici.CollectionError):
                    toplayici.anonim_belge_oku(URL, {**kaynak_http.request_variant("en"), **extra})
            with self.assertRaises(toplayici.CollectionError):
                toplayici.anonim_belge_oku(URL + "?token=SECRET", kaynak_http.request_variant("en"))
            request.assert_not_called()

    def test_private_dns_is_rejected_before_tls_connection(self):
        with patch.object(toplayici, "_robots_allowed", return_value=True), \
                patch.object(toplayici.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]), \
                patch.object(toplayici, "_PinnedHTTPSConnection") as connection, self.assertRaises(toplayici.CollectionError):
            toplayici.anonim_belge_oku(URL, kaynak_http.request_variant("en"))
        connection.assert_not_called()

    def test_redirect_and_unconditional_304_are_not_followed(self):
        for result in (response(302), response(304)):
            with patch.object(toplayici, "_robots_allowed", return_value=True), \
                    patch.object(toplayici, "_request_once", return_value=result) as request, \
                    self.assertRaises(toplayici.CollectionError):
                toplayici.anonim_belge_oku(URL, kaynak_http.request_variant("en"))
            request.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Kaynak hafızası istemcisinin dar yanıt, geçerli özet ve anonim kontrol sınırlarını ağsız sınar."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient

from radar.kaynak_hafizasi import KaynakHafizasi, KaynakTanimi
from radar.kaynak_hafizasi_istemci import (COMMUNITY_URL, COOKIE, MAX_RESPONSE_BYTES, PREFIX,
                                         KaynakHafizasiIstemcisi, KaynakHafizasiIstemciHatasi)
from radar.toplayici import FetchResult


SOURCE = KaynakTanimi("pilot", "https://blog.youtube/news-and-events/public-source-pilot/")
HASH = "a" * 64
COOKIE_VALUE = "t" * 43
CSRF = hashlib.sha256(("devtrellis-csrf:" + COOKIE_VALUE).encode()).hexdigest()
COOKIE_HEADER = COOKIE + "=" + COOKIE_VALUE + "; Path=/; Secure; HttpOnly; SameSite=strict"


def source_record():
    return {"id": SOURCE.id, "url": SOURCE.url, "language": SOURCE.language,
            "representation": SOURCE.representation, "extractor_version": SOURCE.extractor_version}


def summary():
    return {"summary": "A stable public page can reuse reviewed findings.", "findings": ["The checked version is unchanged."],
            "scope": "This public source only.", "limitations": "This is not independent claim verification.",
            "sections": ["Public findings"], "model": "unknown", "generated_at": "2026-11-06T10:00:00+00:00",
            "license": "CC-BY-4.0", "version": 1, "source_revision": 1, "content_hash": HASH}


def record(**updates):
    return {"source_id": SOURCE.id, "key": SOURCE.key, "url": SOURCE.url, "language": SOURCE.language,
            "representation": SOURCE.representation, "extractor_version": SOURCE.extractor_version,
            "state": "fresh", "fresh": True, "source_revision": 1, "summary_status": "missing",
            "summary_reusable": False, "summary": None, "claims_verified": False, "model_calls": 0,
            "content_hash": HASH, "title": "Public source comparison and limitations", "text_length": 300,
            "fetched_at": 100000.0, "checked_at": 100000.0, "attempted_at": 100000.0,
            "last_error": None, "method": "content_hash", "checks": 1, "downloaded_bytes": 500, **updates}


def session_response(**updates):
    return httpx.Response(200, json={"authenticated": False, "user": None, "csrf_token": CSRF, **updates},
                          headers={"set-cookie": COOKIE_HEADER})


class KaynakHafizasiIstemciTestleri(unittest.TestCase):
    def client(self, responder):
        calls = []
        def handler(request):
            calls.append(request)
            return responder(request)
        return KaynakHafizasiIstemcisi(transport=httpx.MockTransport(handler)), calls

    def test_constructor_catalog_and_read_do_not_open_session_or_source(self):
        def respond(request):
            if request.url.path == PREFIX:
                return httpx.Response(200, json={"sources": [source_record()], "model_calls": 0})
            return httpx.Response(200, json=record())
        client, calls = self.client(respond)
        self.assertEqual(calls, [])
        self.assertEqual(client.katalog()["sources"], [source_record()])
        self.assertEqual(client.oku("pilot")["content_hash"], HASH)
        self.assertEqual([call.url.path for call in calls], [PREFIX, PREFIX + "/pilot"])
        for call in calls:
            self.assertEqual(str(call.url).split("/api")[0], COMMUNITY_URL)
            self.assertEqual(call.method, "GET")
            self.assertEqual(call.content, b"")
            for header in ("cookie", "authorization", "origin", "x-ufuk-csrf", "x-devtrellis-client", "x-devtrellis-inbox"):
                self.assertNotIn(header, call.headers)

    def test_approved_current_reusable_summary_preserves_bound_version(self):
        value = record(summary_status="current", summary_reusable=True, summary=summary())
        client, _ = self.client(lambda request: httpx.Response(200, json=value))
        self.assertEqual(client.oku("pilot")["summary"], summary())

    def test_old_error_or_checking_summary_is_not_forwarded(self):
        for state in ("stale", "checking", "error"):
            with self.subTest(state=state):
                value = record(state=state, fresh=False, summary_status="current", summary_reusable=False,
                               summary={**summary(), "summary": "private.person@example.com"},
                               last_error="private upstream details" if state == "error" else None)
                client, _ = self.client(lambda request: httpx.Response(200, json=value))
                result = client.oku("pilot")
                self.assertIsNone(result["summary"])
                self.assertFalse(result["summary_reusable"])
                self.assertNotIn("private", json.dumps(result))

    def test_mismatched_or_private_claimed_reusable_summary_is_rejected(self):
        variations = [{"content_hash": "b" * 64}, {"source_revision": 2}, {"version": True},
                      {"summary": "private.person@example.com"}, {"conversation": "private context"},
                      {"model": "sk-" + "".join(("synthetic", "Private", "Value", "9999"))}]
        for change in variations:
            with self.subTest(fields=list(change)):
                value = record(summary_status="current", summary_reusable=True, summary={**summary(), **change})
                client, _ = self.client(lambda request: httpx.Response(200, json=value))
                with self.assertRaises(KaynakHafizasiIstemciHatasi) as failure:
                    client.oku("pilot")
                self.assertNotIn("private", str(failure.exception).lower())
        for change in ({"fresh": False, "state": "stale"}, {"summary_status": "stale"}, {"summary": None}):
            value = {**record(summary_status="current", summary_reusable=True, summary=summary()), **change}
            client, _ = self.client(lambda request: httpx.Response(200, json=value))
            with self.subTest(change=change), self.assertRaises(KaynakHafizasiIstemciHatasi):
                client.oku("pilot")

    def test_strict_read_shape_and_identity_fail_without_reflecting_values(self):
        changes = [{"source_id": "other"}, {"key": "b"*64}, {"url": "https://127.0.0.1/private"},
                   {"claims_verified": True}, {"model_calls": False}, {"model_calls": 1},
                   {"fresh": "true"}, {"source_revision": True}, {"state": []}, {"summary_status": {}},
                   {"method": []}, {"checked_at": True}, {"fetched_at": 10**500}, {"checks": -1},
                   {"downloaded_bytes": False}, {"body": "private upstream body"}, {"summary": []}]
        for change in changes:
            with self.subTest(fields=list(change)):
                client, _ = self.client(lambda request: httpx.Response(200, json=record(**change)))
                with self.assertRaises(KaynakHafizasiIstemciHatasi) as failure:
                    client.oku("pilot")
                self.assertNotIn("private", str(failure.exception).lower())

    def test_check_creates_only_fresh_anonymous_session_then_bound_post(self):
        def respond(request):
            if request.url.path.endswith("/session"):
                self.assertNotIn("cookie", request.headers)
                return session_response()
            return httpx.Response(200, json=record(check_started=True, check_succeeded=True, ownership_lost=False))
        client, calls = self.client(respond)
        result = client.kontrol_et("pilot", 60, True)
        self.assertTrue(result["check_succeeded"])
        self.assertEqual([call.method for call in calls], ["POST", "POST"])
        self.assertEqual(json.loads(calls[0].content), {})
        self.assertEqual(calls[0].headers["origin"], COMMUNITY_URL)
        self.assertNotIn("x-ufuk-csrf", calls[0].headers)
        self.assertEqual(calls[1].url.path, PREFIX + "/pilot/check")
        self.assertEqual(json.loads(calls[1].content), {"max_age_seconds": 60, "freshness_required": True})
        self.assertEqual(calls[1].headers["cookie"], COOKIE + "=" + COOKIE_VALUE)
        self.assertEqual(calls[1].headers["origin"], COMMUNITY_URL)
        self.assertEqual(calls[1].headers["x-ufuk-csrf"], CSRF)
        self.assertNotIn(COOKIE_VALUE, json.dumps(result))
        self.assertNotIn(CSRF, json.dumps(result))
        self.assertEqual(calls[1].extensions["timeout"]["read"], 45)
        for call in calls:
            self.assertNotIn("authorization", call.headers)
            self.assertNotIn("x-devtrellis-client", call.headers)
            self.assertNotIn("x-devtrellis-inbox", call.headers)
        client.kontrol_et("pilot", freshness_required=False)
        self.assertEqual(len(calls), 4)
        self.assertNotIn("cookie", calls[2].headers)
        self.assertFalse(json.loads(calls[3].content)["freshness_required"])

    def test_invalid_ids_and_coerced_check_arguments_do_not_contact_service(self):
        client, calls = self.client(lambda request: self.fail("Unexpected request"))
        for identifier in (None, 1, True, "", "a", "../pilot", "pilot?question=private", "pilot#x", "https://host/path", "pilot\r\nCookie:x"):
            for operation in (client.oku, client.kontrol_et):
                with self.subTest(identifier=identifier), self.assertRaises(KaynakHafizasiIstemciHatasi):
                    operation(identifier)
        for options in ({"max_age_seconds": True}, {"max_age_seconds": "60"}, {"max_age_seconds": 59},
                        {"max_age_seconds": 86401}, {"freshness_required": 1}, {"freshness_required": "false"}):
            with self.subTest(options=options), self.assertRaises(KaynakHafizasiIstemciHatasi):
                client.kontrol_et("pilot", **options)
        self.assertEqual(calls, [])

    def test_session_cookie_and_csrf_are_narrow_and_never_bypassed(self):
        responses = [session_response(authenticated=True, user={"name": "private user"}),
                     session_response(csrf_token="e" * 64)]
        for cookie in (COOKIE_HEADER.replace("Secure; ", ""), COOKIE_HEADER.replace("SameSite=strict", "SameSite=none"),
                       COOKIE_HEADER + "; Domain=devtrellis.org", COOKIE_HEADER.replace("Path=/", "Path=/else"),
                       "other=" + COOKIE_VALUE + "; Secure; HttpOnly; Path=/; SameSite=strict"):
            responses.append(httpx.Response(200, json={"authenticated":False,"user":None,"csrf_token":CSRF}, headers={"set-cookie":cookie}))
        responses.append(httpx.Response(200, json={"authenticated":False,"user":None,"csrf_token":CSRF},
                                        headers=[("set-cookie",COOKIE_HEADER),("set-cookie","other=private")]))
        for response in responses:
            client, calls = self.client(lambda request: response)
            with self.subTest(headers=response.headers.get_list("set-cookie")), self.assertRaises(KaynakHafizasiIstemciHatasi):
                client.kontrol_et("pilot")
            self.assertEqual(len(calls), 1)

    def test_status_redirect_and_transport_failures_never_retry_or_echo(self):
        for status in (301, 302, 307, 401, 403, 404, 429, 500):
            client, calls = self.client(lambda request: httpx.Response(status, text="private upstream details",
                                                                      headers={"location":"https://elsewhere.invalid/private"}))
            with self.subTest(status=status), self.assertRaises(KaynakHafizasiIstemciHatasi) as failure:
                client.oku("pilot")
            self.assertNotIn("private", str(failure.exception))
            self.assertEqual(len(calls),1)
        def fail(request):
            raise httpx.ConnectError("private upstream transport detail")
        client, calls = self.client(fail)
        with self.assertRaises(KaynakHafizasiIstemciHatasi) as failure:
            client.oku("pilot")
        self.assertNotIn("private", str(failure.exception))
        self.assertEqual(len(calls),1)

    def test_content_encoding_size_json_and_unexpected_cookies_are_rejected(self):
        responses = [httpx.Response(200,text="private HTML",headers={"content-type":"text/html"}),
                     httpx.Response(200,content=b"{}",headers={"content-type":"application/json","content-encoding":"br"}),
                     httpx.Response(200,content=b"{}",headers={"content-type":"application/json","content-length":str(MAX_RESPONSE_BYTES+1)}),
                     httpx.Response(200,content=b" "*(MAX_RESPONSE_BYTES+1),headers={"content-type":"application/json"}),
                     httpx.Response(200,content=b'{"private":"one","private":"two"}',headers={"content-type":"application/json"}),
                     httpx.Response(200,content=b'{"private":NaN}',headers={"content-type":"application/json"}),
                     httpx.Response(200,content=b"private invalid json",headers={"content-type":"application/json"}),
                     httpx.Response(200,json=record(),headers={"set-cookie":"private=unexpected"})]
        for response in responses:
            client, calls = self.client(lambda request: response)
            with self.subTest(headers=dict(response.headers)), self.assertRaises(KaynakHafizasiIstemciHatasi) as failure:
                client.oku("pilot")
            self.assertNotIn("private", str(failure.exception))
            self.assertEqual(len(calls),1)

    def test_catalog_limits_private_urls_and_duplicate_identity_are_rejected(self):
        for sources in ([source_record()]*21, [source_record(),source_record()],
                        [{**source_record(),"url":"https://blog.youtube/article?token=private"}],
                        [{**source_record(),"url":"https://unapproved.example.org/article"}],
                        [{**source_record(),"private":"unexpected"}]):
            client, _ = self.client(lambda request: httpx.Response(200,json={"sources":sources,"model_calls":0}))
            with self.subTest(count=len(sources)), self.assertRaises(KaynakHafizasiIstemciHatasi):
                client.katalog()

    def test_client_disables_environment_proxy_and_redirects(self):
        client, _ = self.client(lambda request: httpx.Response(200,json=record()))
        actual = httpx.Client
        with patch("radar.kaynak_hafizasi_istemci.httpx.Client", wraps=actual) as constructor:
            client.oku("pilot")
        self.assertIs(constructor.call_args.kwargs["trust_env"], False)
        self.assertIs(constructor.call_args.kwargs["follow_redirects"], False)

    def test_real_api_contract_with_fake_publisher_preserves_reuse_and_summary(self):
        from radar.topluluk_api import uygulama
        text = ("This public article explains bounded source checks and reusable summaries. " * 6)
        fetch = Mock(return_value=FetchResult(200, ("<article><h1>Public source comparison and limitations</h1><p>" + text + "</p></article>").encode(),
                                             {"content-type":"text/html"},SOURCE.url))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = KaynakHafizasi(root/"memory.sqlite3",[SOURCE],fetcher=fetch)
            app = uygulama(root/"community.sqlite3",public_url=COMMUNITY_URL,sources=[],source_memory=memory)
            with TestClient(app,base_url=COMMUNITY_URL) as api:
                def handler(request):
                    api.cookies.clear()
                    response = api.request(request.method,str(request.url),headers=dict(request.headers),content=request.content)
                    return httpx.Response(response.status_code,headers=list(response.headers.multi_items()),content=response.content)
                client, calls = self.client(handler)
                self.assertEqual(client.oku("pilot")["state"], "missing")
                fetch.assert_not_called()
                first = client.kontrol_et("pilot")
                self.assertTrue(first["check_succeeded"])
                packet = {key:value for key,value in summary().items() if key not in {"version","source_revision","content_hash"}}
                memory.ozet_kaydet("pilot",expected_revision=first["source_revision"],expected_hash=first["content_hash"],
                                  summary=packet,approved_public=True)
                self.assertTrue(client.oku("pilot")["summary_reusable"])
                again = client.kontrol_et("pilot")
                self.assertFalse(again["check_started"])
                self.assertIsNotNone(again["summary"])
                self.assertEqual(fetch.call_count,1)
                self.assertEqual(len(calls),6)


if __name__ == "__main__":
    unittest.main()
